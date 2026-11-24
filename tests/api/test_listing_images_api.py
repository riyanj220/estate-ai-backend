"""Draft image routes over HTTP (Story 2.9), with Django's CSRF checks on."""

import io
import json
import logging
import threading
import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image

from apps.identity import services as identity_services
from apps.identity.models import Account
from apps.kernel import runtime, storage
from apps.kernel.errors import field_message, get_error
from apps.kernel.models import MediaAsset, RateLimitPolicy, StorageUsage
from apps.kernel.request_logging import JsonFormatter
from apps.listings.models import ListingImage, ListingRevision
from tests.api.test_listings_api import (
    ADMIN_PASSWORD,
    CREATE,
    CSRF,
    detail,
    post,
    sign_in,
    signed_in,
)
from tests.imaging import jpeg_with_metadata, make_image, png_header_only
from tests.listings.support import SYSTEM, seed_cap, seed_media_policies

MB = 1024 * 1024


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


def images_url(listing_id):
    return f"/api/v1/listings/{listing_id}/images"


def image_url(listing_id, image_id):
    return f"/api/v1/listings/{listing_id}/images/{image_id}"


def csrf(client) -> dict:
    return {"HTTP_X_CSRFTOKEN": client.get(CSRF).json()["csrf_token"]}


def upload(client, listing_id, data, name="photo.jpg", declared="image/jpeg", token=True, **parts):
    body = {"file": SimpleUploadedFile(name, data, content_type=declared), **parts}
    extra = csrf(client) if token else {}
    return client.post(images_url(listing_id), body, **extra)


def delete(client, listing_id, image_id, token=True):
    return client.delete(image_url(listing_id, image_id), **(csrf(client) if token else {}))


def new_draft(client):
    return post(client, CREATE).json()


def expected_error(response, code, fields=None):
    """The whole body: the catalogue message, the given field messages and the request id, and
    nothing else (no submitted value can ride along)."""
    return {
        "error": {
            "code": code,
            "message": get_error(code).message,
            "fields": fields or {},
            "request_id": response["X-Request-ID"],
        }
    }


def refused(response, status, code, fields=None):
    assert response.status_code == status, response.content
    assert response.json() == expected_error(response, code, fields)
    assert response["Cache-Control"] == "no-store"


def stored_files(media_root):
    return sorted(p.name for p in media_root.rglob("*") if p.is_file())


JPEG = make_image("JPEG", (1200, 800))
WIRE = {"id", "position", "content_type", "width", "height", "bytes"}


# --- upload ------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["individual", "agency"])
def test_the_owner_uploads_and_gets_the_wire_shape(kind, media_root):
    client = signed_in(account_type=kind)
    listing = new_draft(client)
    response = upload(client, listing["id"], JPEG)
    assert response.status_code == 201
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert set(body) == WIRE
    assert (body["position"], body["content_type"], body["width"], body["height"]) == (
        1,
        "image/jpeg",
        1200,
        800,
    )
    uuid.UUID(body["id"])
    asset = MediaAsset.objects.get()
    assert body["bytes"] == asset.bytes
    # nothing in the response points at the stored file
    assert asset.storage_key not in response.content.decode()
    assert stored_files(media_root) == [asset.storage_key]
    assert ListingImage.objects.get().listing_id == uuid.UUID(listing["id"])


