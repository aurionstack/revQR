"""
Razorpay integration service.
Handles order creation and payment verification for QR code generation.
"""

import uuid
import hmac
import hashlib
import json
import re
import asyncio
import logging
import requests
from datetime import datetime, timezone

import razorpay
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.models import Payment, Business, WebhookEvent
from app.services.operations import queue_notification, audit
from app.services.plans import add_months, get_plan


# Initialize Razorpay client
class ProviderSession(requests.Session):
    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", (5, 15))
        return super().request(*args, **kwargs)


client = razorpay.Client(session=ProviderSession(), auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
logger = logging.getLogger(__name__)


class RetryableWebhookError(RuntimeError):
    pass


async def reconcile_order(order_id: str, db: AsyncSession, business_id=None):
    entities = await asyncio.to_thread(client.order.payments, order_id)
    query = select(Payment).where(Payment.razorpay_order_id == order_id)
    if business_id:
        query = query.where(Payment.business_id == business_id)
    payment = (await db.execute(query.with_for_update())).scalar_one_or_none()
    if not payment:
        return False
    items = entities.get("items", [])
    for candidate in items:
        if not _matches(payment, candidate):
            continue
        entity = await asyncio.to_thread(client.payment.fetch, candidate["id"])
        if not _matches(payment, entity):
            continue
        refunded=int(entity.get("amount_refunded") or 0)
        if refunded:
            if not 0 <= refunded <= payment.amount:
                continue
            payment.refunded_amount=max(payment.refunded_amount,refunded)
            payment.status="refunded" if refunded==payment.amount else "partially_refunded"
            payment.billing_review_required=True
            await audit(db,None,"billing.reconciliation_review",str(payment.id))
        elif entity.get("status")=="captured" and not payment.billing_review_required:
            await _activate(payment,entity,db)
    payment.last_checked_at=datetime.now(timezone.utc)
    await db.commit()
    return payment.entitlement_applied


class PaymentConfigurationError(RuntimeError):
    pass


def _clean_shipping(shipping: dict | None) -> dict[str, str]:
    values = shipping or {}
    cleaned = {
        "shipping_name": str(values.get("shipping_name", "")).strip()[:255],
        "shipping_phone": re.sub(r"[^0-9+]", "", str(values.get("shipping_phone", "")))[:20],
        "shipping_address": str(values.get("shipping_address", "")).strip()[:1000],
        "shipping_postal_code": re.sub(r"\D", "", str(values.get("shipping_postal_code", "")))[:6],
    }
    if (
        len(cleaned["shipping_name"]) < 2
        or len(cleaned["shipping_phone"].lstrip("+")) < 10
        or len(cleaned["shipping_address"]) < 10
        or len(cleaned["shipping_postal_code"]) != 6
    ):
        raise ValueError("Enter a complete delivery name, phone, address, and 6-digit PIN code.")
    return cleaned


async def create_order(
    business_id: str | uuid.UUID,
    db: AsyncSession,
    purpose: str = "subscription",
    plan_code: str | None = None,
    quantity: int = 1,
    shipping: dict | None = None,
) -> dict:
    """
    Create a Razorpay order for QR code generation.
    Returns the order details needed by the frontend checkout.
    """
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise PaymentConfigurationError("Online payments are temporarily unavailable.")
    if not settings.PUBLIC_CHECKOUT_ENABLED or not settings.POLICIES_APPROVED or not settings.RAZORPAY_WEBHOOK_SECRET:
        raise PaymentConfigurationError("Checkout is not open yet. Please contact support.")

    if purpose == "subscription":
        plan = get_plan(plan_code)
        if not plan:
            raise ValueError("Select a valid subscription plan.")
        amount = int(plan["amount"])
        quantity = 1
        shipping_values = {}
        description = f"revQR {plan['name']} subscription"
    elif purpose == "physical_stand":
        if not settings.PHYSICAL_STANDS_ENABLED or not settings.SHIPPING_ESTIMATE:
            raise PaymentConfigurationError("Physical stand ordering is not open yet.")
        if quantity < 1 or quantity > 20:
            raise ValueError("Physical stand quantity must be between 1 and 20.")
        amount = settings.PHYSICAL_STAND_PRICE_PAISE * quantity
        shipping_values = _clean_shipping(shipping)
        plan_code = None
        description = f"{quantity} physical revQR stand{'s' if quantity != 1 else ''}"
    else:
        raise ValueError("Invalid payment purpose.")

    currency = "INR"

    biz_id_str = str(business_id)
    biz_uuid = business_id if isinstance(business_id, uuid.UUID) else uuid.UUID(biz_id_str)

    # Create order via Razorpay API
    order_data = {
        "amount": amount,
        "currency": currency,
        "receipt": f"rq_{biz_id_str[:8]}_{uuid.uuid4().hex[:10]}",
        "notes": {
            "business_id": biz_id_str,
            "purpose": purpose,
            "plan_code": plan_code or "",
            "quantity": str(quantity),
        },
    }
    razorpay_order = await asyncio.to_thread(client.order.create, data=order_data)
    if (razorpay_order.get("amount") != amount or razorpay_order.get("currency") != currency
            or not razorpay_order.get("id")):
        raise PaymentConfigurationError("Payment provider returned an unexpected order.")

    # Save to DB
    payment = Payment(
        business_id=biz_uuid,
        razorpay_order_id=razorpay_order["id"],
        amount=amount,
        currency=currency,
        purpose=purpose,
        plan_code=plan_code,
        quantity=quantity,
        fulfillment_status="awaiting_payment" if purpose == "physical_stand" else None,
        **shipping_values,
        status="created",
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    return {
        "order_id": razorpay_order["id"],
        "amount": amount,
        "currency": currency,
        "key": settings.RAZORPAY_KEY_ID,
        "description": description,
        "purpose": purpose,
    }


async def verify_payment(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
    db: AsyncSession,
    business_id: str | uuid.UUID | None = None,
) -> bool:
    """
    Verify Razorpay payment signature and mark payment as paid.
    Returns True if verification succeeds.
    """
    # Verify signature using HMAC SHA256
    message = f"{razorpay_order_id}|{razorpay_payment_id}"
    expected_signature = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, razorpay_signature):
        return False

    # Serialize callback and webhook processing for this order.
    query = select(Payment).where(Payment.razorpay_order_id == razorpay_order_id)
    if business_id is not None:
        owner_uuid = business_id if isinstance(business_id, uuid.UUID) else uuid.UUID(str(business_id))
        query = query.where(Payment.business_id == owner_uuid)
    result = await db.execute(query.with_for_update())
    payment = result.scalar_one_or_none()

    if not payment:
        return False

    if payment.entitlement_applied:
        return payment.status == "paid" and payment.razorpay_payment_id == razorpay_payment_id
    entity = await asyncio.to_thread(client.payment.fetch, razorpay_payment_id)
    order = await asyncio.to_thread(client.order.fetch, razorpay_order_id)
    if (not _matches(payment, entity) or entity.get("status") != "captured"
            or int(entity.get("amount_refunded") or 0) != 0
            or order.get("id") != payment.razorpay_order_id
            or order.get("amount") != payment.amount or order.get("currency") != payment.currency
            or order.get("status") != "paid"):
        return False
    if not await _activate(payment, entity, db):
        return False
    payment.razorpay_signature = razorpay_signature
    await db.commit()
    return True


def _matches(payment: Payment, entity: dict) -> bool:
    return (isinstance(entity, dict) and entity.get("order_id") == payment.razorpay_order_id
            and entity.get("amount") == payment.amount and entity.get("currency") == payment.currency
            and isinstance(entity.get("id"), str))


async def _activate(payment: Payment, entity: dict, db: AsyncSession) -> bool:
    if not _matches(payment, entity) or entity.get("status") != "captured":
        return False
    if payment.entitlement_applied:
        return payment.razorpay_payment_id == entity["id"]
    business = (await db.execute(select(Business).where(Business.id == payment.business_id).with_for_update())).scalar_one_or_none()
    if not business or not _apply_paid_order(payment, business):
        return False
    payment.razorpay_payment_id = entity["id"]
    payment.status = "paid"
    payment.paid_at = datetime.now(timezone.utc)
    payment.entitlement_applied = True
    await queue_notification(db, f"receipt:{payment.id}", business.email, "Your RevQR payment receipt",
        f"Payment received: INR {payment.amount / 100:.2f}.\nOrder: {payment.razorpay_order_id}\nPayment: {entity['id']}\nPurchase: {payment.purpose}\nYour receipt and subscription details are in {settings.APP_URL}/dashboard/billing. This payment receipt is not a GST tax invoice.")
    await audit(db, None, "payment.activated", str(payment.id))
    return True


def _apply_paid_order(payment: Payment, business: Business) -> bool:
    if payment.purpose == "subscription":
        plan = get_plan(payment.plan_code)
        if not plan:
            return False
        now = datetime.now(timezone.utc)
        current_expiry = business.subscription_expires_at
        if current_expiry and current_expiry.tzinfo is None:
            current_expiry = current_expiry.replace(tzinfo=timezone.utc)
        starts_at = current_expiry if current_expiry and current_expiry > now else now
        business.subscription_expires_at = add_months(starts_at, int(plan["months"]))
        business.subscription_plan = payment.plan_code
        business.has_paid = True
        return True
    elif payment.purpose == "physical_stand":
        payment.fulfillment_status = "paid"
        return True
    return False


async def handle_webhook(raw_body: bytes, signature: str, db: AsyncSession, event_id: str = "") -> bool:
    """
    Handle Razorpay webhook events (backup verification).
    Called by POST /billing/webhook.
    """
    # Verify webhook signature
    secrets = [secret for secret in (settings.RAZORPAY_WEBHOOK_SECRET, settings.RAZORPAY_WEBHOOK_PREVIOUS_SECRET) if secret]
    if not secrets or not any(hmac.compare_digest(hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest(), signature) for secret in secrets):
        return False
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    event = payload.get("event", "")
    if not isinstance(event, str) or len(event) > 80:
        return False
    identity = event_id[:128] if event_id else hashlib.sha256(raw_body).hexdigest()
    inserted = (await db.execute(insert(WebhookEvent).values(event_id=identity, event_type=event, status="received").on_conflict_do_nothing().returning(WebhookEvent.event_id))).scalar_one_or_none()
    if not inserted:
        return True
    try:
        handled = {"payment.captured", "order.paid", "payment.failed", "refund.processed", "payment.dispute.created", "payment.dispute.won", "payment.dispute.lost", "payment.dispute.closed"}
        if event not in handled:
            event_record = await db.get(WebhookEvent, identity)
            event_record.status = "ignored"
            await db.commit()
            return True
        data = payload.get("payload", {})
        if not isinstance(data, dict):
            raise RetryableWebhookError("Invalid provider payload")
        kind = "refund" if event.startswith("refund.") else "dispute" if "dispute" in event else "payment"
        entity = data.get(kind, {}).get("entity", {})
        if not isinstance(entity, dict):
            raise RetryableWebhookError("Invalid provider entity")
        pid = entity.get("payment_id") if kind != "payment" else entity.get("id")
        if not pid:
            raise RetryableWebhookError("Missing payment identity")
        authoritative = await asyncio.to_thread(client.payment.fetch, pid)
        payment = (await db.execute(select(Payment).where(Payment.razorpay_order_id == authoritative.get("order_id")).with_for_update())).scalar_one_or_none()
        if not payment:
            # Order persistence may lag provider delivery. Roll back the event
            # receipt so a retried delivery can process it later.
            raise RetryableWebhookError("Order not yet available")
        if not _matches(payment, authoritative):
            raise RetryableWebhookError("Provider payment does not match order")
        if event in {"payment.captured", "order.paid"}:
            if authoritative.get("status") == "captured" and not authoritative.get("amount_refunded"):
                if not await _activate(payment, authoritative, db):
                    raise RetryableWebhookError("Activation unsuccessful")
            elif authoritative.get("status") not in {"refunded", "captured"}:
                raise RetryableWebhookError("Payment not captured")
        elif event == "payment.failed" and not payment.entitlement_applied:
            if authoritative.get("status") == "failed":
                payment.status = "failed"
        if event == "refund.processed" or authoritative.get("amount_refunded"):
            refunded = int(authoritative.get("amount_refunded") or 0)
            if not 0 < refunded <= payment.amount:
                raise RetryableWebhookError("Invalid refund amount")
            payment.refunded_amount = max(payment.refunded_amount, refunded)
            payment.status = "refunded" if payment.refunded_amount == payment.amount else "partially_refunded"
            payment.billing_review_required = True
        if "dispute" in event:
            payment.billing_review_required = True
            if event == "payment.dispute.created":
                payment.status = "disputed"
        if payment.billing_review_required:
            await audit(db, None, "billing.review_required", str(payment.id))
            if settings.SUPPORT_EMAIL:
                await queue_notification(db, f"billing-alert:{identity}", settings.SUPPORT_EMAIL, "RevQR payment needs review", f"Order {payment.razorpay_order_id} needs refund/dispute and entitlement review. Open {settings.APP_URL}/admin/operations.")
        record = await db.get(WebhookEvent, identity)
        record.status = "processed"
        await db.commit()
        return True
    except Exception as exc:
        await db.rollback()
        logger.warning("Webhook processing failed (%s)", type(exc).__name__)
        raise RetryableWebhookError("Webhook processing must be retried") from exc
