"""The image pipeline: limits, type by content, metadata stripping, bombs, executor behaviour."""

import io
import threading
import time

import pytest
from PIL import Image

from apps.kernel import images, runtime
from apps.kernel.images import Busy, ImageRejected, process_image
from tests.imaging import (
    COMMENT_CANARY,
    GPS_CANARY,
    TRAILER_CANARY,
    gradient,
    jpeg_with_metadata,
    make_image,
    png_header_only,
)


@pytest.fixture(autouse=True)
def fresh_executor():
    runtime.shutdown()
    yield
    runtime.shutdown()


def reason(data):
    with pytest.raises(ImageRejected) as caught:
        process_image(data)
    assert caught.value.code == "validation_error"
    return caught.value.reason


def decoded(result):
    return Image.open(io.BytesIO(result.data))


# --- accepted -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "content_type"), [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")]
)
def test_valid_images_are_reencoded_in_their_own_format(fmt, content_type):
    original = make_image(fmt, (1200, 800))
    result = process_image(original)
    assert result.content_type == content_type
    assert (result.width, result.height) == (1200, 800)
    assert result.data != original  # never the input bytes
    image = decoded(result)
    assert image.format == fmt and image.size == (1200, 800)


def test_the_type_comes_from_the_content_not_the_declared_type():
    # A PNG is a PNG whatever it is called or declared: the pipeline never sees a name.
    assert process_image(make_image("PNG", (400, 300))).content_type == "image/png"


def test_long_side_is_downscaled_to_2048_and_never_upscaled():
    big = process_image(make_image("JPEG", (3000, 2000)))
    assert (big.width, big.height) == (2048, 1365)
    exact = process_image(make_image("JPEG", (2048, 300)))
    assert (exact.width, exact.height) == (2048, 300)
    small = process_image(make_image("JPEG", (300, 200)))
    assert (small.width, small.height) == (300, 200)


def test_png_with_alpha_keeps_alpha_and_png_without_does_not():
    translucent = process_image(make_image("PNG", (400, 300), mode="RGBA", color=(10, 20, 30, 60)))
    image = decoded(translucent)
    assert image.mode == "RGBA" and image.getpixel((5, 5))[3] == 60
    opaque = process_image(make_image("PNG", (400, 300), mode="L", color=90))
    assert decoded(opaque).mode == "RGB"


def test_palette_png_with_transparency_keeps_it():
    palette = Image.new("P", (400, 300), 0)
    palette.info["transparency"] = 0
    out = io.BytesIO()
    palette.save(out, "PNG", transparency=0)
    assert decoded(process_image(out.getvalue())).mode == "RGBA"


def test_webp_with_alpha_is_flattened_to_rgb():
    result = process_image(make_image("WEBP", (400, 300), mode="RGBA", color=(0, 0, 0, 0)))
    image = decoded(result)
    assert image.mode == "RGB"
    assert image.getpixel((5, 5)) == (255, 255, 255)


def test_exif_orientation_is_applied():
    result = process_image(jpeg_with_metadata((640, 400), orientation=6))
    assert (result.width, result.height) == (400, 640)  # turned a quarter
    assert (decoded(result).width, decoded(result).height) == (400, 640)


def test_orientation_applies_before_the_downscale():
    result = process_image(jpeg_with_metadata((3000, 400), orientation=6))
    assert (result.width, result.height) == (273, 2048)


# --- metadata -----------------------------------------------------------------------------------


def test_every_kind_of_metadata_is_gone_from_the_stored_bytes():
    original = jpeg_with_metadata()
    for needle in (b"Exif", GPS_CANARY.encode(), COMMENT_CANARY, TRAILER_CANARY, b"PK\x03\x04"):
        assert needle in original  # the fixture really carries them
    result = process_image(original)
    for needle in (
        b"Exif",
        b"ICC_PROFILE",
        b"XMP",
        GPS_CANARY.encode(),
        COMMENT_CANARY,
        TRAILER_CANARY,
        b"PK\x03\x04",
        b"GPS",
    ):
        assert needle not in result.data
    image = decoded(result)
    assert len(image.getexif()) == 0
    assert not image.getexif().get_ifd(0x8825)
    assert "icc_profile" not in image.info and "comment" not in image.info
    assert "exif" not in image.info and "xmp" not in image.info


CANARY = b"canary-words-meta"


def _icc() -> bytes:
    from PIL import ImageCms

    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _exif_bytes() -> bytes:
    from PIL import ExifTags

    exif = Image.Exif()
    exif[ExifTags.Base.ImageDescription] = CANARY.decode()
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = (24.0, 51.0, 12.0)
    return exif.tobytes()