def test_the_listing_revision_moves_and_an_old_editor_gets_a_conflict():
    client = signed_in()
    listing = new_draft(client)
    assert upload(client, listing["id"], JPEG).status_code == 201
    current = client.get(detail(listing["id"])).json()
    assert current["revision"] == 2
    stale = client.patch(
        detail(listing["id"]),
        json.dumps({"base_revision": 1, "fields": {"title": "A fine family house"}}),
        content_type="application/json",
        **csrf(client),
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_revision"
    assert ListingRevision.objects.get(listing_id=listing["id"], revision=2).changes == {
        "images": 1
    }


def test_the_name_and_declared_type_are_ignored_and_never_kept(media_root, caplog):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    caplog.set_level(logging.DEBUG)
    client = signed_in()
    listing = new_draft(client)
    png = make_image("PNG", (500, 400))
    try:
        response = upload(
            client, listing["id"], png, name="holiday-canary-name.php.jpg", declared="text/html"
        )
    finally:
        root.removeHandler(handler)
    assert response.status_code == 201 and response.json()["content_type"] == "image/png"
    asset = MediaAsset.objects.get()
    needles = ["holiday-canary-name", "text/html", asset.storage_key]
    logged = stream.getvalue() + caplog.text
    assert not [n for n in needles if n in logged]
    for model in (MediaAsset, ListingImage, ListingRevision):
        dumped = json.dumps(list(model.objects.values()), default=str)
        assert "holiday-canary-name" not in dumped and "text/html" not in dumped
    assert "holiday-canary-name" not in response.content.decode()


def test_exif_gps_and_comments_never_reach_the_store(media_root):
    client = signed_in()
    listing = new_draft(client)
    assert upload(client, listing["id"], jpeg_with_metadata()).status_code == 201
    stored = storage.get_storage().get(MediaAsset.objects.get().storage_key)
    for needle in (b"Exif", b"Karachi-Clifton-canary", b"fake-comment-canary", b"PK\x03\x04"):
        assert needle not in stored
    assert len(Image.open(io.BytesIO(stored)).getexif()) == 0


# --- 400 ---------------------------------------------------------------------------------------


def svg():
    return b'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"></svg>'


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        pytest.param(b"x" * (5 * MB + 1), "too_large", id="over-5mb"),
        pytest.param(svg(), "unsupported_type", id="svg"),
        pytest.param(
            make_image("GIF", (400, 300), mode="P", color=0), "unsupported_type", id="gif"
        ),
        pytest.param(b"%PDF-1.4 fake", "unsupported_type", id="pdf"),
        pytest.param(png_header_only(60000, 60000), "too_large_dimensions", id="bomb"),
        pytest.param(make_image("PNG", (150, 150)), "too_small", id="small"),
        pytest.param(b"not an image", "invalid_image", id="garbage"),
        pytest.param(make_image("JPEG", (800, 600))[:900], "invalid_image", id="truncated"),
    ],
)
def test_a_bad_image_is_a_validation_error_with_a_static_message(data, reason, media_root):
    client = signed_in()
    listing = new_draft(client)
    response = upload(client, listing["id"], data, name=f"{reason}-secret.jpg")
    refused(response, 400, "validation_error", {"file": [field_message(reason)]})
    assert "secret" not in response.content.decode()
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []
    assert client.get(detail(listing["id"])).json()["revision"] == 1


def test_an_oversize_body_is_too_large_and_nothing_is_parsed(media_root):
    client = signed_in()
    listing = new_draft(client)
    response = upload(client, listing["id"], b"x" * (6 * MB + 10))
    refused(response, 400, "validation_error", {"file": [field_message("too_large")]})
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []


def test_an_oversize_body_without_a_csrf_token_is_forbidden_before_it_is_read():
    client = signed_in()
    listing = new_draft(client)
    response = upload(client, listing["id"], b"x" * (6 * MB + 10), token=False)
    refused(response, 403, "forbidden")


def test_a_body_just_under_the_cap_reaches_the_image_checks():
    client = signed_in()
    listing = new_draft(client)
    response = upload(client, listing["id"], b"x" * (5 * MB + 1000))
    refused(response, 400, "validation_error", {"file": [field_message("too_large")]})


def test_exactly_one_file_part_is_required():
    client = signed_in()
    listing = new_draft(client)
    url = images_url(listing["id"])
    token = csrf(client)
    one = SimpleUploadedFile("a.jpg", JPEG, content_type="image/jpeg")
    other = SimpleUploadedFile("b.jpg", JPEG, content_type="image/jpeg")
    missing = client.post(url, {}, **token)
    refused(missing, 400, "validation_error", {"file": [field_message("required")]})
    two = client.post(url, {"file": [one, other]}, **token)
    refused(two, 400, "validation_error", {"file": [field_message("invalid")]})
    text = client.post(url, {"file": "just text"}, **token)
    refused(text, 400, "validation_error", {"file": [field_message("invalid")]})
    extra_file = client.post(url, {"file": JPEG_FILE(), "second": JPEG_FILE()}, **token)
    refused(
        extra_file, 400, "validation_error", {"non_field_errors": [field_message("unknown_field")]}
    )
    extra_text = client.post(url, {"file": JPEG_FILE(), "caption": "hello"}, **token)
    refused(
        extra_text, 400, "validation_error", {"non_field_errors": [field_message("unknown_field")]}
    )
    assert not MediaAsset.objects.exists()


