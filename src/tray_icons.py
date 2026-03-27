"""Status icons for the AXIS Producer system tray app."""

from PIL import Image, ImageDraw

SIZE = 64
HALF = SIZE // 2
RADIUS = 24


def _circle_icon(fill: str, outline: str = "#333333") -> Image.Image:
    """Draw a circle icon on a transparent background."""
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bbox = (HALF - RADIUS, HALF - RADIUS, HALF + RADIUS, HALF + RADIUS)
    draw.ellipse(bbox, fill=fill, outline=outline, width=2)
    # "A" letter in center for AXIS branding
    try:
        draw.text((HALF, HALF), "A", fill="white", anchor="mm")
    except TypeError:
        # Older Pillow without anchor support
        draw.text((HALF - 4, HALF - 6), "A", fill="white")
    return img


def icon_idle() -> Image.Image:
    """Gray icon — idle, not detecting."""
    return _circle_icon(fill="#666666")


def icon_detecting() -> Image.Image:
    """Yellow icon — listening for speech."""
    return _circle_icon(fill="#CCAA00")


def icon_recording() -> Image.Image:
    """Green icon — actively recording session."""
    return _circle_icon(fill="#00AA44")
