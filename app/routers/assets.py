"""Public, immutable-by-ETag delivery for database-backed business assets."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import BusinessAsset

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("/logos/{business_id}")
async def business_logo(
    business_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BusinessAsset).where(
            BusinessAsset.business_id == business_id,
            BusinessAsset.kind == "logo",
        )
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Logo not found")

    etag = f'"{asset.sha256}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    return Response(
        content=asset.data,
        media_type=asset.content_type,
        headers={
            "Cache-Control": "public, max-age=86400, must-revalidate",
            "ETag": etag,
            "X-Content-Type-Options": "nosniff",
        },
    )