def JPEG_FILE():
    return SimpleUploadedFile("a.jpg", JPEG, content_type="image/jpeg")


def test_only_multipart_is_accepted():
    client = signed_in()
    listing = new_draft(client)
    response = client.post(
        images_url(listing["id"]),
        json.dumps({"file": "x"}),
        content_type="application/json",
        **csrf(client),
    )
    refused(response, 415, "unsupported_media_type")
    response = client.post(
        images_url(listing["id"]), JPEG, content_type="image/jpeg", **csrf(client)
    )
    refused(response, 415, "unsupported_media_type")


# --- who may -----------------------------------------------------------------------------------


def test_a_visitor_is_401_and_a_missing_csrf_token_is_403():
    owner = signed_in()
    listing = new_draft(owner)
    anonymous = Client(enforce_csrf_checks=True)
    response = anonymous.post(images_url(listing["id"]), {"file": JPEG_FILE()}, **csrf(anonymous))
    refused(response, 401, "unauthenticated")
    refused(upload(owner, listing["id"], JPEG, token=False), 403, "forbidden")
    assert not MediaAsset.objects.exists()


def test_everyone_but_the_owner_gets_the_same_404(media_root):
    owner = signed_in()
    listing = new_draft(owner)
    stranger = signed_in("other.owner@example.com")
    identity_services.provision_admin(SYSTEM, "root.admin@example.com", ADMIN_PASSWORD)
    admin = sign_in("root.admin@example.com", admin=True)
    responses = [
        upload(stranger, listing["id"], JPEG),
        upload(admin, listing["id"], JPEG),
        upload(owner, str(uuid.uuid4()), JPEG),
        upload(owner, "not-a-uuid", JPEG),
    ]
    for response in responses:
        refused(response, 404, "not_found")
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []


@pytest.mark.parametrize(
    "change",
    [{"suspended_at": "now", "suspension_reason": "r"}],
    ids=["suspended"],  # a deactivated account's sessions have ended: 401 (service tests cover it)
)
def test_an_account_that_may_not_edit_is_403(change, media_root):
    from django.utils import timezone

    client = signed_in()
    listing = new_draft(client)
    Account.objects.filter(id=client.account_id).update(
        **{k: (timezone.now() if v == "now" else v) for k, v in change.items()}
    )
    refused(upload(client, listing["id"], JPEG), 403, "forbidden")
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []


def test_other_methods_are_405():
    client = signed_in()
    listing = new_draft(client)
    for method in ("get", "patch", "put"):
        response = getattr(client, method)(images_url(listing["id"]), **csrf(client))
        assert response.status_code == 405 and response.json()["error"]["code"] == (
            "method_not_allowed"
        )
    assert client.post(image_url(listing["id"], uuid.uuid4()), **csrf(client)).status_code == 405


# --- 409, 429, 503 -----------------------------------------------------------------------------


def test_the_eleventh_image_is_409_too_many_images(media_root):
    client = signed_in()
    listing = new_draft(client)
    for _ in range(10):
        assert upload(client, listing["id"], JPEG).status_code == 201
    before = stored_files(media_root)
    refused(upload(client, listing["id"], JPEG), 409, "too_many_images")
    assert stored_files(media_root) == before and MediaAsset.objects.count() == 10


def test_the_storage_cap_is_409(media_root):
    client = signed_in()
    listing = new_draft(client)
    first = upload(client, listing["id"], JPEG).json()
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(
        limit=first["bytes"] + 5
    )
    refused(upload(client, listing["id"], JPEG), 409, "storage_cap_exceeded")
    assert MediaAsset.objects.count() == 1 and len(stored_files(media_root)) == 1


def test_the_upload_rate_limit_is_429_with_retry_after():
    client = signed_in()
    listing = new_draft(client)
    RateLimitPolicy.objects.filter(scope="image_upload").update(limit=2)
    assert upload(client, listing["id"], JPEG).status_code == 201
    assert upload(client, listing["id"], JPEG).status_code == 201
    response = upload(client, listing["id"], JPEG)
    refused(response, 429, "throttled")
    assert int(response["Retry-After"]) >= 1
    assert MediaAsset.objects.count() == 2
    other = signed_in("other.owner@example.com")  # the limit is per account
    other_listing = new_draft(other)
    assert upload(other, other_listing["id"], JPEG).status_code == 201


