"""
Billing router — handles Razorpay payment flow for QR code generation.
"""

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
    Only called for non-admin, non-paid businesses.
    """
    if business.is_admin or business.has_paid:
        return JSONResponse({"error": "QR already unlocked"}, status_code=400)

    try:
        order = await razorpay_service.create_order(business.id, db)
        return JSONResponse({
            "order_id": order["order_id"],
            "amount": order["amount"],
            "currency": order["currency"],
            "key": order["key"],
            "name": "revQR",
            "description": f"QR Code for {business.name}",

            "business_name": business.name,
            "email": business.email,
            "slug": business.slug,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


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
        razorpay_order_id, razorpay_payment_id, razorpay_signature, db
    )

    if verified:
        # Also mark the business object in this request as paid so it's fresh if needed
        business.has_paid = True
        return RedirectResponse("/dashboard/qr", status_code=303)
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
    body = await request.json()

    success = await razorpay_service.handle_webhook(body, signature, db)

    if success:
        return {"status": "ok"}
    else:
        raise HTTPException(status_code=400, detail="Webhook verification failed")
