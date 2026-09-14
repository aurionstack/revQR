"""
Billing router — handles Razorpay payment flow for QR code generation.
"""

import json

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import razorpay as razorpay_service
from app.services.auth import get_current_business
from app.config import settings

router = APIRouter(tags=["billing"])

@router.post("/dashboard/qr/create-order")
async def create_order(
    request: Request,
    db: AsyncSession = Depends(get_db),
    business = Depends(get_current_business),
):
    """
    Create a Razorpay order for QR code generation.
    Prices and quantities are always validated on the server.
    """
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        return JSONResponse({"error": "Content-Type must be application/json."}, status_code=415)
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        payload = {}

    purpose = str(payload.get("purpose", "subscription"))
    plan_code = str(payload.get("plan_code", "annual"))
    try:
        quantity = int(payload.get("quantity", 1))
    except (TypeError, ValueError):
        quantity = 0

    if purpose == "physical_stand" and not business.has_active_subscription:
        return JSONResponse({"error": "An active subscription is required before ordering stands."}, status_code=403)

    try:
        order = await razorpay_service.create_order(
            business.id,
            db,
            purpose=purpose,
            plan_code=plan_code,
            quantity=quantity,
            shipping=payload,
        )
        return JSONResponse({
            "order_id": order["order_id"],
            "amount": order["amount"],
            "currency": order["currency"],
            "key": order["key"],
            "name": "revQR",
            "description": order["description"],

            "business_name": business.name,
            "email": business.email,
            "slug": business.slug,
        })
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except razorpay_service.PaymentConfigurationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    except Exception:
        return JSONResponse({"error": "Payment order could not be created. Please try again."}, status_code=502)


@router.post("/dashboard/qr/verify-payment")
async def verify_payment(
    request: Request,
    db: AsyncSession = Depends(get_db),
    business = Depends(get_current_business),
):
    """
    Verify Razorpay payment and unlock QR code generation.
    """
    form = await request.form()
    razorpay_order_id = form.get("razorpay_order_id")
    razorpay_payment_id = form.get("razorpay_payment_id")
    razorpay_signature = form.get("razorpay_signature")

    if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature]):
        raise HTTPException(status_code=400, detail="Missing payment details")

    verified = await razorpay_service.verify_payment(
        razorpay_order_id,
        razorpay_payment_id,
        razorpay_signature,
        db,
        business_id=business.id,
    )

    if verified:
        # Also mark the business object in this request as paid so it's fresh if needed
        return RedirectResponse("/dashboard/qr?payment=success", status_code=303)
    else:
        raise HTTPException(status_code=400, detail="Payment verification failed")


@router.post("/billing/webhook")
async def razorpay_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Razorpay webhook endpoint for backup payment verification.
    Razorpay signs webhooks with X-Razorpay-Signature header.
    """
    signature = request.headers.get("X-Razorpay-Signature", "")
    body = await request.body()
    if len(body) > settings.MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="Webhook payload is too large")

    success = await razorpay_service.handle_webhook(body, signature, db)

    if success:
        return {"status": "ok"}
    else:
        raise HTTPException(status_code=400, detail="Webhook verification failed")
