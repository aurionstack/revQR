import uuid
from datetime import timedelta
import re
import hmac
import secrets
from urllib.parse import urlencode

import httpx
import jwt
from starlette.concurrency import run_in_threadpool

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import IntegrityError
from email_validator import EmailNotValidError, validate_email

from app.database import get_db
from app.models import Business
from app.services.auth import (
    create_access_token,
    get_password_hash,
    verify_password,
    verify_password_or_dummy,
    get_current_business_optional,
    create_pre_auth_token,
    verify_pre_auth_token,
    create_email_flow_token,
    verify_email_flow_token,
    issue_email_otp,
    verify_email_otp,
    validate_password_strength,
    set_access_cookie,
    set_flow_cookie,
    request_cookie,
    delete_cookie_variants,
    create_google_oauth_state,
    verify_google_oauth_state,
)
import pyotp
from app.config import settings
from app.main import TEMPLATES_DIR
from app.services.google_reviews import GoogleReviewLinkError, normalize_google_review_link
from app.services.business_context import import_business_context
from app.services.email import send_security_code_email
from app.services.rate_limit import limiter

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["google_oauth_enabled"] = bool(
    settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET
)

GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"

def generate_slug(name: str) -> str:
    """Generate a URL-friendly slug from the business name."""
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    # Add a short unique identifier
    short_id = str(uuid.uuid4())[:6]
    return f"{slug}-{short_id}"


def normalize_email(value: str) -> str | None:
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return None


def safe_next_url(value: str | None) -> str:
    candidate = (value or "").strip()
    return candidate if candidate.startswith("/") and not candidate.startswith("//") else ""


def auth_destination(business: Business, next_url: str = "") -> str:
    if business.is_admin and not business.is_2fa_enabled:
        return "/dashboard/settings/security?required=1"
    return safe_next_url(next_url) or ("/admin" if business.is_admin else "/dashboard")


def authenticated_response(business: Business, destination: str) -> RedirectResponse:
    expiry_minutes = settings.ADMIN_SESSION_MINUTES if business.is_admin else settings.JWT_EXPIRATION_MINUTES
    token = create_access_token(
        data={"sub": str(business.id), "password_version": business.password_version},
        expires_delta=timedelta(minutes=expiry_minutes),
    )
    response = RedirectResponse(destination, status_code=status.HTTP_302_FOUND)
    set_access_cookie(response, token, expiry_minutes * 60)
    return response


async def begin_email_flow(
    business: Business,
    purpose: str,
    db: AsyncSession,
    next_url: str = "",
    force: bool = False,
) -> tuple[RedirectResponse, bool, int]:
    code, retry_after = issue_email_otp(business, purpose, force=force)
    sent = False
    if code:
        db.add(business)
        await db.commit()
        sent = await send_security_code_email(
            business.email,
            code,
            business.name,
            purpose,
        )

    route = "/verify-email" if purpose == "email_verification" else "/recover/verify"
    response = RedirectResponse(route, status_code=status.HTTP_302_FOUND)
    cookie_name = "email_verify_token" if purpose == "email_verification" else "recovery_flow_token"
    set_flow_cookie(
        response,
        cookie_name,
        create_email_flow_token(str(business.id), purpose, safe_next_url(next_url)),
        max_age=15 * 60,
    )
    return response, sent, retry_after


def google_redirect_uri() -> str:
    return f"{settings.APP_URL.rstrip('/')}/auth/google/callback"


def verify_google_id_token(raw_token: str, nonce: str) -> dict:
    """Validate signature and all security-sensitive OIDC claims."""
    signing_key = jwt.PyJWKClient(GOOGLE_JWKS_URL, cache_keys=True, timeout=10).get_signing_key_from_jwt(raw_token)
    claims = jwt.decode(
        raw_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=settings.GOOGLE_OAUTH_CLIENT_ID,
        issuer=["accounts.google.com", "https://accounts.google.com"],
        options={"require": ["exp", "iat", "iss", "aud", "sub", "email", "nonce"]},
    )
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise jwt.InvalidTokenError("OIDC nonce mismatch")
    if claims.get("email_verified") is not True:
        raise jwt.InvalidTokenError("Google email is not verified")
    return claims


