"""Pictures for the development sample data (Story 11.1): property photographs (free Unsplash
files in `photos/`, see its CREDITS.md), and drawn floor plans, agency logos and profile
pictures. No network.

The same arguments always give the same picture. Drawing lives in `kernel` because only `kernel`
imports Pillow.
"""

import colorsys
import io
import random
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 800, 600
PHOTOS = Path(__file__).parent / "photos"
SCENES = (
    "house",
    "tower",
    "lounge",
    "bedroom",
    "kitchen",
    "plot",
    "shop",
    "office",
)


def _colour(hue: float, light: float, saturation: float) -> tuple[int, int, int]:
    red, green, blue = colorsys.hls_to_rgb(hue % 1.0, light, saturation)
    return int(red * 255), int(green * 255), int(blue * 255)


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


@cache
def _photos(kind: str) -> tuple[bytes, ...]:
    return tuple(path.read_bytes() for path in sorted(PHOTOS.glob(f"{kind}-*.jpg")))


def scene(kind: str, variant: int) -> bytes:
    """One property photograph as JPEG bytes. `kind` is one of `SCENES`."""
    photos = _photos(kind)
    # Shifting once per lap keeps Listings whose variants step by the photo count apart.
    return photos[(variant + variant // len(photos)) % len(photos)]


def _text(draw, position, text: str, size: int, fill) -> None:
    draw.text(position, text, font=ImageFont.load_default(size), fill=fill, anchor="mm")


@cache
def floor_plan(variant: int) -> bytes:
    """A schematic floor plan with labelled rooms."""
    rng = random.Random(f"plan-{variant}")  # noqa: S311 - not a secret
    image = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    ink = (36, 44, 60)
    split = rng.randrange(330, 470)
    middle = rng.randrange(250, 330)
    lower = rng.randrange(410, 450)
    right = rng.randrange(260, 320)
    rooms = (
        ("Lounge", (60, 60, split, middle)),
        ("Bedroom 1", (split, 60, 740, right)),
        ("Kitchen", (60, middle, 250, 540)),
        ("Dining", (250, middle, split, 540)),
        ("Bedroom 2", (split, right, 740, lower)),
        ("Bath", (split, lower, 590, 540)),
        ("Store", (590, lower, 740, 540)),
    )
    for name, box in rooms:
        draw.rectangle(box, outline=ink, width=6)
        centre = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
        _text(draw, centre, name, 22, ink)
    draw.rectangle((120, 534, 190, 546), fill=(255, 255, 255))  # the entrance
    _text(draw, (155, 568), "Entrance", 18, ink)
    _text(draw, (400, 30), "Floor plan (not to scale)", 24, ink)
    return _png(image)


@cache
def badge(initials: str, variant: int) -> bytes:
    """A square agency logo or profile picture: initials on a coloured shape."""
    size = 400
    hue = (variant * 0.173) % 1.0
    image = Image.new("RGB", (size, size), _colour(hue, 0.9, 0.4))
    draw = ImageDraw.Draw(image)
    fill = _colour(hue, 0.36, 0.55)
    if variant % 2:
        draw.ellipse((40, 40, size - 40, size - 40), fill=fill)
    else:
        draw.rounded_rectangle((40, 40, size - 40, size - 40), radius=60, fill=fill)
    _text(draw, (size // 2, size // 2), initials, 140, (255, 255, 255))
    return _png(image)
