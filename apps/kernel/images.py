"""Image validation and re-encoding (AD-15). The one module that decodes untrusted images
(`kernel.sample_art`, the only other Pillow user, only draws development placeholders).

`process_image(data)` accepts the raw bytes of an upload and returns a freshly encoded JPEG, PNG or
WebP. The output is never the input: the image is decoded, rotated by its EXIF orientation, copied
into a new bitmap (so no EXIF, XMP, ICC profile, comment, GPS or trailing byte can survive),
downscaled so the long side is at most 2,048 px, and encoded again. The type comes from the decoded
format, never from a file name or a declared content type.

Order of checks: byte size (before any decoding), format, pixel limits from the header, then a full
decode. The work runs on the bounded runtime executor with a timeout; a full executor or a timeout
is `Busy`. Rejections are `ImageRejected` with a stable reason code and no decoder text.
"""

import io
import logging
import threading
import warnings
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass

from PIL import Image, ImageCms, ImageOps

from apps.kernel import runtime
from apps.kernel.errors import DomainError

logger = logging.getLogger("estate.media")

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_SIDE = 10_000
MIN_SIDE = 200
MAX_OUTPUT_SIDE = 2_048
JPEG_QUALITY = 85
WEBP_QUALITY = 85
TIMEOUT_SECONDS = 20
BUSY_RETRY_SECONDS = 5

# Set explicitly (never None): Pillow warns above this many pixels and refuses above twice as many.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

ALLOWED_FORMATS = {
    "JPEG": "image/jpeg",
    "MPO": "image/jpeg",  # multi-picture JPEG (phones): the first frame, re-encoded as JPEG
    "PNG": "image/png",
    "WEBP": "image/webp",
}

OPEN_FORMATS = ["JPEG", "PNG", "WEBP"]  # a multi-picture JPEG opens through the JPEG reader

REASONS = frozenset(
    {"too_large", "unsupported_type", "too_large_dimensions", "too_small", "invalid_image"}
)

# The first bytes of formats we recognise and refuse, so a GIF or a PDF is `unsupported_type`
# and random bytes are `invalid_image`.
_REFUSED_MAGIC = (
    b"GIF87a",
    b"GIF89a",
    b"II*\x00",
    b"MM\x00*",
    b"BM",
    b"%PDF",
    b"\x00\x00\x01\x00",
    b"\x89HDF",
    b"<",
)

_decode_slots = threading.BoundedSemaphore(2)
_open_lock = threading.Lock()  # `warnings.catch_warnings` is process-wide; keep its window short


class ImageRejected(DomainError):
    """The upload is not acceptable. `reason` is one of `REASONS`; it is the field code."""

    def __init__(self, reason: str) -> None:
        if reason not in REASONS:
            raise ValueError(reason)
        super().__init__("validation_error")
        self.reason = reason


class Busy(DomainError):
    """No capacity or too slow: retry shortly (`busy`, 503 with `Retry-After`)."""

    def __init__(self, retry_after: int = BUSY_RETRY_SECONDS) -> None:
        super().__init__("busy")
        self.retry_after = retry_after


@dataclass(frozen=True)
class ProcessedImage:
    data: bytes
    content_type: str
    width: int
    height: int


def _looks_like_refused_type(data: bytes) -> bool:
    head = data[:16].lstrip(b"\xef\xbb\xbf \t\r\n")
    if any(head.startswith(magic) for magic in _REFUSED_MAGIC):
        return True
    return data[4:8] == b"ftyp"  # HEIC, AVIF and other ISO media


def _open(data: bytes) -> Image.Image:
    """Open from the header only. Pillow's bomb warning is made an error for this call."""
    try:
        with _open_lock, warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            return Image.open(io.BytesIO(data), formats=OPEN_FORMATS)
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ImageRejected("too_large_dimensions") from None
    except Image.UnidentifiedImageError:
        reason = "unsupported_type" if _looks_like_refused_type(data) else "invalid_image"
        raise ImageRejected(reason) from None
    except MemoryError:
        raise ImageRejected("too_large_dimensions") from None
    except Exception:
        raise ImageRejected("invalid_image") from None


def _check_header(image: Image.Image) -> None:
    if image.format not in ALLOWED_FORMATS:
        raise ImageRejected("unsupported_type")
    if image.format != "MPO":  # a multi-picture JPEG is read as its first frame
        if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1:
            raise ImageRejected("unsupported_type")
    width, height = image.size
    if width > MAX_SIDE or height > MAX_SIDE or width * height > MAX_PIXELS:
        raise ImageRejected("too_large_dimensions")
    if width < MIN_SIDE or height < MIN_SIDE:
        raise ImageRejected("too_small")


