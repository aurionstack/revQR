from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.config import settings
from app.database import get_db
from app.models import Business
from app.schemas import TokenData

# ── Password Hashing ──────────────────────────────────────────────────────────

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


# ── JWT Tokens ────────────────────────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )
    return encoded_jwt

def create_password_reset_token(email: str, business_id: str) -> str:
    """Create a 30-minute signed token specifically for password reset."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=30)
    payload = {
        "sub": str(business_id),
        "email": email.lower().strip(),
        "type": "password_reset",
        "exp": expire
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

def verify_password_reset_token(token: str) -> Optional[dict]:
    """Verify password reset token and return payload if valid and unexpired."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != "password_reset":
            return None
        if not payload.get("sub") or not payload.get("email"):
            return None
        return payload
    except JWTError:
        return None



# ── Dependencies ──────────────────────────────────────────────────────────────

async def get_current_business(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Business:
    """
    Dependency to get the current business from the JWT token in the HttpOnly cookie.
    Raises HTTPException 401 if not authenticated.
    """
    token = request.cookies.get("access_token")
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
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        business_id_str = payload.get("sub")
        if business_id_str is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Fetch business from database
    result = await db.execute(select(Business).filter(Business.id == business_id_str))
    business = result.scalars().first()

    if business is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
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
    business: Business = Depends(get_current_business)
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
    return business

