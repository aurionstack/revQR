"""Generate the deterministic revQR Open Graph social preview image."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "app" / "static" / "og-revqr.png"
ICON_OUTPUT = ROOT / "app" / "static" / "brand-icon.png"
WIDTH, HEIGHT = 1200, 630


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    filename = "arialbd.ttf" if bold else "arial.ttf"
    candidates = (
        Path("C:/Windows/Fonts") / filename,
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def main() -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#e8e2d2")
    draw = ImageDraw.Draw(image)

    for x in range(18, WIDTH, 24):
        for y in range(18, HEIGHT, 24):
            draw.ellipse((x, y, x + 2, y + 2), fill="#cbc3ad")

    draw.rounded_rectangle((66, 58, 1134, 572), radius=36, fill="#f9f7f0", outline="#c9c0aa", width=2)
    draw.rounded_rectangle((66, 58, 1134, 70), radius=6, fill="#5d63f1")

    draw.text((122, 112), "revQR", fill="#20201d", font=font(42, bold=True))
    draw.text((122, 205), "Turn every customer visit into", fill="#20201d", font=font(54, bold=True))
    draw.text((122, 274), "a relevant Google review.", fill="#5d63f1", font=font(54, bold=True))
    draw.text(
        (122, 370),
        "Branded QR codes · Customer-led AI drafts · Review analytics",
        fill="#5f5b52",
        font=font(25),
    )

    draw.rounded_rectangle((122, 456, 360, 514), radius=18, fill="#20201d")
    draw.text((153, 471), "revqr.tech", fill="#ffffff", font=font(23, bold=True))
    draw.text((872, 474), "Built for local business", fill="#777166", font=font(19))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT, format="PNG", optimize=True)

    icon = Image.new("RGB", (512, 512), "#e8e2d2")
    icon_draw = ImageDraw.Draw(icon)
    icon_draw.rounded_rectangle((32, 32, 480, 480), radius=96, fill="#20201d")
    light = "#f9f7f0"
    accent = "#6268f4"
    icon_draw.rounded_rectangle((104, 104, 236, 236), radius=12, outline=light, width=24)
    icon_draw.rectangle((145, 145, 195, 195), fill=accent)
    icon_draw.rounded_rectangle((276, 104, 408, 236), radius=12, outline=light, width=24)
    icon_draw.rectangle((317, 145, 367, 195), fill=accent)
    icon_draw.rounded_rectangle((104, 276, 236, 408), radius=12, outline=light, width=24)
    icon_draw.rectangle((145, 317, 195, 367), fill=accent)
    icon_draw.rectangle((276, 276, 324, 324), fill=light)
    icon_draw.rectangle((348, 276, 408, 324), fill=light)
    icon_draw.rectangle((276, 348, 324, 408), fill=light)
    icon_draw.rectangle((348, 348, 408, 408), fill=accent)
    icon_draw.text((256, 444), "revQR", fill=light, font=font(28, bold=True), anchor="mm")
    icon.save(ICON_OUTPUT, format="PNG", optimize=True)


if __name__ == "__main__":
    main()