def test_a_full_executor_is_503_busy_with_retry_after(settings, media_root):
    client = signed_in()
    listing = new_draft(client)
    gate = threading.Event()
    heavy = settings.RUNTIME_EXECUTOR_MAX_WORKERS - settings.RUNTIME_EXECUTOR_RESERVED_WORKERS
    futures = [runtime.submit_heavy(gate.wait, 5) for _ in range(heavy)]
    try:
        response = upload(client, listing["id"], JPEG)
    finally:
        gate.set()
        for future in futures:
            future.result()
    refused(response, 503, "busy")
    assert int(response["Retry-After"]) >= 1
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []
    assert upload(client, listing["id"], JPEG).status_code == 201  # and it recovers


# --- delete ------------------------------------------------------------------------------------


def test_delete_removes_the_image_and_a_repeat_is_404(
    media_root, django_capture_on_commit_callbacks
):
    client = signed_in()
    listing = new_draft(client)
    keep = upload(client, listing["id"], JPEG).json()
    gone = upload(client, listing["id"], JPEG).json()
    with django_capture_on_commit_callbacks(execute=True):
        response = delete(client, listing["id"], gone["id"])
    assert response.status_code == 204 and response.content == b""
    assert response["Cache-Control"] == "no-store"
    assert list(ListingImage.objects.values_list("id", flat=True)) == [uuid.UUID(keep["id"])]
    assert StorageUsage.objects.get().bytes_used == keep["bytes"]
    assert len(stored_files(media_root)) == 1
    assert client.get(detail(listing["id"])).json()["revision"] == 4
    refused(delete(client, listing["id"], gone["id"]), 404, "not_found")
    assert StorageUsage.objects.get().bytes_used == keep["bytes"]


def test_delete_needs_the_owner_and_a_csrf_token():
    owner = signed_in()
    listing = new_draft(owner)
    image = upload(owner, listing["id"], JPEG).json()
    stranger = signed_in("other.owner@example.com")
    refused(delete(stranger, listing["id"], image["id"]), 404, "not_found")
    refused(delete(owner, listing["id"], image["id"], token=False), 403, "forbidden")
    refused(delete(owner, listing["id"], "nope"), 404, "not_found")
    assert ListingImage.objects.count() == 1


def test_delete_by_a_visitor_is_401():
    owner = signed_in()
    listing = new_draft(owner)
    image = upload(owner, listing["id"], JPEG).json()
    anonymous = Client(enforce_csrf_checks=True)
    refused(delete(anonymous, listing["id"], image["id"]), 401, "unauthenticated")


def test_delete_is_forbidden_for_a_suspended_owner():
    from django.utils import timezone

    owner = signed_in()
    listing = new_draft(owner)
    image = upload(owner, listing["id"], JPEG).json()
    Account.objects.filter(id=owner.account_id).update(
        suspended_at=timezone.now(), suspension_reason="r"
    )
    refused(delete(owner, listing["id"], image["id"]), 403, "forbidden")
    assert ListingImage.objects.count() == 1


# --- the contract ------------------------------------------------------------------------------


def test_no_route_serves_a_file():
    client = signed_in()
    listing = new_draft(client)
    image = upload(client, listing["id"], JPEG).json()
    for url in (
        image_url(listing["id"], image["id"]),
        images_url(listing["id"]) + "/" + image["id"] + "/file",
    ):
        response = client.get(url)
        assert response.status_code in (404, 405)
        assert b"image" not in response["Content-Type"].encode()
    assert client.get(detail(listing["id"])).json()["media"] == []


# --- ownership before the body ------------------------------------------------------------------


BAD_BODIES = [
    pytest.param({}, id="no-file"),
    pytest.param({"file": "text", "caption": "x"}, id="bad-parts"),
    pytest.param({"file": SimpleUploadedFile("a.jpg", b"garbage")}, id="garbage"),
]


@pytest.mark.parametrize("body", BAD_BODIES)
def test_a_non_owner_gets_404_whatever_the_body(body):
    owner = signed_in()
    listing = new_draft(owner)
    stranger = signed_in("other.owner@example.com")
    refused(stranger.post(images_url(listing["id"]), body, **csrf(stranger)), 404, "not_found")
    refused(stranger.post(images_url(uuid.uuid4()), body, **csrf(stranger)), 404, "not_found")


