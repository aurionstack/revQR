from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
import uuid
from typing import Optional

from fastapi import Depends, HTTPException, Request, Response, status
import jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.config import settings
from app.database import get_db
from app.models import Business
from app.schemas import TokenData

# ── Password Hashing ──────────────────────────────────────────────────────────

pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")
TOKEN_ALGORITHM = "HS256"
TOKEN_ISSUER = "revqr"
TOKEN_AUDIENCE = "revqr-web"
TOKEN_REQUIRED_CLAIMS = ["exp", "iat", "nbf", "iss", "aud", "sub", "type", "jti"]
_DUMMY_PASSWORD_HASH = pwd_context.hash("TimingOnly-Not-A-Real-Account-9c9f7d")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def verify_and_update_password(plain_password: str, hashed_password: str) -> tuple[bool, str | None]:
    """Verify a password and transparently upgrade legacy bcrypt hashes."""
    return pwd_context.verify_and_update(plain_password, hashed_password)


def verify_password_or_dummy(plain_password: str, hashed_password: str | None) -> tuple[bool, str | None]:
    """Use equal-cost verification for unknown accounts to reduce timing leaks."""
    return verify_and_update_password(plain_password, hashed_password or _DUMMY_PASSWORD_HASH)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def validate_password_strength(password: str) -> str | None:
    if len(password) < 10:
        return "Password must be at least 10 characters."
    if len(password) > 128:
        return "Password must be 128 characters or fewer."
    if not any(char.islower() for char in password):
        return "Password must include a lowercase letter."
    if not any(char.isupper() for char in password):
        return "Password must include an uppercase letter."
    if not any(char.isdigit() for char in password):
        return "Password must include a number."
    return None


# ── JWT Tokens ────────────────────────────────────────────────────────────────

def _token_claims(subject: str, token_type: str, expires_delta: timedelta) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "sub": str(subject),
        "type": token_type,
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + expires_delta,
        "jti": uuid.uuid4().hex,
    }


def _decode_token(token: str, expected_type: str) -> dict:
    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[TOKEN_ALGORITHM],
        audience=TOKEN_AUDIENCE,
        issuer=TOKEN_ISSUER,
        options={"require": TOKEN_REQUIRED_CLAIMS, "strict_aud": True},
    )
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("Unexpected token type")
    return payload

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    subject = str(data.get("sub") or "")
    if not subject:
        raise ValueError("Access tokens require a subject")
    lifetime = expires_delta or timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)
    to_encode = {**data, **_token_claims(subject, "access", lifetime)}
    encoded_jwt = jwt.encode(
        to_encode, settings.JWT_SECRET_KEY, algorithm=TOKEN_ALGORITHM
    )
    return encoded_jwt

def create_password_reset_token(email: str, business_id: str, password_version: int = 0) -> str:
    """Create a 30-minute signed token specifically for password reset."""
    payload = {
        **_token_claims(str(business_id), "password_reset", timedelta(minutes=30)),
        "email": email.lower().strip(),
        "password_version": password_version,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=TOKEN_ALGORITHM)

def verify_password_reset_token(token: str) -> Optional[dict]:
    """Verify password reset token and return payload if valid and unexpired."""
    try:
        payload = _decode_token(token, "password_reset")
        if not payload.get("sub") or not payload.get("email"):
            return None
        return payload
    except jwt.PyJWTError:
        return None

def create_pre_auth_token(business_id: str, next_url: str = "") -> str:
    """Create a short-lived token for the 2FA verification step."""
    payload = {
        **_token_claims(str(business_id), "pre_auth", timedelta(minutes=10)),
        "next": next_url if next_url.startswith("/") and not next_url.startswith("//") else "",
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=TOKEN_ALGORITHM)

def verify_pre_auth_token(token: str) -> Optional[dict]:
    """Verify pre-auth token and return its payload if valid."""
    try:
        payload = _decode_token(token, "pre_auth")
        return payload if payload.get("sub") else None
    except jwt.PyJWTError:
        return None


def create_email_flow_token(business_id: str, purpose: str, next_url: str = "") -> str:
    payload = {
        **_token_claims(str(business_id), "email_flow", timedelta(minutes=15)),
        "purpose": purpose,
        "next": next_url if next_url.startswith("/") and not next_url.startswith("//") else "",
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=TOKEN_ALGORITHM)


def verify_email_flow_token(token: str | None, purpose: str) -> Optional[dict]:
    if not token:
        return None
    try:
        payload = _decode_token(token, "email_flow")
        if payload.get("purpose") != purpose:
            return None
        return payload if payload.get("sub") else None
    except jwt.PyJWTError:
        return None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _otp_digest(business_id: str, purpose: str, code: str) -> str:
    message = f"{business_id}:{purpose}:{code}".encode("utf-8")
    return hmac.new(settings.JWT_SECRET_KEY.encode("utf-8"), message, hashlib.sha256).hexdigest()


