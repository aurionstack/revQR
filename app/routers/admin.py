import re
import uuid
from typing import Optional, Annotated
from datetime import datetime, timedelta, timezone

from email_validator import EmailNotValidError, validate_email

from fastapi import APIRouter, Depends, Form, Request, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, delete, func, desc, or_

from app.database import get_db
from app.models import Business, Scan, Review, Feedback, Payment
from app.services.auth import (
    create_access_token,
    get_current_admin,
    get_password_hash,
    set_access_cookie,
    validate_password_strength,
)
from app.services.plans import add_months
from app.services.google_reviews import GoogleReviewLinkError, normalize_google_review_link
from app.services.time import format_local_datetime
from app.services.business_context import import_business_context
from app.services.operations import queue_notification, audit
from urllib.parse import urlparse
from app.config import settings
from app.main import TEMPLATES_DIR

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["local_time"] = format_local_datetime


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

    now = datetime.now(timezone.utc)
    paid_clients_res = await db.execute(select(func.count(Business.id)).filter(or_(
        Business.is_admin == True,
        and_(
            Business.has_paid == True,
            or_(Business.subscription_expires_at.is_(None), Business.subscription_expires_at > now),
        ),
    )))
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
    stand_orders_result = await db.execute(
        select(Payment, Business)
        .join(Business, Business.id == Payment.business_id)
        .where(Payment.purpose == "physical_stand", Payment.status == "paid")
        .order_by(desc(Payment.paid_at))
        .limit(20)
    )
    stand_orders = [
        {"payment": payment, "business": owner}
        for payment, owner in stand_orders_result.all()
    ]

    return templates.TemplateResponse(request, "admin/dashboard.html", {
        "admin": admin,
        "total_clients": total_clients,
        "paid_clients": paid_clients,
        "total_scans": total_scans,
        "total_reviews": total_reviews,
        "clients": client_data,
        "search_query": q or "",
        "app_url": app_url,
        "stand_orders": stand_orders,
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
    try:
        email = validate_email(email.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return templates.TemplateResponse(request, "admin/new_client.html", {
            "admin": admin, "error": "Enter a valid client email address."
        }, status_code=400)

    email_check = await db.execute(select(Business).filter(Business.email == email))
    if email_check.scalar_one_or_none():
        return templates.TemplateResponse(request, "admin/new_client.html", {
            "admin": admin, "error": f"An account with email '{email}' already exists."
        }, status_code=400)

    # Password
    if not password or not password.strip():
        password = "Client" + uuid.uuid4().hex[:6] + "!"
    password_error = validate_password_strength(password.strip())
    if password_error:
        return templates.TemplateResponse(request, "admin/new_client.html", {
            "admin": admin, "error": password_error
        }, status_code=400)

    password_hash = get_password_hash(password.strip())

    try:
        review_link = normalize_google_review_link(google_place_id)
    except GoogleReviewLinkError as exc:
        return templates.TemplateResponse(request, "admin/new_client.html", {
            "admin": admin, "error": str(exc)
        }, status_code=400)

    new_biz = Business(
        id=uuid.uuid4(),
        name=name,
        slug=computed_slug,
        email=email,
        password_hash=password_hash,
        phone=phone.strip() if phone else None,
        google_place_id=review_link,
        scraped_context=await import_business_context(review_link, name),
        brand_color=brand_color if brand_color else "#6366f1",
        has_paid=bool(has_paid),
        is_admin=False,
        is_active=True,
        email_verified=False,
        subscription_plan="annual" if has_paid else None,
        subscription_expires_at=add_months(datetime.now(timezone.utc), 12) if has_paid else None,
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
    if biz.is_admin:
        raise HTTPException(status_code=400, detail="Admin entitlement cannot be changed here.")

    if biz.has_active_subscription:
        biz.has_paid = False
        biz.subscription_plan = None
        biz.subscription_expires_at = None
    else:
        biz.has_paid = True
        biz.subscription_plan = "annual"
        biz.subscription_expires_at = add_months(datetime.now(timezone.utc), 12)
    db.add(biz)
    await db.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@router.post("/clients/{client_id}/qr-access")
async def set_qr_access(
    client_id: uuid.UUID,
    revoke: bool = Form(...),
    confirmed: bool = Form(False),
    admin: Business = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    if not confirmed:
        raise HTTPException(400, "Confirm the QR access change first.")
    biz = (await db.execute(select(Business).where(Business.id == client_id).with_for_update())).scalar_one_or_none()
    if not biz:
        raise HTTPException(404, "Client not found")
    if biz.is_admin:
        raise HTTPException(400, "Administrator QR access cannot be revoked here.")
    biz.qr_revoked = revoke
    await audit(db, admin.id, "qr.revoked" if revoke else "qr.restored", str(biz.id))
    await db.commit()
    return RedirectResponse("/admin", 303)


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
    if biz.is_admin:
        raise HTTPException(status_code=400, detail="Admin accounts cannot be suspended here.")

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
    if biz.is_admin or not biz.is_active:
        raise HTTPException(status_code=400, detail="This account cannot be impersonated.")
    token = create_access_token(
        data={
            "sub": str(biz.id),
            "email": biz.email,
            "password_version": biz.password_version,
            "impersonated_by": str(admin.id),
        },
        expires_delta=timedelta(minutes=30),
    )
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    set_access_cookie(response, token, max_age_seconds=30 * 60)
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
    if biz.qr_revoked:
        raise HTTPException(403, "Restore QR access before creating a standee.")

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
    admin: Business = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Save edited client account details."""
    res = await db.execute(select(Business).filter(Business.id == client_id))
    biz = res.scalar_one_or_none()
    if not biz:
        raise HTTPException(status_code=404, detail="Client not found")

    try:
        review_link = normalize_google_review_link(google_place_id)
    except GoogleReviewLinkError as exc:
        return templates.TemplateResponse(request, "admin/edit_client.html", {
            "admin": admin, "client": biz, "error": str(exc)
        }, status_code=400)

    # Update basic fields
    updated_name = name.strip()
    updated_slug = slugify(slug)
    if len(updated_name) < 2 or not updated_slug:
        return templates.TemplateResponse(request, "admin/edit_client.html", {
            "admin": admin, "client": biz, "error": "Business name and URL slug are required."
        }, status_code=400)
    try:
        updated_email = validate_email(email.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return templates.TemplateResponse(request, "admin/edit_client.html", {
            "admin": admin, "client": biz, "error": "Enter a valid client email address."
        }, status_code=400)
    conflict = await db.execute(select(Business).where(
        ((Business.email == updated_email) | (Business.slug == updated_slug)),
        Business.id != biz.id,
    ))
    if conflict.scalar_one_or_none():
        return templates.TemplateResponse(request, "admin/edit_client.html", {
            "admin": admin, "client": biz, "error": "That email address or review URL is already in use."
        }, status_code=400)
    old_email = biz.email
    refresh_context = biz.google_place_id != review_link or biz.name != updated_name[:255] or not biz.scraped_context
    biz.name = updated_name[:255]
    biz.slug = updated_slug
    biz.email = updated_email
    biz.phone = phone.strip() if phone else None
    biz.google_place_id = review_link
    if refresh_context:
        biz.scraped_context = await import_business_context(review_link, biz.name)
    biz.brand_color = brand_color if brand_color else "#6366f1"
    if not biz.is_admin:
        was_paid = biz.has_paid
        biz.has_paid = bool(has_paid)
        if biz.has_paid and not was_paid:
            biz.subscription_plan = "annual"
            biz.subscription_expires_at = add_months(datetime.now(timezone.utc), 12)
        elif not biz.has_paid:
            biz.subscription_plan = None
            biz.subscription_expires_at = None
    if old_email != updated_email:
        biz.email_verified = False
        biz.password_version += 1

    if new_password and new_password.strip():
        password_error = validate_password_strength(new_password.strip())
        if password_error:
            return templates.TemplateResponse(request, "admin/edit_client.html", {
                "admin": admin, "client": biz, "error": password_error
            }, status_code=400)
        biz.password_hash = get_password_hash(new_password.strip())
        biz.password_version += 1

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

    # Use a SQL DELETE so PostgreSQL's ON DELETE CASCADE removes payments,
    # reviews, scans, and feedback atomically. ORM instance deletion previously
    # attempted payments.business_id = NULL and violated its NOT NULL constraint.
    await db.execute(delete(Business).where(Business.id == client_id))
    await db.commit()

    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@router.post("/stand-orders/{order_id}/status")
async def update_stand_order_status(
    order_id: uuid.UUID,
    fulfillment_status: Annotated[str, Form()],
    tracking_number: Annotated[str, Form()] = "",
    tracking_url: Annotated[str, Form()] = "",
    admin: Business = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    allowed = {"paid", "processing", "shipped", "delivered", "cancelled"}
    if fulfillment_status not in allowed:
        raise HTTPException(status_code=400, detail="Invalid fulfillment status")
    result = await db.execute(select(Payment).where(
        Payment.id == order_id,
        Payment.purpose == "physical_stand",
        Payment.status == "paid",
    ))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Stand order not found")
    tracking_url=tracking_url.strip()
    parsed=urlparse(tracking_url)
    if tracking_url and (parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password):
        raise HTTPException(400,"Tracking URL must be a complete HTTPS courier URL")
    if fulfillment_status == "shipped" and not tracking_number.strip():
        raise HTTPException(400,"Enter a tracking number before marking an order shipped")
    order.fulfillment_status = fulfillment_status
    order.tracking_number=tracking_number.strip()[:100] or None
    order.tracking_url=tracking_url[:500] or None
    business=await db.get(Business,order.business_id)
    if business:
        await queue_notification(db, f"stand:{order.id}:{fulfillment_status}:{order.tracking_number or ''}",business.email,
            "Your RevQR stand order update", f"Order {order.razorpay_order_id}: {fulfillment_status}.\nTracking: {order.tracking_number or 'Not yet assigned'}\n{order.tracking_url or ''}\nDetails: {settings.APP_URL}/dashboard/billing")
    await audit(db,admin.id,"stand.status_updated",str(order.id))
    db.add(order)
    await db.commit()
    return RedirectResponse(url="/admin#stand-orders", status_code=status.HTTP_302_FOUND)
