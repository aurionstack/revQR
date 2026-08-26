import uuid
from datetime import timedelta
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models import Business
from app.services.auth import (
    create_access_token,
    get_password_hash,
    verify_password,
    get_current_business_optional,
)
from app.config import settings
from app.main import TEMPLATES_DIR

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

def generate_slug(name: str) -> str:
    """Generate a URL-friendly slug from the business name."""
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    # Add a short unique identifier
    short_id = str(uuid.uuid4())[:6]
    return f"{slug}-{short_id}"

# ── Signup ────────────────────────────────────────────────────────────────────

@router.get("/signup", response_class=HTMLResponse)
async def signup_page(
    request: Request,
    business=Depends(get_current_business_optional)
):
    if business:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "auth/signup.html")

@router.post("/signup", response_class=HTMLResponse)
async def signup(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    google_place_id: str = Form(""),
    phone: str = Form(""),
    db: AsyncSession = Depends(get_db)
):
    # Validation
    if len(password) < 8:
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"error": "Password must be at least 8 characters."},
            status_code=status.HTTP_400_BAD_REQUEST
        )
    
    # Check if email exists
    result = await db.execute(select(Business).filter(Business.email == email))
    if result.scalars().first():
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"error": "Email already registered. Please log in."},
            status_code=status.HTTP_400_BAD_REQUEST
        )
    
    # Create business
    slug = generate_slug(name)
    new_business = Business(
        name=name,
        email=email,
        password_hash=get_password_hash(password),
        slug=slug,
        google_place_id=google_place_id or None,
        phone=phone or None,
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
            {"error": "An error occurred during registration."},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
    # Generate token & set cookie
    access_token_expires = timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)
    access_token = create_access_token(
        data={"sub": str(new_business.id)}, expires_delta=access_token_expires
    )
    
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=settings.JWT_EXPIRATION_MINUTES * 60,
        expires=settings.JWT_EXPIRATION_MINUTES * 60,
        samesite="lax",
    )
    return response

# ── Login ─────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    business=Depends(get_current_business_optional)
):
    if business:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "auth/login.html")

@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Business).filter(Business.email == email))
    business = result.scalars().first()
    
    if not business or not verify_password(password, business.password_hash):
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": "Incorrect email or password."},
            status_code=status.HTTP_401_UNAUTHORIZED
        )
    
    # Generate token & set cookie
    access_token_expires = timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)
    access_token = create_access_token(
        data={"sub": str(business.id)}, expires_delta=access_token_expires
    )
    
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=settings.JWT_EXPIRATION_MINUTES * 60,
        expires=settings.JWT_EXPIRATION_MINUTES * 60,
        samesite="lax",
    )
    return response

# ── Logout ────────────────────────────────────────────────────────────────────

@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token")
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


from app.services.email import send_password_reset_email

@router.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_post(
    request: Request,
    email: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    email_clean = email.strip().lower()
    result = await db.execute(select(Business).filter(Business.email == email_clean))
    business = result.scalars().first()

    if business and business.is_active:
        token = create_password_reset_token(business.email, str(business.id))
        app_url = str(request.base_url).rstrip("/")
        reset_url = f"{app_url}/reset-password?token={token}"
        # Send password reset email directly and privately to their inbox
        await send_password_reset_email(business.email, reset_url, business.name)

    # Always return a safe generic message without exposing the token or whether the user exists
    return templates.TemplateResponse(request, "auth/forgot_password.html", {
        "submitted": True,
        "email": email_clean,
    })



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

    return templates.TemplateResponse(request, "auth/reset_password.html", {
        "token": token,
        "email": payload.get("email")
    })


@router.post("/reset-password", response_class=HTMLResponse)
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

    if len(password) < 8:
        return templates.TemplateResponse(request, "auth/reset_password.html", {
            "token": token,
            "email": payload.get("email"),
            "error": "Password must be at least 8 characters."
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

    business.password_hash = get_password_hash(password.strip())
    db.add(business)
    await db.commit()

    return templates.TemplateResponse(request, "auth/login.html", {
        "success": "✓ Password reset successfully! Please log in with your new password."
    })

