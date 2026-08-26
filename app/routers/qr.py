from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.database import get_db
from app.models import Business
from app.services.auth import get_current_business
from app.services.qr_generator import generate_qr_code

router = APIRouter(prefix="/qr", tags=["qr"])


@router.get("/{slug}.{ext}")
async def get_qr_image(
    slug: str,
    ext: str,
    request: Request,
    download: int = 0,
    source: str = "",
    color: str = "",
    badge: int = 1,
    business: Business = Depends(get_current_business),
    db: AsyncSession = Depends(get_db)
):
    """
    Serve the branded QR code for a business as PNG or SVG.
    Supports brand colors, center badge, staff/table source tracking, and footer identification.
    """
    if ext not in ["png", "svg"]:
        raise HTTPException(status_code=400, detail="Invalid format. Use png or svg.")

    target_business = business
    # Check if the requested slug matches the logged-in business or if caller is admin
    if business.slug != slug:
        if not business.is_admin:
            raise HTTPException(status_code=403, detail="You can only view your own QR code.")
        res = await db.execute(select(Business).filter(Business.slug == slug))
        target_business = res.scalar_one_or_none()
        if not target_business:
            raise HTTPException(status_code=404, detail="Business not found.")

    # Check paywall (admin always bypasses)
    if not business.is_admin and not target_business.has_paid:
        raise HTTPException(status_code=402, detail="Payment required to unlock QR code.")

    # Generate the target URL for the QR code
    app_url = str(request.base_url).rstrip("/")
    target_url = f"{app_url}/review/{target_business.slug}"
    if source.strip():
        target_url += f"?source={source.strip()}"

    # Determine QR color (default to solid black for maximum scannability and contrast)
    fill_color = color.strip() if color.strip() else "#000000"


    # Bottom label text (shows business name and custom source/table below the QR)
    source_label = f" · {source.strip().replace('_', ' ').upper()}" if source.strip() else ""
    label_text = f"{target_business.name.upper()}{source_label}"

    # Generate QR Code bytes
    qr_bytes = generate_qr_code(
        target_url,
        format=ext,
        fill_color=fill_color,
        business_name=target_business.name,
        label_text=label_text,
    )

    media_type = "image/png" if ext == "png" else "image/svg+xml"

    headers = {}
    if download == 1:
        source_suffix = f"_{source.strip()}" if source.strip() else ""
        headers["Content-Disposition"] = f'attachment; filename="qr_{target_business.slug}{source_suffix}.{ext}"'

    return Response(content=qr_bytes, media_type=media_type, headers=headers)