async def exchange_google_code(code: str, nonce: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": google_redirect_uri(),
            "grant_type": "authorization_code",
        })
        response.raise_for_status()
        raw_token = response.json().get("id_token")
        if not raw_token:
            raise ValueError("Google did not return an identity token")
    return await run_in_threadpool(verify_google_id_token, raw_token, nonce)


@router.get("/auth/google")
@limiter.limit("20/minute")
async def google_auth_start(request: Request, mode: str = "login", next: str = ""):
    if not settings.GOOGLE_OAUTH_CLIENT_ID or not settings.GOOGLE_OAUTH_CLIENT_SECRET:
        return RedirectResponse(f"/{'signup' if mode == 'signup' else 'login'}?oauth=unavailable", status_code=302)
    nonce = secrets.token_urlsafe(32)
    state_token = create_google_oauth_state(mode, safe_next_url(next), nonce)
    query = urlencode({
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state_token,
        "nonce": nonce,
        "prompt": "select_account",
    })
    response = RedirectResponse(f"{GOOGLE_AUTHORIZATION_URL}?{query}", status_code=302)
    set_flow_cookie(response, "google_oauth_state", state_token, max_age=600)
    return response


@router.get("/auth/google/callback")
@limiter.limit("20/minute")
async def google_auth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: AsyncSession = Depends(get_db),
):
    cookie_state = request_cookie(request, "google_oauth_state")
    state_claims = verify_google_oauth_state(state)
    fallback = "/signup" if state_claims and state_claims.get("mode") == "signup" else "/login"
    if error:
        response = RedirectResponse(f"{fallback}?oauth=cancelled", status_code=302)
        delete_cookie_variants(response, "google_oauth_state")
        return response
    if not code or not state_claims or not cookie_state or not hmac.compare_digest(state, cookie_state):
        response = RedirectResponse(f"{fallback}?oauth=invalid", status_code=302)
        delete_cookie_variants(response, "google_oauth_state")
        return response

    try:
        claims = await exchange_google_code(code, state_claims["nonce"])
        email = normalize_email(str(claims["email"]))
        subject = str(claims["sub"])
        if not email or not subject:
            raise ValueError("Incomplete Google identity")

        result = await db.execute(
            select(Business).where((Business.google_subject == subject) | (Business.email == email))
        )
        matches = result.scalars().all()
        by_subject = next((item for item in matches if item.google_subject == subject), None)
        by_email = next((item for item in matches if item.email == email), None)
        if by_subject and by_email and by_subject.id != by_email.id:
            raise ValueError("Google identity conflicts with an existing account")
        business = by_subject or by_email

        if business:
            if not business.is_active:
                raise ValueError("Account is inactive")
            if business.google_subject and business.google_subject != subject:
                raise ValueError("Email is already linked to another Google identity")
            business.google_subject = subject
            business.email_verified = True
        else:
            display_name = str(claims.get("name") or email.split("@", 1)[0]).strip()[:255]
            business = Business(
                name=display_name or "My Business",
                slug=generate_slug(display_name or "business"),
                email=email,
                password_hash=get_password_hash(secrets.token_urlsafe(48)),
                google_subject=subject,
                email_verified=True,
                is_admin=False,
                has_paid=False,
            )
        db.add(business)
        await db.commit()
        await db.refresh(business)
    except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError, IntegrityError):
        await db.rollback()
        response = RedirectResponse(f"{fallback}?oauth=failed", status_code=302)
        delete_cookie_variants(response, "google_oauth_state")
        return response

    next_url = safe_next_url(state_claims.get("next"))
    if business.is_2fa_enabled:
        response = RedirectResponse("/login/2fa", status_code=302)
        set_flow_cookie(response, "pre_auth_token", create_pre_auth_token(str(business.id), next_url), max_age=600)
    else:
        response = authenticated_response(business, auth_destination(business, next_url))
    delete_cookie_variants(response, "google_oauth_state")
    return response

# ── Signup ────────────────────────────────────────────────────────────────────

@router.get("/signup", response_class=HTMLResponse)
async def signup_page(
    request: Request,
    business=Depends(get_current_business_optional)
):
    if business:
        if business.is_admin:
            return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "auth/signup.html")