def _with_metadata(fmt: str) -> bytes:
    from PIL.PngImagePlugin import PngInfo

    out = io.BytesIO()
    picture = gradient((640, 480))
    if fmt == "PNG":
        info = PngInfo()
        info.add_text("Comment", CANARY.decode())
        picture.save(out, "PNG", pnginfo=info, icc_profile=_icc(), exif=_exif_bytes())
    elif fmt == "WEBP":
        xmp = b"<x:xmpmeta>" + CANARY + b"</x:xmpmeta>"
        picture.save(out, "WEBP", exif=_exif_bytes(), xmp=xmp, icc_profile=_icc())
    else:
        picture.save(
            out, "JPEG", exif=_exif_bytes(), icc_profile=_icc(), comment=CANARY, xmp=CANARY
        )
    return out.getvalue()


# the markers and chunk names that carry metadata in each format
CARRIERS = {
    "PNG": [b"iCCP", b"eXIf", b"tEXt", b"zTXt", b"iTXt"],
    "WEBP": [b"EXIF", b"XMP ", b"ICCP"],
    "JPEG": [b"Exif", b"ICC_PROFILE", b"\xff\xfe", b"http://ns.adobe.com/xap"],
}


@pytest.mark.parametrize("fmt", ["PNG", "WEBP", "JPEG"])
def test_metadata_is_stripped_from_every_format(fmt):
    original = _with_metadata(fmt)
    present = [c for c in CARRIERS[fmt] if c in original]
    assert present, "the fixture must really carry metadata"  # the check below can fail
    result = process_image(original)
    assert CANARY not in result.data and b"GPS" not in result.data
    for carrier in CARRIERS[fmt]:
        assert carrier not in result.data, carrier
    image = decoded(result)
    assert len(image.getexif()) == 0
    carried = {"exif", "xmp", "icc_profile", "comment", "Comment", "photoshop", "XML:com.adobe.xmp"}
    assert set(image.info).isdisjoint(carried)


def test_polyglot_jpeg_with_appended_zip_is_stored_clean():
    result = process_image(make_image("JPEG", (400, 300)) + b"PK\x03\x04" + b"zip-canary-words")
    assert b"PK\x03\x04" not in result.data and b"zip-canary-words" not in result.data


# --- rejected -----------------------------------------------------------------------------------


def test_the_size_cap_is_checked_before_decoding(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("decoded an oversize upload")

    monkeypatch.setattr(images.Image, "open", forbidden)
    assert reason(b"x" * (5 * 1024 * 1024 + 1)) == "too_large"


def test_exactly_the_cap_is_not_too_large():
    data = make_image("JPEG", (400, 300))
    assert reason(data + b"\0" * (5 * 1024 * 1024 - len(data) + 1)) == "too_large"
    # at the cap the pipeline goes on to decode (and re-encodes the picture, trailing bytes gone)
    assert process_image(data + b"\0" * (5 * 1024 * 1024 - len(data))).width == 400


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"GIF89a" + b"\0" * 40, id="gif-magic"),
        pytest.param(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', id="svg"),
        pytest.param(b"<!doctype html><html><body>x</body></html>", id="html"),
        pytest.param(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n", id="pdf"),
        pytest.param(b"\x00\x00\x00\x18ftypheic" + b"\0" * 30, id="heic"),
        pytest.param(b"II*\x00" + b"\0" * 40, id="tiff-magic"),
        pytest.param(b"BM" + b"\0" * 40, id="bmp-magic"),
        pytest.param(make_image("GIF", (400, 300), mode="P", color=0), id="real-gif"),
        pytest.param(make_image("TIFF", (400, 300)), id="real-tiff"),
        pytest.param(make_image("BMP", (400, 300)), id="real-bmp"),
        pytest.param(make_image("ICO", (64, 64)), id="real-ico"),
    ],
)
def test_other_types_are_unsupported(data):
    assert reason(data) == "unsupported_type"


def animated(fmt):
    frames = [Image.new("RGB", (400, 300), c) for c in ((255, 0, 0), (0, 255, 0))]
    out = io.BytesIO()
    frames[0].save(out, fmt, save_all=True, append_images=frames[1:], duration=100)
    return out.getvalue()


@pytest.mark.parametrize("fmt", ["PNG", "WEBP"])
def test_animated_images_are_refused(fmt):
    assert reason(animated(fmt)) == "unsupported_type"


