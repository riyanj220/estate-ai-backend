"""Small generated images for the media tests (no binary fixtures are checked in)."""

import io
import struct
import zlib

from PIL import ExifTags, Image, ImageCms


def make_image(fmt="JPEG", size=(1200, 800), color=(180, 60, 40), mode="RGB", **save) -> bytes:
    image = Image.new(mode, size, color)
    out = io.BytesIO()
    image.save(out, fmt, **save)
    return out.getvalue()


def gradient(size=(640, 480)) -> Image.Image:
    """A non-uniform picture, so re-encoded sizes are realistic and rotations are detectable."""
    image = Image.new("RGB", size)
    width, height = size
    pixels = image.load()
    for x in range(width):
        for y in range(0, height):
            pixels[x, y] = (x * 255 // width, y * 255 // height, (x + y) % 256)
    return image


def png_header_only(width: int, height: int) -> bytes:
    """A PNG that announces `width` x `height` with almost no data: a decompression bomb."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00"))
        + chunk(b"IEND", b"")
    )


GPS_CANARY = "Karachi-Clifton-canary"
COMMENT_CANARY = b"fake-comment-canary"
TRAILER_CANARY = b"fake-trailer-canary"


def jpeg_with_metadata(size=(640, 480), orientation=None) -> bytes:
    """A JPEG carrying EXIF (GPS, description, orientation), a comment, an ICC profile and a ZIP
    appended after the image."""
    exif = Image.Exif()
    exif[ExifTags.Base.ImageDescription] = GPS_CANARY
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = (24.0, 51.0, 12.0)
    gps[ExifTags.GPS.GPSLongitudeRef] = "E"
    gps[ExifTags.GPS.GPSLongitude] = (67.0, 1.0, 30.0)
    if orientation is not None:
        exif[ExifTags.Base.Orientation] = orientation
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    out = io.BytesIO()
    gradient(size).save(out, "JPEG", exif=exif, comment=COMMENT_CANARY, icc_profile=profile)
    return out.getvalue() + b"PK\x03\x04" + TRAILER_CANARY
