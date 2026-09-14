import uuid
import io
from datetime import datetime, time, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
import pyotp
import qrcode
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, desc

from app.database import get_db
from app.models import Business, BusinessAsset, Scan, Review, Feedback, Payment
from app.services.auth import get_current_business, verify_password
from app.services.assets import LogoValidationError, normalize_logo
from app.services.plans import public_plans
from app.config import settings
from app.main import TEMPLATES_DIR
from app.services.google_reviews import (
    GoogleReviewLinkError,
    google_business_reviews_destination,
    normalize_google_review_link,
)
from app.services.rate_limit import limiter
from app.services.time import app_timezone, as_local_datetime, format_local_datetime, local_now

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["local_time"] = format_local_datetime

@router.get("", response_class=HTMLResponse)
async def dashboard_home(
    request: Request,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db)
):
    # 1. Fetch Stats
    # Total Scans
    scans_res = await db.execute(select(func.count(Scan.id)).filter(Scan.business_id == business.id))
    total_scans = scans_res.scalar() or 0

    # Total Reviews
    reviews_res = await db.execute(select(func.count(Review.id)).filter(Review.business_id == business.id))
    total_reviews = reviews_res.scalar() or 0

    # Total Copied Reviews
    copied_res = await db.execute(select(func.count(Review.id)).filter(Review.business_id == business.id, Review.copied == True))
    total_copied = copied_res.scalar() or 0

    # Average Rating
    avg_rating_res = await db.execute(select(func.avg(Review.rating)).filter(Review.business_id == business.id))
    average_rating = avg_rating_res.scalar()

    # Total Feedback (Private Notes)
    feedback_res = await db.execute(select(func.count(Feedback.id)).filter(Feedback.business_id == business.id))
    total_feedback = feedback_res.scalar() or 0

    conversion_rate = (total_copied / total_scans * 100) if total_scans > 0 else 0

    stats = {
        "total_scans": total_scans,
        "total_reviews": total_reviews,
        "total_copied": total_copied,
        "conversion_rate": conversion_rate,
        "average_rating": average_rating,
        "total_feedback": total_feedback,
    }

    # 2. Chart Data (Last 7 Days Scans)
    # Simple Python generation since doing group_by date in SQLite/PG varies
    chart_data = []
    chart_max = 0
    today = local_now().date()
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        # Count scans on this day
        # For cross-DB compatibility, we can just fetch last 7 days and group in python
        chart_data.append({"label": day.strftime("%a"), "count": 0, "date": day})

    # Fetch last 7 days scans
    seven_days_ago = datetime.combine(
        today - timedelta(days=6),
        time.min,
        tzinfo=app_timezone(),
    ).astimezone(timezone.utc)
    recent_scans_res = await db.execute(
        select(Scan.scanned_at).filter(Scan.business_id == business.id, Scan.scanned_at >= seven_days_ago)
    )
    recent_scans = recent_scans_res.scalars().all()
    
    for scan_dt in recent_scans:
        scan_date = as_local_datetime(scan_dt).date()
        for cd in chart_data:
            if cd["date"] == scan_date:
                cd["count"] += 1
                if cd["count"] > chart_max:
                    chart_max = cd["count"]
                break

    # 3. Recent Activity (Limit 5 Reviews)
    recent_reviews_res = await db.execute(
        select(Review).filter(Review.business_id == business.id).order_by(desc(Review.created_at)).limit(5)
    )
    recent_reviews = recent_reviews_res.scalars().all()

    # 4. Source Attribution — top sources
    source_res = await db.execute(
        select(Scan.source, func.count(Scan.id).label("cnt"))
        .filter(Scan.business_id == business.id, Scan.source.isnot(None), Scan.source != "")
        .group_by(Scan.source)
        .order_by(desc("cnt"))
        .limit(5)
    )
    top_sources = source_res.all()

    return templates.TemplateResponse(request, "dashboard/home.html", {
        "business": business,
        "stats": stats,
        "chart_data": chart_data,
        "chart_max": chart_max,
        "recent_reviews": recent_reviews,
        "top_sources": top_sources,
    })

@router.get("/reviews", response_class=HTMLResponse)
async def dashboard_reviews(
    request: Request,
    tab: str = "",
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db)
):
    reviews_res = await db.execute(select(func.count(Review.id)).filter(Review.business_id == business.id))
    total_reviews = reviews_res.scalar() or 0

    feedback_res = await db.execute(select(func.count(Feedback.id)).filter(Feedback.business_id == business.id))
    total_feedback = feedback_res.scalar() or 0

    reviews = []
    feedback_items = []

    if tab == "feedback":
        fb_query = await db.execute(
            select(Feedback).filter(Feedback.business_id == business.id).order_by(desc(Feedback.created_at))
        )
        feedback_items = fb_query.scalars().all()
    else:
        rev_query = await db.execute(
            select(Review).filter(Review.business_id == business.id).order_by(desc(Review.created_at))
        )
        reviews = rev_query.scalars().all()

    return templates.TemplateResponse(request, "dashboard/reviews.html", {
        "business": business,
        "tab": tab,
        "total_reviews": total_reviews,
        "total_feedback": total_feedback,
        "reviews": reviews,
        "feedback_items": feedback_items,
    })