def issue_email_otp(business: Business, purpose: str, force: bool = False) -> tuple[str | None, int]:
    """Create a six-digit one-time code, respecting the resend cooldown."""
    now = datetime.now(timezone.utc)
    last_sent = _as_utc(business.email_otp_last_sent_at)
    if not force and last_sent:
        elapsed = int((now - last_sent).total_seconds())
        if elapsed < settings.EMAIL_OTP_RESEND_SECONDS:
            return None, settings.EMAIL_OTP_RESEND_SECONDS - elapsed

    code = f"{secrets.randbelow(1_000_000):06d}"
    business.email_otp_hash = _otp_digest(str(business.id), purpose, code)
    business.email_otp_purpose = purpose
    business.email_otp_expires_at = now + timedelta(minutes=settings.EMAIL_OTP_EXPIRY_MINUTES)
    business.email_otp_last_sent_at = now
    business.email_otp_attempts = 0
    return code, 0


def verify_email_otp(business: Business, purpose: str, code: str) -> tuple[bool, str]:
    now = datetime.now(timezone.utc)
    expires_at = _as_utc(business.email_otp_expires_at)
    if (
        not business.email_otp_hash
        or business.email_otp_purpose != purpose
        or not expires_at
        or expires_at <= now
    ):
        return False, "This code has expired. Request a new code."

    if business.email_otp_attempts >= 5:
        return False, "Too many attempts. Request a new code."

    business.email_otp_attempts += 1
    expected = _otp_digest(str(business.id), purpose, code.strip())
    if not hmac.compare_digest(expected, business.email_otp_hash):
        remaining = max(0, 5 - business.email_otp_attempts)
        return False, f"Incorrect code. {remaining} attempt{'s' if remaining != 1 else ''} remaining."

    clear_email_otp(business)
    return True, ""


def clear_email_otp(business: Business) -> None:
    business.email_otp_hash = None
    business.email_otp_purpose = None
    business.email_otp_expires_at = None
    business.email_otp_attempts = 0


def cookie_name(name: str) -> str:
    """Use host-only cookie names in HTTPS deployments without breaking local development."""
    if not settings.cookie_secure:
        return name
    aliases = {"access_token": "session", "pre_auth_token": "pre-auth"}
    suffix = aliases.get(name, name.replace("_", "-"))
    return f"__Host-revqr-{suffix}"


def request_cookie(request: Request, name: str) -> str | None:
    return request.cookies.get(cookie_name(name)) or request.cookies.get(name)


def delete_cookie_variants(response: Response, name: str) -> None:
    response.delete_cookie(name, path="/")
    secured_name = cookie_name(name)
    if secured_name != name:
        response.delete_cookie(secured_name, path="/", secure=True, httponly=True, samesite="lax")


def set_access_cookie(response: Response, token: str, max_age_seconds: int | None = None) -> None:
    max_age = max_age_seconds or settings.JWT_EXPIRATION_MINUTES * 60
    response.set_cookie(
        key=cookie_name("access_token"),
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        max_age=max_age,
        expires=max_age,
        samesite="lax",
        path="/",
    )


def set_flow_cookie(response: Response, name: str, token: str, max_age: int = 900) -> None:
    response.set_cookie(
        key=cookie_name(name),
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        max_age=max_age,
        expires=max_age,
        samesite="lax",
        path="/",
    )




# ── Dependencies ──────────────────────────────────────────────────────────────

async def get_current_business(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Business:
    """
    Dependency to get the current business from the JWT token in the HttpOnly cookie.
    Raises HTTPException 401 if not authenticated.
    """
    token = request_cookie(request, "access_token")
    if not token:
        # Check authorization header as fallback (for testing)
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = _decode_token(token, "access")
        business_id_str = payload.get("sub")
        if business_id_str is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Fetch business from database
    try:
        business_id = uuid.UUID(business_id_str)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")
    result = await db.execute(select(Business).filter(Business.id == business_id))
    business = result.scalars().first()

    if business is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if int(payload.get("password_version", 0)) != business.password_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please log in again.",
        )

    if not business.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user",
        )

    return business

async def get_current_business_optional(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Optional[Business]:
    """
    Dependency to get the current business if authenticated, else returns None.
    Does NOT raise an exception.
    """
    try:
        return await get_current_business(request, db)
    except HTTPException:
        return None

async def get_current_admin(
    request: Request,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
) -> Business:
    """
    Dependency to ensure the current authenticated user has admin privileges.
    Raises HTTPException 403 if not admin.
    """
    if not business.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Administrator privileges required.",
        )
    if not business.email_verified or not business.is_2fa_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin requires verified email and two-factor authentication.",
        )
    if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
        from app.services.operations import audit
        await audit(db, business.id, "admin.action_attempt", request.url.path)
        await db.commit()
    return business