def _to_srgb(image: Image.Image) -> Image.Image:
    """Convert from an embedded ICC profile to sRGB when there is one; the profile is dropped."""
    profile = image.info.get("icc_profile")
    if profile and image.mode in ("RGB", "RGBA", "CMYK"):
        try:
            source = ImageCms.ImageCmsProfile(io.BytesIO(profile))
            target = ImageCms.createProfile("sRGB")
            mode = "RGBA" if image.mode == "RGBA" else "RGB"
            converted = ImageCms.profileToProfile(image, source, target, outputMode=mode)
            if converted is not None:
                return converted
        except Exception:
            logger.info("media_icc_skipped", extra={"event": "media_icc_skipped"})
    return image


def _has_alpha(image: Image.Image) -> bool:
    """Real alpha, a palette or colour-key (tRNS) transparency."""
    return image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info


def _to_8_bit(image: Image.Image) -> Image.Image:
    """16-bit (and 32-bit integer) greyscale scaled to 0..255, not clamped."""
    if image.mode.startswith("I"):
        try:
            return image.point(lambda value: value * (1 / 257.0)).convert("L")
        except (ValueError, OSError):
            return image.convert("I").point(lambda value: value * (1 / 257.0)).convert("L")
    return image


def _to_output_mode(image: Image.Image, keep_alpha: bool, transparent: bool) -> Image.Image:
    """RGBA when the output keeps transparency; otherwise RGB with any transparency composited onto
    white. `image` is already RGBA when `transparent`."""
    if keep_alpha:
        return image if image.mode == "RGBA" else image.convert("RGBA")
    if transparent:
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.getchannel("A"))
        return background
    return image if image.mode == "RGB" else image.convert("RGB")


def _fresh(image: Image.Image) -> Image.Image:
    """Strip everything that is not pixels: `info` (EXIF, ICC profile, comment, text chunks,
    transparency keys) is emptied in place, so nothing can be written out from it."""
    image.info = {}
    exif = image.getexif()
    exif.clear()
    return image


def _encode(image: Image.Image, fmt: str) -> bytes:
    out = io.BytesIO()
    if fmt == "JPEG":
        image.save(out, "JPEG", quality=JPEG_QUALITY, progressive=True, optimize=True)
    elif fmt == "PNG":
        image.save(out, "PNG", optimize=True)
    else:
        image.save(out, "WEBP", quality=WEBP_QUALITY)
    return out.getvalue()


def _process(data: bytes) -> ProcessedImage | ImageRejected:
    """Returns a rejection instead of raising it, so an ordinary bad upload is not logged by the
    executor as a failed task."""
    try:
        with _decode_slots:  # at most two decodes at once: a big bitmap is hundreds of MB
            return _decode_and_encode(data)
    except ImageRejected as rejection:
        return rejection
    except MemoryError:
        return ImageRejected("too_large_dimensions")
    except Exception:
        return ImageRejected("invalid_image")  # any odd decoder failure, never a 500


def _decode_and_encode(data: bytes) -> ProcessedImage:
    image = _open(data)
    try:
        _check_header(image)
        fmt = "JPEG" if image.format == "MPO" else image.format
        if fmt == "JPEG":
            image.draft(None, (MAX_OUTPUT_SIDE, MAX_OUTPUT_SIDE))  # cheaper decode of huge photos
        image.load()
        ImageOps.exif_transpose(image, in_place=True)
        alpha = fmt == "PNG" and _has_alpha(image)
        work = _to_8_bit(image) if image.mode.startswith("I") else image
        if work is not image:
            image.close()
        transparent = _has_alpha(work)
        if transparent and work.mode != "RGBA":
            work = work.convert("RGBA")  # applies a palette or colour-key (tRNS) transparency
        work = _to_srgb(work)
        work = _to_output_mode(work, alpha, transparent)
        if max(work.size) > MAX_OUTPUT_SIDE:
            scale = MAX_OUTPUT_SIDE / max(work.size)
            size = (max(1, round(work.width * scale)), max(1, round(work.height * scale)))
            work = work.resize(size, Image.Resampling.LANCZOS)
        work = _fresh(work)
        encoded = _encode(work, fmt)
        return ProcessedImage(encoded, ALLOWED_FORMATS[fmt], work.width, work.height)
    finally:
        image.close()


def process_image(data: bytes) -> ProcessedImage:
    """Validate and re-encode an upload on the runtime executor.

    Raises `ImageRejected` (a reason code, never decoder text) or `Busy` (the executor is full or
    the work took longer than `TIMEOUT_SECONDS`; the caller may retry).
    """
    if not isinstance(data, bytes | bytearray | memoryview):
        raise ImageRejected("invalid_image")
    data = bytes(data)
    if len(data) > MAX_BYTES:  # before any decoding
        raise ImageRejected("too_large")
    if not data:
        raise ImageRejected("invalid_image")
    try:
        future = runtime.submit_heavy(_process, data)
    except runtime.ExecutorBusy:
        raise Busy() from None
    try:
        result = future.result(timeout=TIMEOUT_SECONDS)
    except FutureTimeout:
        future.cancel()
        raise Busy() from None
    if isinstance(result, ImageRejected):
        raise result
    return result