@router.get("/reviews/google-profile")
async def dashboard_google_reviews(
    business: Business = Depends(get_current_business),
):
    """Open the review section for the Google profile connected to this account."""
    destination = await google_business_reviews_destination(
        business.google_place_id,
        business.name,
    )
    return RedirectResponse(destination, status_code=status.HTTP_302_FOUND)

# ── AI Review Reply (HTMX endpoint) ──────────────────────────────────────────

from app.services.ai import generate_review_reply

@router.post("/reviews/ai-reply", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def dashboard_ai_reply(
    request: Request,
    review_id: str = Form(...),
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db)
):
    """Generate AI reply options for a review and return partial HTML."""
    res = await db.execute(select(Review).filter(Review.id == uuid.UUID(review_id), Review.business_id == business.id))
    review = res.scalar_one_or_none()
    if not review:
        return HTMLResponse("<p class='hint'>Review not found.</p>", status_code=404)

    replies = await generate_review_reply(
        rating=review.rating,
        review_text=review.generated_text,
        business_name=business.name
    )

    return templates.TemplateResponse(request, "dashboard/partials/ai_reply.html", {
        "review_id": review_id,
        "replies": replies,
        "rating": review.rating,
    })

# ── WhatsApp Review Request Generator ────────────────────────────────────────

@router.get("/whatsapp", response_class=HTMLResponse)
async def dashboard_whatsapp(
    request: Request,
    business: Business = Depends(get_current_business)
):
    app_url = str(request.base_url).rstrip("/")
    review_link = f"{app_url}/review/{business.slug}"

    # Pre-built WhatsApp templates with Unicode emojis
    wa_templates = [
        {
            "name": "After Visit — Friendly",
            "message": f"Hi {{{{name}}}}! \U0001F60A Thank you for visiting {business.name} today. Could you take 15 seconds to leave us a quick review? It means the world to our small team!\n\n\U0001F449 {review_link}\n\nThank you! \U0001F64F",
        },
        {
            "name": "Post-Service — Professional",
            "message": f"Dear {{{{name}}}},\n\nThank you for choosing {business.name}. We hope you had a great experience!\n\nIf you have a moment, we'd love your honest feedback:\n{review_link}\n\nYour review helps other customers find us. Thank you!",
        },
        {
            "name": "Follow-Up — Casual",
            "message": f"Hey {{{{name}}}}! \U0001F44B Hope you enjoyed your visit to {business.name}!\n\nWe'd really appreciate a quick Google review — takes just 30 seconds:\n{review_link}\n\nThanks a lot! \u2B50",
        },
        {
            "name": "Staff Incentive",
            "message": f"Hi {{{{name}}}}! {business.name} here. {{{{staff_name}}}} served you today and we hope everything was perfect! \U0001F604\n\nWould you mind leaving us a quick review?\n{review_link}\n\nIt helps our team a lot. Thank you! \U0001F31F",
        },
    ]


    return templates.TemplateResponse(request, "dashboard/whatsapp.html", {
        "business": business,
        "review_link": review_link,
        "wa_templates": wa_templates,
    })


# ── Standee / Table Tent Kit ─────────────────────────────────────────────────

@router.get("/standee", response_class=HTMLResponse)
async def dashboard_standee(
    request: Request,
    business: Business = Depends(get_current_business)
):
    app_url = str(request.base_url).rstrip("/")
    review_link = f"{app_url}/review/{business.slug}"

    return templates.TemplateResponse(request, "dashboard/standee.html", {
        "business": business,
        "app_url": app_url,
        "review_link": review_link,
    })


@router.get("/qr", response_class=HTMLResponse)
async def dashboard_qr(
    request: Request,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db),
):
    app_url = str(request.base_url).rstrip("/")
    orders_result = await db.execute(
        select(Payment)
        .where(
            Payment.business_id == business.id,
            Payment.purpose == "physical_stand",
            Payment.status == "paid",
        )
        .order_by(desc(Payment.created_at))
        .limit(5)
    )
    
    return templates.TemplateResponse(request, "dashboard/qr.html", {
        "business": business,
        "app_url": app_url,
        "plans": public_plans(),
        "stand_price": settings.PHYSICAL_STAND_PRICE_PAISE // 100,
        "stand_orders": orders_result.scalars().all(),
        "payment_success": request.query_params.get("payment") == "success",
        "razorpay_key": settings.RAZORPAY_KEY_ID,
    })

@router.get("/settings", response_class=HTMLResponse)
async def dashboard_settings(
    request: Request,
    business: Business = Depends(get_current_business)
):
    return templates.TemplateResponse(request, "dashboard/settings.html", {
        "business": business,
    })

# ── Security Settings (2FA) ──────────────────────────────────────────────────

