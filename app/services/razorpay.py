"""
Razorpay integration service.
Handles order creation and payment verification for QR code generation.
"""

import uuid
import hmac
import hashlib
import json
import re
from datetime import datetime, timezone

import razorpay
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.models import Payment, Business
from app.services.plans import add_months, get_plan


# Initialize Razorpay client
client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


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

    if purpose == "subscription":
        plan = get_plan(plan_code)
        if not plan:
            raise ValueError("Select a valid subscription plan.")
        amount = int(plan["amount"])
        quantity = 1
        shipping_values = {}
        description = f"revQR {plan['name']} subscription"
    elif purpose == "physical_stand":
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
    razorpay_order = client.order.create(data=order_data)

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

    # Update payment record
    query = select(Payment).where(Payment.razorpay_order_id == razorpay_order_id)
    if business_id is not None:
        owner_uuid = business_id if isinstance(business_id, uuid.UUID) else uuid.UUID(str(business_id))
        query = query.where(Payment.business_id == owner_uuid)
    result = await db.execute(query)
    payment = result.scalar_one_or_none()

    if not payment:
        return False

    if payment.status == "paid":
        return payment.razorpay_payment_id == razorpay_payment_id

    payment.razorpay_payment_id = razorpay_payment_id
    payment.razorpay_signature = razorpay_signature
    payment.status = "paid"
    payment.paid_at = datetime.now(timezone.utc)

    biz_result = await db.execute(
        select(Business).where(Business.id == payment.business_id)
    )
    business = biz_result.scalar_one_or_none()
    if not business:
        return False
    if not _apply_paid_order(payment, business):
        await db.rollback()
        return False

    await db.commit()
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


async def handle_webhook(raw_body: bytes, signature: str, db: AsyncSession) -> bool:
    """
    Handle Razorpay webhook events (backup verification).
    Called by POST /billing/webhook.
    """
    # Verify webhook signature
    webhook_secret = settings.RAZORPAY_WEBHOOK_SECRET or settings.RAZORPAY_KEY_SECRET
    if not webhook_secret:
        return False
    try:
        client.utility.verify_webhook_signature(raw_body.decode("utf-8"), signature, webhook_secret)
        payload = json.loads(raw_body)
    except (razorpay.errors.SignatureVerificationError, UnicodeDecodeError, json.JSONDecodeError):
        return False

    event = payload.get("event", "")

    if event == "payment.captured":
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        order_id = payment_entity.get("order_id")
        payment_id = payment_entity.get("id")

        if order_id and payment_id:
            result = await db.execute(
                select(Payment).where(Payment.razorpay_order_id == order_id)
            )
            payment = result.scalar_one_or_none()

            if payment and payment.status != "paid":
                payment.razorpay_payment_id = payment_id
                payment.status = "paid"
                payment.paid_at = datetime.now(timezone.utc)

                biz_result = await db.execute(
                    select(Business).where(Business.id == payment.business_id)
                )
                business = biz_result.scalar_one_or_none()
                if not business:
                    return False
                if not _apply_paid_order(payment, business):
                    await db.rollback()
                    return False

                await db.commit()

    return True
