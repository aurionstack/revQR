import uuid
import json
from typing import Optional, Annotated
from datetime import datetime, timezone
import hashlib

from fastapi import APIRouter, Depends, Request, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Business, Scan, Review, Feedback
from app.services.rate_limit import limiter
from app.services.ai import generate_review_variations, generate_review_reply
from app.config import settings
from app.main import TEMPLATES_DIR

router = APIRouter(prefix="/review", tags=["Review Flow"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

def get_client_ip(request: Request) -> str:
    """Helper to get client IP for hashing in Scan model."""
    if request.client and request.client.host:
        return request.client.host
    return "unknown"

@router.get("/{business_slug}", response_class=HTMLResponse)
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
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()
    user_agent = request.headers.get("user-agent")
    
    new_scan = Scan(
        business_id=business.id,
        ip_hash=ip_hash,
        user_agent=user_agent,
        source=source[:100] if source else None,
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
        
    # Combine selected chips and customer notes
    full_notes = ""
    if selected_chips:
        full_notes += selected_chips + ". "
    if customer_notes:
        full_notes += customer_notes

    # Generate 3 review variations using AI
    variations = await generate_review_variations(
        rating=rating, 
        notes=full_notes, 
        business_name=business.name,
        custom_prompt=business.custom_prompt,
        scraped_context=business.scraped_context
    )
    
    # Save Review to DB (primary = detailed version)
    primary_text = variations.get("detailed", variations.get("punchy", ""))
    new_review = Review(
        business_id=business.id,
        scan_id=uuid.UUID(scan_id) if scan_id else None,
        rating=rating,
        customer_notes=full_notes,
        generated_text=primary_text
    )
    db.add(new_review)
    await db.commit()
    await db.refresh(new_review)
    
    # Build Google URL
    # If place_id exists, construct exact write review url or use it directly if it's a URL. Else fallback to maps search.
    if business.google_place_id:
        if business.google_place_id.startswith("http://") or business.google_place_id.startswith("https://"):
            google_review_url = business.google_place_id
        else:
            google_review_url = f"https://search.google.com/local/writereview?placeid={business.google_place_id}"
    else:
        encoded_name = business.name.replace(" ", "+")
        google_review_url = f"https://www.google.com/maps/search/?api=1&query={encoded_name}"
        
    return templates.TemplateResponse(request, "review/generated.html", {
        "review_id": str(new_review.id),
        "generated_text": primary_text,
        "variations": variations,
        "variations_json": json.dumps(variations),
        "google_review_url": google_review_url,
        "slug": business.slug,
        "scan_id": scan_id,
        "rating": rating,
        "business_name": business.name
    })

@router.get("/{business_slug}/private-note-form", response_class=HTMLResponse)
async def get_private_note_form(
    request: Request, 
    business_slug: str,
    scan_id: str,
    review_id: str,
    rating: int,
    db: AsyncSession = Depends(get_db)
):
    """Returns the form partial for a private note."""
    return HTMLResponse(content=f'''
        <div class="private-note-box fade-in">
            <h3>Send a private message</h3>
            <form hx-post="/review/{business_slug}/private-note" hx-target="#review-stage" hx-swap="innerHTML">
                <input type="hidden" name="scan_id" value="{scan_id}" />
                <input type="hidden" name="review_id" value="{review_id}" />
                <input type="hidden" name="rating" value="{rating}" />
                <textarea name="message" rows="3" placeholder="This goes directly to the business owner..." required></textarea>
                <div class="action-row" style="margin-top:12px;">
                    <button type="submit" class="btn btn-primary">Send Message</button>
                </div>
            </form>
        </div>
    ''')

@router.post("/{business_slug}/private-note", response_class=HTMLResponse)
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
        
    new_feedback = Feedback(
        business_id=business.id,
        scan_id=uuid.UUID(scan_id) if scan_id else None,
        review_id=uuid.UUID(review_id) if review_id else None,
        rating=rating,
        message=message
    )
    db.add(new_feedback)
    await db.commit()
    
    return templates.TemplateResponse(request, "review/thankyou.html", {
        "business": business
    })