@router.post("/signup", response_class=HTMLResponse)
@limiter.limit("5/hour")
async def signup(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    google_place_id: str = Form(""),
    phone: str = Form(""),
    business_description: str = Form(""),
    db: AsyncSession = Depends(get_db)
):
    form_data = {
        "name": name,
        "email": email,
        "google_place_id": google_place_id,
        "phone": phone,
        "business_description": business_description,
    }

    name = name.strip()
    if len(name) < 2 or len(name) > 255:
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"error": "Business name must contain 2 to 255 characters.", "form_data": form_data},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    password_error = validate_password_strength(password)
    if password_error:
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"error": password_error, "form_data": form_data},
            status_code=status.HTTP_400_BAD_REQUEST
        )
    
    # Check if email exists
    email_clean = normalize_email(email)
    if not email_clean:
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"error": "Enter a valid email address.", "form_data": form_data},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    result = await db.execute(select(Business).filter(Business.email == email_clean))
    if result.scalars().first():
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"error": "Email already registered. Please log in.", "form_data": form_data},
            status_code=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        review_link = normalize_google_review_link(google_place_id)
    except GoogleReviewLinkError as exc:
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"error": str(exc), "form_data": form_data},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Create business
    slug = generate_slug(name)
    new_business = Business(
        name=name,
        email=email_clean,
        password_hash=get_password_hash(password),
        slug=slug,
        # Keep the existing column name for database compatibility. It now stores
        # the direct Google review link (legacy Place IDs are still supported).
        google_place_id=review_link,
        scraped_context=await import_business_context(review_link, name),
        custom_prompt=business_description.strip()[:2000] or None,
        phone=phone.strip() or None,
        is_admin=False,
        has_paid=False,
        email_verified=False,
    )
    
    db.add(new_business)
    try:
        await db.commit()
        await db.refresh(new_business)
    except IntegrityError:
        await db.rollback()
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"error": "An error occurred during registration.", "form_data": form_data},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
    response, sent, _ = await begin_email_flow(
        new_business,
        "email_verification",
        db,
        force=True,
    )
    if not sent:
        response.headers["Location"] = "/verify-email?delivery=failed"
    return response

# ── Login ─────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    business=Depends(get_current_business_optional)
):
    if business:
        if business.is_admin:
            return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "auth/login.html")

@router.post("/login", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: AsyncSession = Depends(get_db)
):
    email_clean = normalize_email(email) or email.strip().lower()
    result = await db.execute(select(Business).filter(Business.email == email_clean))
    business = result.scalars().first()
    password_valid, upgraded_hash = verify_password_or_dummy(
        password,
        business.password_hash if business else None,
    )

    if not business or not business.is_active or not password_valid:
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": "Incorrect email or password."},
            status_code=status.HTTP_401_UNAUTHORIZED
        )

    if upgraded_hash:
        business.password_hash = upgraded_hash
        db.add(business)
        await db.commit()
    
    next_url = safe_next_url(next or request.query_params.get("next"))

    if not business.email_verified:
        response, sent, _ = await begin_email_flow(
            business,
            "email_verification",
            db,
            next_url=next_url,
        )
        if not sent and not business.email_otp_hash:
            response.headers["Location"] = "/verify-email?delivery=failed"
        return response

    if business.is_2fa_enabled:
        # Generate pre-auth token and redirect to 2FA page
        pre_auth_token = create_pre_auth_token(str(business.id), next_url)
        response = RedirectResponse(url="/login/2fa", status_code=status.HTTP_302_FOUND)
        set_flow_cookie(response, "pre_auth_token", pre_auth_token, max_age=600)
        return response
    
    return authenticated_response(business, auth_destination(business, next_url))

# ── 2FA ───────────────────────────────────────────────────────────────────────

def masked_email(value: str) -> str:
    local, _, domain = value.partition("@")
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}{'*' * max(2, len(local) - len(visible))}@{domain}"


async def email_flow_business(
    request: Request,
    purpose: str,
    db: AsyncSession,
) -> tuple[dict, Business] | None:
    cookie_name = "email_verify_token" if purpose == "email_verification" else "recovery_flow_token"
    payload = verify_email_flow_token(request_cookie(request, cookie_name), purpose)
    if not payload:
        return None
    try:
        business_id = uuid.UUID(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    result = await db.execute(select(Business).filter(Business.id == business_id))
    business = result.scalar_one_or_none()
    return (payload, business) if business and business.is_active else None


@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email_page(
    request: Request,
    delivery: str = "",
    wait: int = 0,
    db: AsyncSession = Depends(get_db),
):
    flow = await email_flow_business(request, "email_verification", db)
    if not flow:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    _, business = flow
    if business.email_verified:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "auth/email_otp.html", {
        "mode": "verify",
        "masked_email": masked_email(business.email),
        "delivery_error": delivery == "failed",
        "retry_after": max(0, wait),
    })


