import re
import uuid
from typing import Optional, Annotated
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, desc, or_

from app.database import get_db
from app.models import Business, Scan, Review, Feedback
from app.services.auth import get_current_admin, get_password_hash, create_access_token
from app.config import settings
from app.main import TEMPLATES_DIR

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def slugify(text: str) -> str:
    """Helper to convert business name to URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-")


@router.get("", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    q: Optional[str] = None,
    admin: Business = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Super Admin Overview & Client Management Portal.
    Lists all clients, payment status, scans, reviews, and quick actions.
    """
    # 1. Fetch Global Summary Stats
    total_clients_res = await db.execute(select(func.count(Business.id)))
    total_clients = total_clients_res.scalar() or 0

    paid_clients_res = await db.execute(select(func.count(Business.id)).filter(or_(Business.has_paid == True, Business.is_admin == True)))
    paid_clients = paid_clients_res.scalar() or 0

    total_scans_res = await db.execute(select(func.count(Scan.id)))
    total_scans = total_scans_res.scalar() or 0

    total_reviews_res = await db.execute(select(func.count(Review.id)))
    total_reviews = total_reviews_res.scalar() or 0

    # 2. Query Clients with Search filter
    query = select(Business).order_by(desc(Business.created_at))
    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Business.name.ilike(term),
                Business.slug.ilike(term),
                Business.email.ilike(term),
                Business.phone.ilike(term)
            )
        )

    clients_res = await db.execute(query)
    clients = clients_res.scalars().all()

    # 3. Aggregate scan and review counts per business
    # Query scans count per business
    scans_by_biz_res = await db.execute(
        select(Scan.business_id, func.count(Scan.id)).group_by(Scan.business_id)
    )
    scans_map = dict(scans_by_biz_res.all())

    reviews_by_biz_res = await db.execute(
        select(Review.business_id, func.count(Review.id)).group_by(Review.business_id)
    )
    reviews_map = dict(reviews_by_biz_res.all())

    client_data = []
    for c in clients:
        client_data.append({
            "business": c,
            "scans_count": scans_map.get(c.id, 0),
            "reviews_count": reviews_map.get(c.id, 0),
        })

    app_url = str(request.base_url).rstrip("/")

    return templates.TemplateResponse(request, "admin/dashboard.html", {
        "admin": admin,
        "total_clients": total_clients,
        "paid_clients": paid_clients,
        "total_scans": total_scans,
        "total_reviews": total_reviews,
        "clients": client_data,
        "search_query": q or "",
        "app_url": app_url,
    })


@router.get("/clients/new", response_class=HTMLResponse)
async def new_client_page(
    request: Request,
    admin: Business = Depends(get_current_admin)
):
    """Render manual client creation form (e.g. for offline/cash payments)."""
    return templates.TemplateResponse(request, "admin/new_client.html", {
        "admin": admin,
        "error": None
    })


@router.post("/clients/new", response_class=HTMLResponse)
async def create_client_post(
    request: Request,
    name: Annotated[str, Form()],
    slug: Annotated[Optional[str], Form()] = None,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    phone: Annotated[Optional[str], Form()] = None,
    google_place_id: Annotated[Optional[str], Form()] = None,
    brand_color: Annotated[str, Form()] = "#6366f1",
    has_paid: Annotated[Optional[bool], Form()] = False,
    is_admin: Annotated[Optional[bool], Form()] = False,
    admin: Business = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Manually create a new client account.
    If has_paid=True (e.g. cash payment collected), client immediately has full QR access!
    """
    name = name.strip()
    if not name:
        return templates.TemplateResponse(request, "admin/new_client.html", {
            "admin": admin, "error": "Business name is required."
        }, status_code=400)

    # Compute or validate slug
    computed_slug = slugify(slug) if slug and slug.strip() else slugify(name)
    if not computed_slug:
        computed_slug = f"biz-{uuid.uuid4().hex[:6]}"

    # Check if slug exists
    slug_check = await db.execute(select(Business).filter(Business.slug == computed_slug))
    if slug_check.scalar_one_or_none():
        # Append random suffix
        computed_slug = f"{computed_slug}-{uuid.uuid4().hex[:4]}"

    # Normalize email
    email = email.strip().lower()
    if not email:
        email = f"{computed_slug}@client.qrreviews.app"

    email_check = await db.execute(select(Business).filter(Business.email == email))
    if email_check.scalar_one_or_none():
        return templates.TemplateResponse(request, "admin/new_client.html", {
            "admin": admin, "error": f"An account with email '{email}' already exists."
        }, status_code=400)

    # Password
    if not password or not password.strip():
        password = "Client" + uuid.uuid4().hex[:6] + "!"

    password_hash = get_password_hash(password.strip())

    new_biz = Business(
        id=uuid.uuid4(),
        name=name,
        slug=computed_slug,
        email=email,
        password_hash=password_hash,
        phone=phone.strip() if phone else None,
        google_place_id=google_place_id.strip() if google_place_id else None,
        brand_color=brand_color if brand_color else "#6366f1",
        has_paid=bool(has_paid),
        is_admin=bool(is_admin),
        is_active=True
    )
    db.add(new_biz)
    await db.commit()
    await db.refresh(new_biz)

    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@router.post("/clients/{client_id}/toggle-paid")
async def toggle_paid_status(
    client_id: uuid.UUID,
    admin: Business = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """1-click toggle between Paid (cash/offline unlocked) and Unpaid."""
    res = await db.execute(select(Business).filter(Business.id == client_id))
    biz = res.scalar_one_or_none()
    if not biz:
        raise HTTPException(status_code=404, detail="Client not found")

    biz.has_paid = not biz.has_paid
    db.add(biz)
    await db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@router.post("/clients/{client_id}/toggle-active")
async def toggle_active_status(
    client_id: uuid.UUID,
    admin: Business = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """1-click activate or suspend client account."""
    res = await db.execute(select(Business).filter(Business.id == client_id))
    biz = res.scalar_one_or_none()
    if not biz:
        raise HTTPException(status_code=404, detail="Client not found")

    biz.is_active = not biz.is_active
    db.add(biz)
    await db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@router.post("/clients/{client_id}/impersonate")
async def impersonate_client(
    client_id: uuid.UUID,
    admin: Business = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    Log in as the client to view or configure their dashboard directly.
    Sets JWT token cookie for the target business.
    """
    res = await db.execute(select(Business).filter(Business.id == client_id))
    biz = res.scalar_one_or_none()
    if not biz:
        raise HTTPException(status_code=404, detail="Client not found")

    # Issue JWT for client
    token = create_access_token(data={"sub": str(biz.id), "email": biz.email})
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=60 * 60 * 24 * 7,
        samesite="lax",
    )
    return response