@router.get("/settings/security", response_class=HTMLResponse)
async def dashboard_security(
    request: Request,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db)
):
    if not business.totp_secret:
        business.totp_secret = pyotp.random_base32()
        db.add(business)
        await db.commit()
        await db.refresh(business)
        
    return templates.TemplateResponse(request, "dashboard/security.html", {
        "business": business,
        "setup_required": request.query_params.get("required") == "1",
    })


@router.get("/settings/security/qr")
async def dashboard_security_qr(
    business: Business = Depends(get_current_business),
):
    if not business.totp_secret or business.is_2fa_enabled:
        raise HTTPException(status_code=404, detail="2FA setup QR is unavailable")
    provisioning_uri = pyotp.TOTP(business.totp_secret).provisioning_uri(
        name=business.email,
        issuer_name="revQR",
    )
    image = qrcode.make(provisioning_uri)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return Response(
        content=output.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "no-store, private", "Pragma": "no-cache"},
    )

@router.post("/settings/security/enable")
@limiter.limit("10/minute")
async def dashboard_security_enable(
    request: Request,
    totp_code: str = Form(...),
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db)
):
    if not business.totp_secret:
        return RedirectResponse(url="/dashboard/settings/security", status_code=status.HTTP_302_FOUND)
        
    totp = pyotp.TOTP(business.totp_secret)
    if totp.verify(totp_code.strip(), valid_window=1):
        business.is_2fa_enabled = True
        db.add(business)
        await db.commit()
        return templates.TemplateResponse(request, "dashboard/security.html", {
            "business": business,
            "success": "Two-Factor Authentication has been successfully enabled."
        })
    else:
        return templates.TemplateResponse(request, "dashboard/security.html", {
            "business": business,
            "error": "Invalid code. Please try again."
        })

@router.post("/settings/security/disable")
@limiter.limit("5/hour")
async def dashboard_security_disable(
    request: Request,
    password: str = Form(...),
    totp_code: str = Form(...),
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db)
):
    if business.is_admin:
        return templates.TemplateResponse(request, "dashboard/security.html", {
            "business": business,
            "error": "Two-factor authentication is mandatory for super administrators.",
        }, status_code=status.HTTP_403_FORBIDDEN)
    totp = pyotp.TOTP(business.totp_secret or "")
    if not verify_password(password, business.password_hash) or not totp.verify(
        totp_code.strip(), valid_window=1
    ):
        return templates.TemplateResponse(request, "dashboard/security.html", {
            "business": business,
            "error": "Password or authentication code is incorrect.",
        }, status_code=status.HTTP_400_BAD_REQUEST)
    business.is_2fa_enabled = False
    business.totp_secret = None
    db.add(business)
    await db.commit()
    return RedirectResponse(url="/dashboard/settings/security", status_code=status.HTTP_302_FOUND)

@router.post("/settings", response_class=HTMLResponse)
async def dashboard_settings_post(
    request: Request,
    name: str = Form(...),
    brand_color: str = Form(...),
    google_place_id: str = Form(""),
    phone: str = Form(""),
    custom_prompt: str = Form(""),
    logo: UploadFile = File(None),
    remove_logo: bool = Form(False),
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db)
):
    try:
        review_link = normalize_google_review_link(google_place_id)
    except GoogleReviewLinkError as exc:
        return templates.TemplateResponse(
            request,
            "dashboard/settings.html",
            {
                "business": business,
                "flash_error": str(exc),
                "google_profile_input": google_place_id,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    name = name.strip()
    if len(name) < 2:
        return templates.TemplateResponse(request, "dashboard/settings.html", {
            "business": business,
            "flash_error": "Business name must contain at least 2 characters.",
        }, status_code=status.HTTP_400_BAD_REQUEST)
    if not __import__("re").fullmatch(r"#[0-9A-Fa-f]{6}", brand_color):
        brand_color = "#6366f1"

    business.name = name[:255]
    business.brand_color = brand_color
    business.google_place_id = review_link
    business.phone = phone.strip()[:20] or None
    business.custom_prompt = custom_prompt.strip()[:2000] or None

    asset_result = await db.execute(
        select(BusinessAsset).where(
            BusinessAsset.business_id == business.id,
            BusinessAsset.kind == "logo",
        )
    )
    asset = asset_result.scalar_one_or_none()
    if remove_logo and asset:
        await db.delete(asset)
        business.logo_url = None
    elif logo and logo.filename:
        raw = await logo.read(settings.MAX_LOGO_BYTES + 1)
        try:
            data, content_type, digest = normalize_logo(raw)
        except LogoValidationError as exc:
            return templates.TemplateResponse(request, "dashboard/settings.html", {
                "business": business,
                "flash_error": str(exc),
            }, status_code=status.HTTP_400_BAD_REQUEST)
        if asset is None:
            asset = BusinessAsset(business_id=business.id, kind="logo")
        asset.data = data
        asset.content_type = content_type
        asset.size_bytes = len(data)
        asset.sha256 = digest
        db.add(asset)
        business.logo_url = f"/assets/logos/{business.id}"

    db.add(business)
    await db.commit()
    await db.refresh(business)

    return RedirectResponse(url="/dashboard/settings", status_code=status.HTTP_302_FOUND)