@pytest.mark.parametrize("body", BAD_BODIES)
def test_a_suspended_owner_gets_403_whatever_the_body(body):
    from django.utils import timezone

    owner = signed_in()
    listing = new_draft(owner)
    Account.objects.filter(id=owner.account_id).update(
        suspended_at=timezone.now(), suspension_reason="r"
    )
    refused(owner.post(images_url(listing["id"]), body, **csrf(owner)), 403, "forbidden")


def test_an_oversize_declared_body_from_a_non_owner_is_still_checked_for_ownership():
    owner = signed_in()
    listing = new_draft(owner)
    stranger = signed_in("other.owner@example.com")
    refused(upload(stranger, listing["id"], b"x" * (6 * MB + 10)), 404, "not_found")


# --- logs and bodies on the failure paths -------------------------------------------------------


def run_capturing(call, caplog):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    caplog.set_level(logging.DEBUG)
    try:
        response = call()
    finally:
        root.removeHandler(handler)
    return response, stream.getvalue() + caplog.text


DECODER_TEXT = (
    "cannot identify image file",
    "image file is truncated",
    "Traceback",
    "PIL.",
    "Errno",
)


def test_failure_paths_leak_no_name_key_or_decoder_text(caplog, settings):
    client = signed_in()
    listing = new_draft(client)
    first = upload(client, listing["id"], JPEG, name="first-canary-name.jpg").json()
    key = MediaAsset.objects.get().storage_key
    needles = ["canary-name", key, *DECODER_TEXT]
    name = "failing-canary-name.jpg"

    def bad_image():
        return upload(client, listing["id"], b"\xff\xd8\xff" + b"x" * 40, name=name)

    def cap_exceeded():
        RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(
            limit=first["bytes"] + 1
        )
        return upload(client, listing["id"], JPEG, name=name)

    def too_many():
        RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(limit=10**9)
        for _ in range(9):
            upload(client, listing["id"], JPEG)
        return upload(client, listing["id"], JPEG, name=name)

    def throttled():
        RateLimitPolicy.objects.filter(scope="image_upload").update(limit=1)
        return upload(client, listing["id"], JPEG, name=name)

    def busy():
        gate = threading.Event()
        heavy = settings.RUNTIME_EXECUTOR_MAX_WORKERS - settings.RUNTIME_EXECUTOR_RESERVED_WORKERS
        futures = [runtime.submit_heavy(gate.wait, 5) for _ in range(heavy)]
        RateLimitPolicy.objects.filter(scope="image_upload").update(limit=1000)
        try:
            return upload(client, listing["id"], JPEG, name=name)
        finally:
            gate.set()
            for future in futures:
                future.result()

    expected = [
        (bad_image, 400),
        (cap_exceeded, 409),
        (too_many, 409),
        (throttled, 429),
    ]
    for call, status in expected:
        response, logged = run_capturing(call, caplog)
        assert response.status_code == status, (call.__name__, response.content)
        for needle in needles:
            assert needle not in logged and needle not in response.content.decode()
    RateLimitPolicy.objects.filter(scope="image_upload").update(limit=1000)
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(limit=10**9)
    MediaAsset.objects.exclude(storage_key=key).delete()
    ListingImage.objects.exclude(asset_id=MediaAsset.objects.get().id).delete()
    response, logged = run_capturing(busy, caplog)
    assert response.status_code == 503
    for needle in needles:
        assert needle not in logged and needle not in response.content.decode()


# --- an Agency's cap ----------------------------------------------------------------------------


def test_an_agency_has_its_own_larger_cap_over_http():
    agency = signed_in("agency.owner@example.com", account_type="agency")
    individual = signed_in("indie.owner@example.com")
    size = len(make_image("JPEG", (1200, 800)))
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(limit=size)
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_agency").update(limit=3 * size)
    one, two = new_draft(individual), new_draft(agency)
    first = upload(individual, one["id"], JPEG)
    # the stored size is what counts, not the upload's: pin the cap to it
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(
        limit=first.json()["bytes"]
    )
    refused(upload(individual, one["id"], JPEG), 409, "storage_cap_exceeded")
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_agency").update(
        limit=3 * first.json()["bytes"]
    )
    for _ in range(3):
        assert upload(agency, two["id"], JPEG).status_code == 201
    refused(upload(agency, two["id"], JPEG), 409, "storage_cap_exceeded")
