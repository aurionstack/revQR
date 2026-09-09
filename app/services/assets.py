"""Validation and normalization for durable uploaded business assets."""

import hashlib
import io

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings


class LogoValidationError(ValueError):
    pass


def normalize_logo(raw: bytes) -> tuple[bytes, str, str]:
    """Validate an uploaded image and return a compact WebP plus its digest."""
    if not raw:
        raise LogoValidationError("Choose a logo image to upload.")
    if len(raw) > settings.MAX_LOGO_BYTES:
        raise LogoValidationError("Logo must be 2 MB or smaller.")

    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.width * source.height > 16_000_000:
                raise LogoValidationError("Logo dimensions are too large.")
            source.verify()
        with Image.open(io.BytesIO(raw)) as source:
            source = ImageOps.exif_transpose(source)
            if source.width < 32 or source.height < 32:
                raise LogoValidationError("Logo must be at least 32 × 32 pixels.")
            source.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            if source.mode not in ("RGB", "RGBA"):
                source = source.convert("RGBA")
            output = io.BytesIO()
            source.save(output, format="WEBP", quality=88, method=6)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        if isinstance(exc, LogoValidationError):
            raise
        raise LogoValidationError("Upload a valid PNG, JPEG, or WebP image.") from exc

    data = output.getvalue()
    return data, "image/webp", hashlib.sha256(data).hexdigest()
