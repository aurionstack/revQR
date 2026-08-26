"""
Razorpay integration service.
Handles order creation and payment verification for QR code generation.
"""

import uuid
import hmac
import hashlib
from datetime import datetime, timezone

import razorpay
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.models import Payment, Business


# Initialize Razorpay client
client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


async def create_order(business_id: str | uuid.UUID, db: AsyncSession) -> dict:
    """
    Create a Razorpay order for QR code generation.
    Returns the order details needed by the frontend checkout.
    """
    amount = settings.QR_PRICE_PAISE  # in paise
    currency = "INR"

    biz_id_str = str(business_id)
    biz_uuid = business_id if isinstance(business_id, uuid.UUID) else uuid.UUID(biz_id_str)

    # Create order via Razorpay API
    order_data = {
        "amount": amount,
        "currency": currency,
        "receipt": f"qr_{biz_id_str[:8]}",
        "notes": {
            "business_id": biz_id_str,
            "purpose": "qr_code_generation",
        },
    }
    razorpay_order = client.order.create(data=order_data)

    # Save to DB
    payment = Payment(
        business_id=biz_uuid,
        razorpay_order_id=razorpay_order["id"],
        amount=amount,
        currency=currency,
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
    }


async def verify_payment(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
    db: AsyncSession,
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
    result = await db.execute(
        select(Payment).where(Payment.razorpay_order_id == razorpay_order_id)
    )
    payment = result.scalar_one_or_none()

    if not payment:
        return False

    payment.razorpay_payment_id = razorpay_payment_id
    payment.razorpay_signature = razorpay_signature
    payment.status = "paid"
    payment.paid_at = datetime.now(timezone.utc)

    # Unlock QR for the business
    biz_result = await db.execute(
        select(Business).where(Business.id == payment.business_id)
    )
    business = biz_result.scalar_one_or_none()
    if business:
        business.has_paid = True

    await db.commit()
    return True


async def handle_webhook(payload: dict, signature: str, db: AsyncSession) -> bool:
    """
    Handle Razorpay webhook events (backup verification).
    Called by POST /billing/webhook.
    """
    # Verify webhook signature
    try:
        client.utility.verify_webhook_signature(
            str(payload).encode("utf-8"),
            signature,
            settings.RAZORPAY_KEY_SECRET,
        )
    except razorpay.errors.SignatureVerificationError:
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

                # Unlock QR
                biz_result = await db.execute(
                    select(Business).where(Business.id == payment.business_id)
                )
                business = biz_result.scalar_one_or_none()
                if business:
                    business.has_paid = True

                await db.commit()

    return True
