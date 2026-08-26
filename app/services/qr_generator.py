import io
import os
from typing import Optional
from PIL import Image, ImageDraw, ImageFont
import qrcode
import qrcode.image.svg


def generate_qr_code(
    url: str,
    format: str = "png",
    fill_color: str = "#000000",
    back_color: str = "#FFFFFF",
    business_name: Optional[str] = None,
    label_text: Optional[str] = None,
) -> bytes:
    """
    Generate an ultra-crisp, plain QR code with optional business name below it.
    No center icons/badges to keep the QR code 100% clean, authentic, and instantly scannable.
    """
    image_factory = None
    if format == "svg":
        image_factory = qrcode.image.svg.SvgPathImage

    # High-density box_size for razor-sharp rendering on retina screens and print
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=12,
        border=3,
        image_factory=image_factory,
    )
    qr.add_data(url)
    qr.make(fit=True)

    if format == "svg":
        img = qr.make_image(fill_color=fill_color, back_color=back_color)
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr)
        return img_byte_arr.getvalue()

    # PNG Format (PIL)
    img = qr.make_image(fill_color=fill_color, back_color=back_color).convert("RGBA")
    w, h = img.size

    # Display text below the QR code if business_name or label_text is provided
    display_text = label_text if label_text else business_name

    if display_text and display_text.strip():
        caption = display_text.strip().upper()
        banner_height = 48
        final_img = Image.new("RGBA", (w, h + banner_height), (255, 255, 255, 255))
        final_img.paste(img, (0, 0))
        draw = ImageDraw.Draw(final_img)

        # Subtle divider line
        draw.line([(24, h), (w - 24, h)], fill=(228, 228, 231, 255), width=1)

        # Clean centered business name
        draw.text(
            (w // 2, h + (banner_height // 2)),
            caption,
            fill=(24, 24, 27, 255),
            anchor="mm"
        )
        img = final_img

    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format="PNG", quality=95)
    return img_byte_arr.getvalue()
