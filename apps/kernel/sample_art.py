"""Drawn placeholder pictures for the development sample data (Story 11.1): property scenes,
floor plans, agency logos and profile pictures, as PNG bytes. No photographs, no network.

The same arguments always draw the same picture; different variants differ in colours and shapes.
It lives in `kernel` because only `kernel` imports Pillow.
"""

import colorsys
import io
import random
from functools import cache

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 800, 600
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


def _sky(draw, rng, horizon: int) -> None:
    hue = rng.uniform(0.52, 0.63)
    for y in range(0, horizon, 4):
        draw.rectangle((0, y, WIDTH, y + 4), fill=_colour(hue, 0.62 + 0.3 * y / horizon, 0.7))
    x, y = rng.randrange(60, 740), rng.randrange(40, 130)
    draw.ellipse((x - 35, y - 35, x + 35, y + 35), fill=(255, 226, 120))


def _tree(draw, x: int, ground: int, size: int) -> None:
    draw.rectangle((x - 8, ground - size, x + 8, ground), fill=(110, 78, 52))
    draw.ellipse(
        (x - size // 2, ground - size * 2, x + size // 2, ground - size + 20), fill=(66, 133, 74)
    )


def _window(draw, box, frame=(255, 255, 255), glass=(140, 190, 222)) -> None:
    left, top, right, bottom = box
    draw.rectangle(box, fill=glass, outline=frame, width=5)
    draw.line(((left + right) // 2, top, (left + right) // 2, bottom), fill=frame, width=3)
    draw.line((left, (top + bottom) // 2, right, (top + bottom) // 2), fill=frame, width=3)


def _house(draw, rng, hue: float) -> None:
    ground = 440
    _sky(draw, rng, ground)
    draw.rectangle((0, ground, WIDTH, HEIGHT), fill=(126, 170, 96))
    width = rng.randrange(380, 520)
    left = (WIDTH - width) // 2 + rng.randrange(-60, 60)
    storeys = rng.choice((1, 2, 2, 3))
    top = ground - 105 * storeys
    draw.rectangle((left, top, left + width, ground), fill=_colour(hue, 0.76, 0.45))
    if rng.random() < 0.5:
        draw.polygon(
            ((left - 30, top), (left + width // 2, top - 90), (left + width + 30, top)),
            fill=_colour(hue + 0.5, 0.38, 0.45),
        )
    else:
        draw.rectangle((left - 15, top - 28, left + width + 15, top), fill=_colour(hue, 0.4, 0.3))
    for storey in range(storeys):
        y = top + 105 * storey + 22
        for x in range(left + 30, left + width - 90, 120):
            _window(draw, (x, y, x + 74, y + 58))
    door = left + rng.randrange(40, width - 110)
    draw.rectangle((door, ground - 88, door + 56, ground), fill=_colour(hue + 0.55, 0.3, 0.5))
    draw.polygon(
        ((door, ground), (door + 56, ground), (door + 150, HEIGHT), (door - 60, HEIGHT)),
        fill=(188, 182, 170),
    )
    for x in rng.sample((60, 120, 700, 750), 2):
        _tree(draw, x, ground + 20, rng.randrange(55, 90))


def _tower(draw, rng, hue: float) -> None:
    ground = 520
    _sky(draw, rng, ground)
    draw.rectangle((0, ground, WIDTH, HEIGHT), fill=(120, 122, 128))
    for x, height in ((40, rng.randrange(160, 280)), (620, rng.randrange(180, 320))):
        draw.rectangle((x, ground - height, x + 140, ground), fill=_colour(hue + 0.3, 0.6, 0.15))
    columns = rng.choice((3, 4, 5))
    width = columns * 84 + 28
    left = (WIDTH - width) // 2
    top = rng.randrange(50, 130)
    draw.rectangle((left, top, left + width, ground), fill=_colour(hue, 0.7, 0.35))
    for y in range(top + 20, ground - 70, 56):
        for x in range(left + 20, left + width - 70, 84):
            _window(draw, (x, y, x + 60, y + 34))
            draw.rectangle((x - 4, y + 34, x + 64, y + 40), fill=_colour(hue, 0.4, 0.3))
    draw.rectangle(
        (left + width // 2 - 40, ground - 56, left + width // 2 + 40, ground), fill=(52, 62, 74)
    )


def _room(draw, rng, hue: float) -> int:
    floor = 400
    draw.rectangle((0, 0, WIDTH, floor), fill=_colour(hue, 0.86, 0.35))
    draw.rectangle((0, floor, WIDTH, HEIGHT), fill=_colour(0.08, rng.uniform(0.4, 0.62), 0.4))
    for x in range(-200, WIDTH + 200, 90):
        draw.line((x, HEIGHT, x + (400 - x) // 3, floor), fill=(120, 92, 66), width=2)
    left = rng.randrange(60, 420)
    _window(draw, (left, 70, left + rng.randrange(180, 300), 250), glass=(176, 214, 238))
    return floor


def _lounge(draw, rng, hue: float) -> None:
    _room(draw, rng, hue)
    sofa = _colour(hue + 0.45, 0.42, 0.45)
    left = rng.randrange(80, 260)
    draw.rounded_rectangle((left, 300, left + 380, 450), radius=26, fill=sofa)
    draw.rounded_rectangle((left - 36, 350, left + 20, 480), radius=18, fill=sofa)
    draw.rounded_rectangle((left + 360, 350, left + 416, 480), radius=18, fill=sofa)
    for x in (left + 30, left + 205):
        draw.rounded_rectangle(
            (x, 380, x + 145, 440), radius=14, fill=_colour(hue + 0.45, 0.55, 0.45)
        )
    draw.ellipse((left + 90, 480, left + 300, 540), fill=(122, 86, 58))
    draw.rectangle((690, 200, 700, 470), fill=(70, 70, 76))
    draw.polygon(((660, 150), (730, 150), (755, 215), (635, 215)), fill=(244, 214, 140))


def _bedroom(draw, rng, hue: float) -> None:
    _room(draw, rng, hue)
    left = rng.randrange(150, 300)
    draw.rounded_rectangle((left, 230, left + 360, 400), radius=20, fill=(120, 84, 60))
    draw.rectangle((left - 10, 360, left + 370, 520), fill=(242, 240, 236))
    draw.rectangle((left - 10, 420, left + 370, 520), fill=_colour(hue + 0.55, 0.55, 0.45))
    for x in (left + 35, left + 200):
        draw.rounded_rectangle((x, 320, x + 125, 375), radius=22, fill=(255, 255, 255))
    for x in (left - 100, left + 395):
        draw.rectangle((x, 400, x + 70, 480), fill=(98, 70, 50))
        draw.ellipse((x + 12, 348, x + 58, 400), fill=(246, 218, 150))


def _kitchen(draw, rng, hue: float) -> None:
    _room(draw, rng, hue)
    cabinet = _colour(hue + 0.5, rng.uniform(0.35, 0.65), 0.35)
    for x in range(40, 760, 120):
        draw.rectangle((x, 300, x + 112, 480), fill=cabinet, outline=(60, 60, 60), width=2)
        draw.rectangle((x + 46, 330, x + 66, 340), fill=(230, 230, 230))
    draw.rectangle((30, 286, 770, 304), fill=(80, 80, 86))
    for x in range(480, 760, 140):
        draw.rectangle((x, 60, x + 130, 190), fill=cabinet, outline=(60, 60, 60), width=2)
    for x in (130, 190):
        draw.ellipse((x, 270, x + 46, 290), fill=(30, 30, 34))


def _plot(draw, rng, hue: float) -> None:
    ground = 330
    _sky(draw, rng, ground)
    draw.rectangle((0, ground, WIDTH, HEIGHT), fill=_colour(0.09, rng.uniform(0.5, 0.66), 0.4))
    for x in range(20, 780, rng.randrange(90, 150)):
        height = rng.randrange(30, 90)
        draw.rectangle((x, ground - height, x + 60, ground), fill=_colour(hue, 0.72, 0.2))
    corners = ((190, 380), (620, 380), (730, 540), (90, 540))
    draw.polygon(corners, fill=_colour(0.12, 0.72, 0.5))
    draw.line((*corners, corners[0]), fill=(255, 255, 255), width=6)
    for x, y in corners:
        draw.rectangle((x - 6, y - 52, x + 6, y), fill=(190, 60, 50))
    draw.rectangle((0, 555, WIDTH, HEIGHT), fill=(86, 88, 94))
    for x in range(20, WIDTH, 110):
        draw.rectangle((x, 574, x + 60, 582), fill=(240, 240, 240))
    _tree(draw, rng.randrange(40, 130), 420, 60)


def _shop(draw, rng, hue: float) -> None:
    ground = 500
    _sky(draw, rng, 180)
    draw.rectangle((0, 120, WIDTH, ground), fill=_colour(hue, 0.74, 0.3))
    draw.rectangle((0, ground, WIDTH, HEIGHT), fill=(150, 148, 142))
    draw.rectangle((120, 170, 680, 240), fill=_colour(hue + 0.5, 0.32, 0.55))
    stripe = _colour(hue + 0.1, 0.52, 0.6)
    for index, x in enumerate(range(110, 690, 58)):
        draw.polygon(
            ((x, 260), (x + 58, 260), (x + 68, 320), (x - 10, 320)),
            fill=stripe if index % 2 else (250, 250, 246),
        )
    _window(draw, (140, 330, 420, ground - 20))
    _window(draw, (450, 330, 550, ground - 20))
    draw.rectangle((575, 330, 665, ground), fill=(66, 74, 86))
    for x in range(170, 400, 70):
        draw.rectangle((x, 430, x + 44, 476), fill=_colour(rng.random(), 0.55, 0.6))


def _office(draw, rng, hue: float) -> None:
    _room(draw, rng, hue)
    for x in range(70, 700, 230):
        draw.rectangle((x, 390, x + 180, 408), fill=(150, 112, 80))
        for leg in (x + 8, x + 164):
            draw.rectangle((leg, 408, leg + 8, 520), fill=(84, 84, 90))
        draw.rectangle((x + 50, 322, x + 130, 376), fill=(40, 46, 58))
        draw.rectangle((x + 84, 376, x + 96, 390), fill=(40, 46, 58))
        draw.rounded_rectangle(
            (x + 60, 440, x + 124, 540), radius=14, fill=_colour(hue + 0.5, 0.35, 0.4)
        )


_PAINTERS = {
    "house": _house,
    "tower": _tower,
    "lounge": _lounge,
    "bedroom": _bedroom,
    "kitchen": _kitchen,
    "plot": _plot,
    "shop": _shop,
    "office": _office,
}


@cache
def scene(kind: str, variant: int) -> bytes:
    """One property picture. `kind` is one of `SCENES`."""
    rng = random.Random(f"{kind}-{variant}")  # noqa: S311 - not a secret
    image = Image.new("RGB", (WIDTH, HEIGHT), (230, 230, 230))
    _PAINTERS[kind](ImageDraw.Draw(image), rng, (variant * 0.137) % 1.0)
    return _png(image)


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