@router.post("/verify-email", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def verify_email_post(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    flow = await email_flow_business(request, "email_verification", db)
    if not flow:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    payload, business = flow
    verified, error = verify_email_otp(business, "email_verification", code.strip())
    if not verified:
        db.add(business)
        await db.commit()
        return templates.TemplateResponse(request, "auth/email_otp.html", {
            "mode": "verify",
            "masked_email": masked_email(business.email),
            "error": error,
        }, status_code=status.HTTP_400_BAD_REQUEST)

    business.email_verified = True
    db.add(business)
    await db.commit()
    destination = auth_destination(business, payload.get("next", ""))
    response = authenticated_response(business, destination)
    delete_cookie_variants(response, "email_verify_token")
    return response


@router.post("/verify-email/resend")
@limiter.limit("3/5minutes")
async def verify_email_resend(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    flow = await email_flow_business(request, "email_verification", db)
    if not flow:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    payload, business = flow
    response, sent, retry_after = await begin_email_flow(
        business,
        "email_verification",
        db,
        next_url=payload.get("next", ""),
    )
    if retry_after:
        response.headers["Location"] = f"/verify-email?wait={retry_after}"
    elif not sent:
        response.headers["Location"] = "/verify-email?delivery=failed"
    return response

@router.get("/login/2fa", response_class=HTMLResponse)
async def login_2fa_page(request: Request):
    pre_auth_token = request_cookie(request, "pre_auth_token")
    if not pre_auth_token or not verify_pre_auth_token(pre_auth_token):
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "auth/login_2fa.html")

@router.post("/login/2fa", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def login_2fa(
    request: Request,
    totp_code: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    pre_auth_token = request_cookie(request, "pre_auth_token")
    payload = verify_pre_auth_token(pre_auth_token) if pre_auth_token else None
    business_id_str = payload.get("sub") if payload else None
    
    if not business_id_str:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
        
    result = await db.execute(select(Business).filter(Business.id == uuid.UUID(business_id_str)))
    business = result.scalars().first()
    
    if not business or not business.is_active or not business.email_verified or not business.totp_secret:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
        
    # Verify TOTP code
    totp = pyotp.TOTP(business.totp_secret)
    if not totp.verify(totp_code.strip(), valid_window=1):
        return templates.TemplateResponse(
            request,
            "auth/login_2fa.html",
            {"error": "Invalid 2FA code. Please try again."},
            status_code=status.HTTP_400_BAD_REQUEST
        )
        
    redirect_target = auth_destination(business, payload.get("next", ""))
    response = authenticated_response(business, redirect_target)
    # Clean up pre-auth cookie
    delete_cookie_variants(response, "pre_auth_token")
    return response

# ── Logout ────────────────────────────────────────────────────────────────────

@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    for cookie_name in ("access_token", "pre_auth_token", "email_verify_token", "recovery_flow_token"):
        delete_cookie_variants(response, cookie_name)
    response.headers["Clear-Site-Data"] = '"cache", "cookies", "storage"'
    return response


# ── Password Reset (Forgot Password) ─────────────────────────────────────────

from app.services.auth import create_password_reset_token, verify_password_reset_token

@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(
    request: Request,
    business=Depends(get_current_business_optional)
):
    if business:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "auth/forgot_password.html")


@router.post("/forgot-password", response_class=HTMLResponse)
@limiter.limit("5/hour")
async def forgot_password_post(
    request: Request,
    email: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    email_clean = normalize_email(email) or email.strip().lower()
    result = await db.execute(select(Business).filter(Business.email == email_clean))
    business = result.scalars().first()

    if business and business.is_active:
        response, sent, _ = await begin_email_flow(
            business,
            "password_reset",
            db,
            force=True,
        )
        if not sent:
            response.headers["Location"] = "/recover/verify?delivery=failed"
        return response

    # The same destination is shown for unknown accounts to avoid immediate
    # account discovery through the recovery endpoint.
    return RedirectResponse("/recover/verify", status_code=status.HTTP_302_FOUND)


@router.get("/recover/verify", response_class=HTMLResponse)
async def recovery_code_page(
    request: Request,
    delivery: str = "",
    wait: int = 0,
    db: AsyncSession = Depends(get_db),
):
    flow = await email_flow_business(request, "password_reset", db)
    masked = masked_email(flow[1].email) if flow else "your account email"
    return templates.TemplateResponse(request, "auth/email_otp.html", {
        "mode": "recovery",
        "masked_email": masked,
        "delivery_error": delivery == "failed",
        "retry_after": max(0, wait),
    })


@router.post("/recover/verify", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def recovery_code_post(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    flow = await email_flow_business(request, "password_reset", db)
    if not flow:
        return templates.TemplateResponse(request, "auth/email_otp.html", {
            "mode": "recovery",
            "masked_email": "your account email",
            "error": "The code is invalid or the recovery session expired. Start again.",
        }, status_code=status.HTTP_400_BAD_REQUEST)

    _, business = flow
    verified, error = verify_email_otp(business, "password_reset", code.strip())
    db.add(business)
    await db.commit()
    if not verified:
        return templates.TemplateResponse(request, "auth/email_otp.html", {
            "mode": "recovery",
            "masked_email": masked_email(business.email),
            "error": error,
        }, status_code=status.HTTP_400_BAD_REQUEST)

    token = create_password_reset_token(
        business.email,
        str(business.id),
        business.password_version,
    )
    response = RedirectResponse(
        f"/reset-password?token={token}",
        status_code=status.HTTP_302_FOUND,
    )
    delete_cookie_variants(response, "recovery_flow_token")
    return response


@router.post("/recover/resend")
@limiter.limit("3/5minutes")
async def recovery_code_resend(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    flow = await email_flow_business(request, "password_reset", db)
    if not flow:
        return RedirectResponse("/forgot-password", status_code=status.HTTP_302_FOUND)
    _, business = flow
    response, sent, retry_after = await begin_email_flow(
        business,
        "password_reset",
        db,
    )
    if retry_after:
        response.headers["Location"] = f"/recover/verify?wait={retry_after}"
    elif not sent:
        response.headers["Location"] = "/recover/verify?delivery=failed"
    return response



@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(
    request: Request,
    token: str = "",
    db: AsyncSession = Depends(get_db)
):
    payload = verify_password_reset_token(token)
    if not payload:
        return templates.TemplateResponse(request, "auth/reset_password.html", {
            "invalid_token": True
        })

    try:
        business_id = uuid.UUID(payload.get("sub", ""))
    except (TypeError, ValueError):
        business_id = None
    result = await db.execute(select(Business).filter(Business.id == business_id)) if business_id else None
    business = result.scalar_one_or_none() if result else None
    if not business or business.password_version != payload.get("password_version", 0):
        return templates.TemplateResponse(request, "auth/reset_password.html", {
            "invalid_token": True
        })

    return templates.TemplateResponse(request, "auth/reset_password.html", {
        "token": token,
        "email": business.email,
    })


@router.post("/reset-password", response_class=HTMLResponse)
@limiter.limit("10/hour")
async def reset_password_post(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    payload = verify_password_reset_token(token)
    if not payload:
        return templates.TemplateResponse(request, "auth/reset_password.html", {
            "invalid_token": True
        })

    password_error = validate_password_strength(password)
    if password_error:
        return templates.TemplateResponse(request, "auth/reset_password.html", {
            "token": token,
            "email": payload.get("email"),
            "error": password_error,
        })

    if password != confirm_password:
        return templates.TemplateResponse(request, "auth/reset_password.html", {
            "token": token,
            "email": payload.get("email"),
            "error": "Passwords do not match."
        })

    # Update business password in DB
    business_id = uuid.UUID(payload.get("sub"))
    res = await db.execute(select(Business).filter(Business.id == business_id))
    business = res.scalar_one_or_none()

    if not business:
        return templates.TemplateResponse(request, "auth/reset_password.html", {
            "invalid_token": True
        })

    if business.password_version != payload.get("password_version", 0):
        return templates.TemplateResponse(request, "auth/reset_password.html", {
            "invalid_token": True
        })

    business.password_hash = get_password_hash(password)
    business.password_version += 1
    db.add(business)
    await db.commit()

    return templates.TemplateResponse(request, "auth/login.html", {
        "success": "✓ Password reset successfully! Please log in with your new password."
    })