@pytest.mark.parametrize(
    "data", [b"", b"\0" * 100, b"not an image at all", b"\xff\xd8\xff" + b"1" * 20]
)
def test_garbage_is_invalid(data):
    assert reason(data) == "invalid_image"


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_truncated_data_is_invalid(fmt):
    out = io.BytesIO()
    gradient((800, 600)).save(out, fmt)
    data = out.getvalue()
    assert reason(data[: len(data) // 2]) == "invalid_image"


def test_a_non_bytes_value_is_invalid():
    assert reason("text") == "invalid_image"
    assert reason(None) == "invalid_image"


@pytest.mark.parametrize(
    "size",
    [(6400, 6400), (10001, 300), (300, 10001), (20000, 20000)],
    ids=["41mp", "wide", "tall", "bomb"],
)
def test_too_many_pixels_or_too_long_a_side(size):
    data = png_header_only(*size)
    assert len(data) < 200
    assert reason(data) == "too_large_dimensions"


def test_a_real_41_megapixel_jpeg_is_refused():
    data = make_image("JPEG", (6400, 6400), color=(9, 9, 9))
    assert len(data) < 5 * 1024 * 1024
    assert reason(data) == "too_large_dimensions"


def test_a_decompression_bomb_is_refused_without_decoding(monkeypatch):
    def forbidden(self, *args, **kwargs):
        raise AssertionError("decoded a bomb")

    monkeypatch.setattr(Image.Image, "load", forbidden)
    assert reason(png_header_only(60000, 60000)) == "too_large_dimensions"


def test_the_pixel_limit_is_set_explicitly():
    assert Image.MAX_IMAGE_PIXELS == 40_000_000


@pytest.mark.parametrize("size", [(199, 400), (400, 199), (100, 100), (1, 1)])
def test_under_200_pixels_is_too_small(size):
    assert reason(make_image("PNG", size)) == "too_small"


def test_the_smallest_accepted_size_and_the_largest_side():
    assert process_image(make_image("PNG", (200, 200))).width == 200
    assert process_image(make_image("PNG", (10000, 200))).width == 2048


# --- no leakage ---------------------------------------------------------------------------------


def test_a_rejection_carries_no_decoder_text():
    with pytest.raises(ImageRejected) as caught:
        process_image(b"\xff\xd8\xff" + b"secret-name.jpg" * 3)
    assert caught.value.args == ("validation_error",)
    assert "secret-name" not in repr(caught.value) and caught.value.__cause__ is None


def test_an_unexpected_decoder_failure_is_invalid_and_quiet(monkeypatch):
    def explode(self, *args, **kwargs):
        raise RuntimeError("decoder said: secret-name.jpg")

    data = make_image("JPEG", (400, 300))
    monkeypatch.setattr(Image.Image, "load", explode)
    with pytest.raises(ImageRejected) as caught:
        process_image(data)
    assert caught.value.reason == "invalid_image"
    assert "secret-name" not in repr(caught.value) and caught.value.__cause__ is None


# --- the executor -------------------------------------------------------------------------------


def test_the_work_runs_on_a_runtime_worker_not_the_calling_thread(monkeypatch):
    seen = []
    real = images._process

    def spy(data):
        seen.append(threading.current_thread().name)
        return real(data)

    monkeypatch.setattr(images, "_process", spy)
    process_image(make_image("JPEG", (400, 300)))
    assert seen and seen[0].startswith("estate-runtime")


def test_a_full_executor_is_busy_with_a_retry_hint(settings):
    gate = threading.Event()
    heavy = settings.RUNTIME_EXECUTOR_MAX_WORKERS - settings.RUNTIME_EXECUTOR_RESERVED_WORKERS
    futures = [runtime.submit_heavy(gate.wait, 5) for _ in range(heavy)]
    try:
        with pytest.raises(Busy) as caught:
            process_image(make_image("JPEG", (400, 300)))
        assert caught.value.code == "busy" and caught.value.retry_after > 0
    finally:
        gate.set()
        for f in futures:
            f.result()


def test_a_slow_job_times_out_as_busy(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(images, "TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(images, "_process", lambda data: release.wait(5))
    started = time.monotonic()
    try:
        with pytest.raises(Busy):
            process_image(make_image("JPEG", (400, 300)))
    finally:
        release.set()
    assert time.monotonic() - started < 3


# --- the catalogue and the docs ---------------------------------------------------------------


def test_every_reason_has_a_static_message_and_is_documented():
    from pathlib import Path

    from apps.kernel.errors import FIELD_MESSAGES, error_codes

    docs = (Path(__file__).resolve().parents[2] / "docs" / "media.md").read_text()
    for reason_code in images.REASONS:
        assert FIELD_MESSAGES[reason_code]
        assert f"`{reason_code}`" in docs
    for code in ("storage_cap_exceeded", "too_many_images", "busy"):
        assert code in error_codes()


def test_the_limits_in_the_docs_are_the_limits_in_the_code():
    from pathlib import Path

    docs = (Path(__file__).resolve().parents[2] / "docs" / "media.md").read_text()
    assert images.MAX_BYTES == 5_242_880 and "5,242,880" in docs
    assert (images.MAX_PIXELS, images.MAX_SIDE, images.MIN_SIDE) == (40_000_000, 10_000, 200)
    assert images.MAX_OUTPUT_SIDE == 2_048 and "2,048" in docs
    assert images.TIMEOUT_SECONDS == 20 and "20 seconds" in docs


# --- modes and formats -------------------------------------------------------------------------


def _png(mode, value, size=(300, 300), **save):
    out = io.BytesIO()
    Image.new(mode, size, value).save(out, "PNG", **save)
    return out.getvalue()


@pytest.mark.parametrize(
    ("value", "expected"), [(60000, 233), (30000, 116), (257, 1), (65535, 255)]
)
def test_16_bit_greyscale_is_scaled_not_clamped(value, expected):
    result = process_image(_png("I;16", value))
    pixel = decoded(result).convert("RGB").getpixel((5, 5))
    assert pixel == (expected,) * 3


def test_a_multi_picture_jpeg_is_accepted_as_its_first_frame():
    first = Image.new("RGB", (400, 300), (200, 10, 10))
    second = Image.new("RGB", (400, 300), (10, 10, 200))
    out = io.BytesIO()
    first.save(out, "MPO", save_all=True, append_images=[second])
    opened = Image.open(io.BytesIO(out.getvalue()))
    assert opened.format == "MPO" and opened.n_frames == 2  # the fixture really is one
    result = process_image(out.getvalue())
    image = decoded(result)
    assert (result.content_type, image.format, image.size) == ("image/jpeg", "JPEG", (400, 300))
    red, _, blue = image.convert("RGB").getpixel((5, 5))
    assert red > 150 and blue < 80  # frame 0


@pytest.mark.parametrize(
    ("mode", "value", "key"),
    [("L", 5, 5), ("RGB", (1, 2, 3), (1, 2, 3)), ("P", 0, 0)],
    ids=["grey-trns", "rgb-trns", "palette-trns"],
)
def test_colour_key_transparency_keeps_alpha_in_a_png(mode, value, key):
    image = Image.new(mode, (300, 300), value)
    out = io.BytesIO()
    image.save(out, "PNG", transparency=key)
    result = process_image(out.getvalue())
    stored = decoded(result)
    assert (result.content_type, stored.mode) == ("image/png", "RGBA")
    assert stored.getpixel((5, 5))[3] == 0


def test_a_png_without_transparency_stays_opaque_rgb():
    assert decoded(process_image(_png("RGB", (1, 2, 3)))).mode == "RGB"


# --- odd failures and memory --------------------------------------------------------------------


@pytest.mark.parametrize("name", ["_check_header", "_to_srgb", "_fresh", "_encode"])
def test_any_odd_exception_is_invalid_image_never_a_500(monkeypatch, name):
    data = make_image("JPEG", (400, 300))

    def boom(*args, **kwargs):
        raise RuntimeError("odd: secret-name.jpg")

    monkeypatch.setattr(images, name, boom)
    with pytest.raises(ImageRejected) as caught:
        process_image(data)
    assert caught.value.reason == "invalid_image" and "secret" not in repr(caught.value)


def test_a_header_that_raises_on_n_frames_is_invalid(monkeypatch):
    data = make_image("PNG", (400, 300))

    class Odd(Image.Image):
        pass

    real = images._check_header

    def odd(image):
        image.__class__ = type("Odd", (type(image),), {"n_frames": property(lambda s: 1 / 0)})
        return real(image)

    monkeypatch.setattr(images, "_check_header", odd)
    assert reason(data) == "invalid_image"


def test_at_most_two_decodes_run_at_once(monkeypatch):
    active, peak, lock = 0, 0, threading.Lock()
    real = images._decode_and_encode

    def counted(data):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.2)
        try:
            return real(data)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(images, "_decode_and_encode", counted)
    data = make_image("JPEG", (400, 300))
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(process_image(data))) for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 3 and peak == 2
