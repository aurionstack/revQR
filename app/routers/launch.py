"""Authenticated billing, reconciliation and operator readiness views."""
from datetime import datetime, timezone
import uuid
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import Business, Payment, Notification, WebhookEvent, AuditLog, UsageCounter
from app.services.auth import get_current_business, get_current_admin
from app.services.time import format_local_datetime
from app.services.operations import audit
from app.services.rate_limit import limiter
from app.services import razorpay as payments
from app.config import settings
from app.main import TEMPLATES_DIR

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["local_time"] = format_local_datetime


def launch_checks():
    return {
        "Dedicated webhook secret": bool(settings.RAZORPAY_WEBHOOK_SECRET),
        "Live Razorpay keys": settings.RAZORPAY_KEY_ID.startswith("rzp_live_") and bool(settings.RAZORPAY_KEY_SECRET),
        "Verified policies": settings.POLICIES_APPROVED,
        "Support contact": bool(settings.SUPPORT_EMAIL and settings.SUPPORT_PHONE),
        "Email delivery configuration": bool(settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASSWORD),
        "Shared IP rate-limit storage": settings.RATE_LIMIT_STORAGE_URI != "memory://",
        "Error-monitoring configuration": bool(settings.SENTRY_DSN or settings.NEW_RELIC_LICENSE_KEY),
        "Public checkout enabled": settings.PUBLIC_CHECKOUT_ENABLED,
        "Physical fulfilment enabled": settings.PHYSICAL_STANDS_ENABLED,
    }


@router.get("/dashboard/billing")
async def billing_page(request: Request, business: Business = Depends(get_current_business), db: AsyncSession = Depends(get_db)):
    orders_query = select(Payment).where(Payment.business_id == business.id)
    # Preserve the retired live-test transactions in the audit database while
    # keeping them out of the customer's normal billing ledger.
    if business.is_admin:
        orders_query = orders_query.where(Payment.amount != 100)
    orders = (await db.execute(orders_query.order_by(desc(Payment.created_at)).limit(50))).scalars().all()
    key=f"business:{business.id}:month:{datetime.now(timezone.utc):%Y-%m}"
    usage = await db.get(UsageCounter, key)
    return templates.TemplateResponse(request,"dashboard/billing.html",{
        "business":business,"orders":orders,"ai_used":usage.count if usage else 0,
        "config":settings,"pending":request.query_params.get("verification") == "pending",
    })


@router.post("/dashboard/billing/{payment_id}/recheck")
@limiter.limit("5/minute")
async def recheck_payment(request: Request, payment_id: uuid.UUID, business: Business = Depends(get_current_business), db: AsyncSession = Depends(get_db)):
    payment=(await db.execute(select(Payment).where(Payment.id==payment_id,Payment.business_id==business.id))).scalar_one_or_none()
    if not payment:
        raise HTTPException(404,"Payment not found")
    try:
        await payments.reconcile_order(payment.razorpay_order_id, db, business.id)
    except Exception:
        await db.rollback()
        return RedirectResponse("/dashboard/billing?verification=pending",303)
    return RedirectResponse("/dashboard/billing",303)


@router.get("/admin/operations")
async def operations_page(request: Request, admin: Business = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    reviews=(await db.execute(select(Payment).where(Payment.billing_review_required==True).order_by(desc(Payment.created_at)).limit(30))).scalars().all()
    events=(await db.execute(select(WebhookEvent).order_by(desc(WebhookEvent.received_at)).limit(30))).scalars().all()
    logs=(await db.execute(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(30))).scalars().all()
    unsent=(await db.execute(select(func.count(Notification.id)).where(Notification.sent_at.is_(None)))).scalar()
    failed=(await db.execute(select(func.count(Notification.id)).where(Notification.sent_at.is_(None),Notification.attempts>=5))).scalar()
    return templates.TemplateResponse(request,"admin/operations.html",{"admin":admin,"checks":launch_checks(),"reviews":reviews,"events":events,"logs":logs,"unsent":unsent,"failed":failed})


@router.post("/admin/operations/{payment_id}/resolve")
async def resolve_review(payment_id: uuid.UUID, confirmed: bool = Form(False), admin: Business = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    if not confirmed:
        raise HTTPException(400,"Confirm entitlement and refund/dispute review first")
    payment=(await db.execute(select(Payment).where(Payment.id==payment_id).with_for_update())).scalar_one_or_none()
    if not payment:
        raise HTTPException(404,"Payment not found")
    payment.billing_review_required=False
    await audit(db,admin.id,"billing.review_resolved",str(payment.id))
    await db.commit()
    return RedirectResponse("/admin/operations",303)