@router.get("/clients/{client_id}/standee", response_class=HTMLResponse)
async def view_client_standee(
    client_id: uuid.UUID,
    request: Request,
    admin: Business = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """View and print standee kit directly for any client."""
    res = await db.execute(select(Business).filter(Business.id == client_id))
    biz = res.scalar_one_or_none()
    if not biz:
        raise HTTPException(status_code=404, detail="Client not found")

    app_url = str(request.base_url).rstrip("/")
    review_link = f"{app_url}/review/{biz.slug}"

    return templates.TemplateResponse(request, "dashboard/standee.html", {
        "business": biz,
        "app_url": app_url,
        "review_link": review_link,
        "admin_view": True
    })


@router.get("/clients/{client_id}/edit", response_class=HTMLResponse)
async def edit_client_page(
    client_id: uuid.UUID,
    request: Request,
    admin: Business = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Edit client account details."""
    res = await db.execute(select(Business).filter(Business.id == client_id))
    biz = res.scalar_one_or_none()
    if not biz:
        raise HTTPException(status_code=404, detail="Client not found")

    return templates.TemplateResponse(request, "admin/edit_client.html", {
        "admin": admin,
        "client": biz,
        "error": None
    })


@router.post("/clients/{client_id}/edit", response_class=HTMLResponse)
async def edit_client_post(
    client_id: uuid.UUID,
    request: Request,
    name: Annotated[str, Form()],
    slug: Annotated[str, Form()],
    email: Annotated[str, Form()],
    phone: Annotated[Optional[str], Form()] = None,
    google_place_id: Annotated[Optional[str], Form()] = None,
    brand_color: Annotated[str, Form()] = "#6366f1",
    new_password: Annotated[Optional[str], Form()] = None,
    has_paid: Annotated[Optional[bool], Form()] = False,
    is_admin: Annotated[Optional[bool], Form()] = False,
    admin: Business = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Save edited client account details."""
    res = await db.execute(select(Business).filter(Business.id == client_id))
    biz = res.scalar_one_or_none()
    if not biz:
        raise HTTPException(status_code=404, detail="Client not found")

    # Update basic fields
    biz.name = name.strip()
    biz.slug = slugify(slug)
    biz.email = email.strip().lower()
    biz.phone = phone.strip() if phone else None
    biz.google_place_id = google_place_id.strip() if google_place_id else None
    biz.brand_color = brand_color if brand_color else "#6366f1"
    biz.has_paid = bool(has_paid)
    biz.is_admin = bool(is_admin)

    if new_password and new_password.strip():
        biz.password_hash = get_password_hash(new_password.strip())

    db.add(biz)
    await db.commit()
    await db.refresh(biz)

    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@router.post("/clients/{client_id}/delete")
async def delete_client(
    client_id: uuid.UUID,
    admin: Business = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Delete client and cascade associated scans, reviews, feedback, and payments."""
    if client_id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own admin account.")

    res = await db.execute(select(Business).filter(Business.id == client_id))
    biz = res.scalar_one_or_none()
    if not biz:
        raise HTTPException(status_code=404, detail="Client not found")

    await db.delete(biz)
    await db.commit()

    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
