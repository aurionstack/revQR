import os
import uuid
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, desc

from app.database import get_db
from app.models import Business, Scan, Review, Feedback
from app.services.auth import get_current_business
from app.config import settings
from app.main import TEMPLATES_DIR

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Ensure media directory exists
MEDIA_DIR = "media"
os.makedirs(MEDIA_DIR, exist_ok=True)

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
    today = datetime.utcnow().date()
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        # Count scans on this day
        # For cross-DB compatibility, we can just fetch last 7 days and group in python
        chart_data.append({"label": day.strftime("%a"), "count": 0, "date": day})

    # Fetch last 7 days scans
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    recent_scans_res = await db.execute(
        select(Scan.scanned_at).filter(Scan.business_id == business.id, Scan.scanned_at >= seven_days_ago)
    )
    recent_scans = recent_scans_res.scalars().all()
    
    for scan_dt in recent_scans:
        scan_date = scan_dt.date()
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

# ── AI Review Reply (HTMX endpoint) ──────────────────────────────────────────

from app.services.ai import generate_review_reply

@router.post("/reviews/ai-reply", response_class=HTMLResponse)
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

import json

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

    wa_templates_json = json.dumps(wa_templates, ensure_ascii=False)

    return templates.TemplateResponse(request, "dashboard/whatsapp.html", {
        "business": business,
        "review_link": review_link,
        "wa_templates": wa_templates,
        "wa_templates_json": wa_templates_json,
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
    business: Business = Depends(get_current_business)
):
    app_url = str(request.base_url).rstrip("/")
    price_display = settings.QR_PRICE_PAISE // 100
    
    return templates.TemplateResponse(request, "dashboard/qr.html", {
        "business": business,
        "app_url": app_url,
        "price_display": price_display,
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

@router.post("/settings", response_class=HTMLResponse)
async def dashboard_settings_post(
    request: Request,
    name: str = Form(...),
    brand_color: str = Form(...),
    google_place_id: str = Form(""),
    phone: str = Form(""),
    custom_prompt: str = Form(""),
    logo: UploadFile = File(None),
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db)
):
    business.name = name
    business.brand_color = brand_color
    business.google_place_id = google_place_id
    business.phone = phone
    business.custom_prompt = custom_prompt

    if logo and logo.filename:
        # Save logo locally for now (in production, use S3)
        ext = os.path.splitext(logo.filename)[1]
        filename = f"logo_{business.id}_{uuid.uuid4().hex[:8]}{ext}"
        filepath = os.path.join(MEDIA_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(await logo.read())
        business.logo_url = f"/media/{filename}"

    db.add(business)
    await db.commit()
    await db.refresh(business)

    return RedirectResponse(url="/dashboard/settings", status_code=status.HTTP_302_FOUND)

from app.services.scraper import fetch_google_reviews

@router.post("/settings/scrape")
async def dashboard_settings_scrape(
    request: Request,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db)
):
    if not business.google_place_id:
        # Render a toast error if they try to scrape without a place ID
        return HTMLResponse(
            "<div class='toast toast-error'>Please enter a Google Place ID first and save settings.</div>",
            status_code=400
        )
        
    scraped_data = await fetch_google_reviews(business.google_place_id)
    if scraped_data:
        business.scraped_context = scraped_data
        db.add(business)
        await db.commit()
        # Return success toast and tell HTMX to reload the page to show new context
        return HTMLResponse(
            "<div class='toast toast-success'>Reviews fetched successfully! Reloading...</div>",
            headers={"HX-Refresh": "true"}
        )
    else:
        return HTMLResponse(
            "<div class='toast toast-error'>Failed to fetch reviews.</div>",
            status_code=500
        )
