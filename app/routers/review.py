import uuid
import json
from typing import Optional, Annotated
from datetime import datetime, timezone
import hashlib
import hmac

from fastapi import APIRouter, Depends, Request, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Business, Scan, Review, Feedback
from app.services.rate_limit import limiter
from app.services.time import format_local_datetime
from app.services.ai import generate_review_variations, ReviewVariations, _get_fallback_variations
from app.services.usage import reserve_business_generation
from app.services.business_context import import_business_context
from app.services.google_reviews import GoogleReviewLinkError, google_review_destination
from app.config import settings
from app.main import TEMPLATES_DIR

router = APIRouter(prefix="/review", tags=["Review Flow"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["local_time"] = format_local_datetime

async def require_qr_access(business_slug: str, db: AsyncSession = Depends(get_db)):
    business = (await db.execute(select(Business).where(Business.slug == business_slug))).scalar_one_or_none()
    if not business or not business.is_active:
        raise HTTPException(404, "Business not found or inactive.")
    if business.qr_revoked:
        raise HTTPException(403, "Review collection is temporarily unavailable for this business.")
    # Super-admin accounts use the same paid entitlement as customers for their
    # own QR profile. Administrative privileges never unlock a public QR.
    if business.is_admin and not business.has_active_subscription:
        raise HTTPException(402, "This QR subscription is not active.")

router.dependencies.append(Depends(require_qr_access))

def get_client_ip(request: Request) -> str:
    """Helper to get client IP for hashing in Scan model."""
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def parse_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}.") from exc


def normalize_source(value: str) -> str | None:
    clean = "_".join(value.strip().split())[:100]
    return "".join(char for char in clean if char.isalnum() or char in "_-") or None

@router.get("/{business_slug}", response_class=HTMLResponse)
@limiter.limit("60/minute")
async def review_landing(request: Request, business_slug: str, source: str = "", db: AsyncSession = Depends(get_db)):
    """
    Entry point when customer scans the QR code.
    Records a Scan and displays the star rating UI.
    Accepts ?source=waiter_rahul or ?source=table_3 for attribution.
    """
    # Fetch business
    res = await db.execute(select(Business).filter(Business.slug == business_slug))
    business = res.scalar_one_or_none()
    
    if not business or not business.is_active:
        raise HTTPException(status_code=404, detail="Business not found or inactive.")
    
    # Check if business has paid/unlocked QR code (optional for review flow? 
    # Usually you only block *generation* of the QR, not scanning it if it exists. 
    # But let's allow scanning regardless, or block if they didn't pay. We'll allow it so old QR codes don't break if sub ends).
    
    # Record scan
    ip = get_client_ip(request)
    ip_hash = hmac.new(
        settings.JWT_SECRET_KEY.encode("utf-8"),
        ip.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    user_agent = (request.headers.get("user-agent") or "")[:500] or None
    
    new_scan = Scan(
        business_id=business.id,
        ip_hash=ip_hash,
        user_agent=user_agent,
        source=normalize_source(source),
    )
    db.add(new_scan)
    await db.commit()
    await db.refresh(new_scan)
    
    return templates.TemplateResponse(request, "review/landing.html", {
        "business": business,
        "scan_id": str(new_scan.id),
        "source": source,
        "now": datetime.now(timezone.utc)
    })

@router.post("/{business_slug}/rate", response_class=HTMLResponse)
@limiter.limit("30/minute")
async def submit_rating(
    request: Request,
    business_slug: str,
    rating: Annotated[int, Form()],
    scan_id: Annotated[str, Form()],
    db: AsyncSession = Depends(get_db)
):
    """
    Handles HTMX POST from landing page star selection.
    Returns the feedback form partial.
    """
    res = await db.execute(select(Business).filter(Business.slug == business_slug))
    business = res.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")

    if rating < 1 or rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5.")
    scan_uuid = parse_uuid(scan_id, "scan session")
    scan_result = await db.execute(select(Scan).where(Scan.id == scan_uuid, Scan.business_id == business.id))
    if not scan_result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="This review session is no longer valid.")
        
    chips = []
    if rating >= 4:
        chips = ["Great service", "Friendly staff", "Clean", "Fast", "Highly recommend"]
    else:
        chips = ["Wait time", "Customer service", "Quality", "Cleanliness", "Pricing"]
        
    return templates.TemplateResponse(request, "review/feedback.html", {
        "slug": business.slug,
        "rating": rating,
        "scan_id": scan_id,
        "business_name": business.name,
        "chips": chips
    })

@router.post("/{business_slug}/generate", response_class=HTMLResponse)
@limiter.limit(settings.AI_RATE_LIMIT)
async def generate_review_text(
    request: Request,
    business_slug: str,
    rating: Annotated[int, Form()],
    scan_id: Annotated[str, Form()],
    selected_chips: Annotated[str, Form()] = "",
    customer_notes: Annotated[str, Form()] = "",
    db: AsyncSession = Depends(get_db)
):
    """
    Takes the rating and notes, generates 3 AI review variations, saves to DB.
    Returns the generated text UI partial with tabbed variations.
    """
    res = await db.execute(select(Business).filter(Business.slug == business_slug))
    business = res.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")

    if rating < 1 or rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5.")
    scan_uuid = parse_uuid(scan_id, "scan session")
    scan_result = await db.execute(select(Scan).where(Scan.id == scan_uuid, Scan.business_id == business.id))
    scan = scan_result.scalar_one_or_none()
    if not scan:
        raise HTTPException(status_code=400, detail="This review session is no longer valid.")

    # Preserve every selected highlight separately. The UI allows multiple chips,
    # while these caps keep public AI requests predictable and inexpensive.
    selected_tags = [
        tag.strip()[:50]
        for tag in selected_chips.split(",")
        if tag.strip()
    ][:8]
    customer_hint = customer_notes.strip()[:500]

    prompt_details = []
    if selected_tags:
        prompt_details.append("Selected highlights: " + "; ".join(selected_tags))
    if customer_hint:
        prompt_details.append("Customer's own hint: " + customer_hint)
    full_notes = "\n".join(prompt_details)

    # Backfill existing accounts once, ignoring the old unverified mock format.
    try:
        context_record = json.loads(business.scraped_context or "{}")
    except ValueError:
        context_record = {}
    if not isinstance(context_record, dict) or context_record.get("version") != 1:
        business.scraped_context = await import_business_context(business.google_place_id, business.name)

    # Generate 3 review variations using AI
    quota_available = await reserve_business_generation(db, business.id, scan_uuid)
    variations = await generate_review_variations(
        rating=rating, 
        notes=full_notes,
        business_name=business.name,
        custom_prompt=business.custom_prompt,
        scraped_context=business.scraped_context
    ) if quota_available else ReviewVariations(_get_fallback_variations(rating, business.name, full_notes))
    
    # Save Review to DB (primary = detailed version)
    primary_text = variations.get("detailed", variations.get("punchy", ""))
    new_review = Review(
        business_id=business.id,
        scan_id=scan_uuid,
        rating=rating,
        customer_notes=full_notes,
        generated_text=primary_text
    )
    db.add(new_review)
    await db.commit()
    await db.refresh(new_review)
    
    # Use the exact link supplied by the business. Do not guess from a name,
    # because a Maps search can send customers to the wrong listing.
    try:
        google_review_url = google_review_destination(business.google_place_id)
    except GoogleReviewLinkError:
        # Preserve the review draft even if an old account contains a malformed URL.
        google_review_url = None
        
    return templates.TemplateResponse(request, "review/generated.html", {
        "review_id": str(new_review.id),
        "generated_text": primary_text,
        "variations": variations,
        "ai_generated": getattr(variations, "ai_generated", True),
        "quota_exhausted": not quota_available,
        "variations_json": json.dumps(variations),
        "google_review_url": google_review_url,
        "slug": business.slug,
        "scan_id": scan_id,
        "rating": rating,
        "business_name": business.name
    })


async def _review_for_business(
    db: AsyncSession,
    business_slug: str,
    review_id: str,
) -> Review | None:
    review_uuid = parse_uuid(review_id, "review")
    result = await db.execute(
        select(Review)
        .join(Business, Business.id == Review.business_id)
        .where(Review.id == review_uuid, Business.slug == business_slug)
    )
    return result.scalar_one_or_none()


@router.post("/{business_slug}/copied")
@limiter.limit("30/minute")
async def mark_review_copied(
    request: Request,
    business_slug: str,
    db: AsyncSession = Depends(get_db),
):
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise HTTPException(status_code=415, detail="Content-Type must be application/json.")
    payload = await request.json()
    review = await _review_for_business(db, business_slug, str(payload.get("review_id", "")))
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    final_text = str(payload.get("final_text") or "").strip()
    review.copied = True
    review.final_text = final_text[:5000] or review.generated_text
    db.add(review)
    await db.commit()
    return {"status": "ok"}


@router.post("/{business_slug}/redirected")
@limiter.limit("30/minute")
async def mark_review_redirected(
    request: Request,
    business_slug: str,
    db: AsyncSession = Depends(get_db),
):
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise HTTPException(status_code=415, detail="Content-Type must be application/json.")
    payload = await request.json()
    review = await _review_for_business(db, business_slug, str(payload.get("review_id", "")))
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    review.redirected = True
    db.add(review)
    await db.commit()
    return {"status": "ok"}

@router.get("/{business_slug}/private-note-form", response_class=HTMLResponse)
async def get_private_note_form(
    request: Request, 
    business_slug: str,
    scan_id: str,
    review_id: str,
    rating: int,
    db: AsyncSession = Depends(get_db)
):
    """Returns the escaped form partial for a private note."""
    business_result = await db.execute(select(Business).where(Business.slug == business_slug))
    business = business_result.scalar_one_or_none()
    scan_uuid = parse_uuid(scan_id, "scan session")
    review_uuid = parse_uuid(review_id, "review")
    related_result = await db.execute(select(Review).where(
        Review.id == review_uuid,
        Review.business_id == business.id if business else None,
        Review.scan_id == scan_uuid,
    ))
    if not business or not related_result.scalar_one_or_none() or rating not in range(1, 6):
        raise HTTPException(status_code=400, detail="This review session is no longer valid.")
    return templates.TemplateResponse(request, "review/private_note_form.html", {
        "slug": business.slug,
        "business_name": business.name,
        "scan_id": scan_id,
        "review_id": review_id,
        "rating": rating,
    })

@router.post("/{business_slug}/private-note", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def submit_private_note(
    request: Request,
    business_slug: str,
    scan_id: Annotated[str, Form()],
    review_id: Annotated[str, Form()],
    rating: Annotated[int, Form()],
    message: Annotated[str, Form()],
    db: AsyncSession = Depends(get_db)
):
    """Saves private note and returns Thank You screen."""
    res = await db.execute(select(Business).filter(Business.slug == business_slug))
    business = res.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
        
    if rating not in range(1, 6):
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5.")
    clean_message = message.strip()[:2000]
    if not clean_message:
        raise HTTPException(status_code=400, detail="Private note cannot be empty.")
    scan_uuid = parse_uuid(scan_id, "scan session")
    review_uuid = parse_uuid(review_id, "review")
    relation_result = await db.execute(select(Review).where(
        Review.id == review_uuid,
        Review.business_id == business.id,
        Review.scan_id == scan_uuid,
    ))
    if not relation_result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="This review session is no longer valid.")
    new_feedback = Feedback(
        business_id=business.id,
        scan_id=scan_uuid,
        review_id=review_uuid,
        rating=rating,
        message=clean_message,
    )
    db.add(new_feedback)
    await db.commit()
    
    return templates.TemplateResponse(request, "review/thankyou.html", {
        "business": business
    })
