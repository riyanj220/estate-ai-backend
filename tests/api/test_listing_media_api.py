"""Reorder-and-cover and the media endpoint over HTTP (Story 2.10), Django's CSRF checks on."""

import io
import json
import logging
import uuid

import pytest
from django.db import connection
from django.test import Client
from django.utils import timezone
from PIL import Image

from apps.identity import services as identity_services
from apps.identity.models import Account
from apps.kernel import runtime, storage
from apps.kernel.errors import field_message
from apps.kernel.models import MediaAsset, RateLimitPolicy
from apps.kernel.request_logging import JsonFormatter
from apps.listings.models import ListingImage, ListingRevision, PropertyListing
from tests.api.test_listing_images_api import (
    csrf,
    expected_error,
    images_url,
    new_draft,
    refused,
    upload,
)
from tests.api.test_listings_api import ADMIN_PASSWORD, WIRE_KEYS, detail, sign_in, signed_in
from tests.imaging import (
    COMMENT_CANARY,
    GPS_CANARY,
    TRAILER_CANARY,
    jpeg_with_metadata,
    make_image,
)
from tests.listings.support import SYSTEM, seed_cap, seed_media_policies

PNG = make_image("PNG", (500, 400))
JPEG = make_image("JPEG", (1200, 800))
ITEM_KEYS = {"id", "position", "is_cover", "content_type", "width", "height", "bytes", "url"}


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


def file_url(listing_id, image_id):
    return f"/api/v1/listings/{listing_id}/images/{image_id}/file"


def patch(client, listing_id, body, token=True):
    extra = csrf(client) if token else {}
    data = body if isinstance(body, str | bytes) else json.dumps(body)
    return client.patch(images_url(listing_id), data, content_type="application/json", **extra)


def with_images(client, count=3, data=PNG):
    listing = new_draft(client)
    made = [upload(client, listing["id"], data).json() for _ in range(count)]
    return listing, made


def current(client, listing_id):
    return client.get(detail(listing_id)).json()


def ids(listing):
    return [item["id"] for item in listing["media"]]


# --- the Listing resource ----------------------------------------------------------------------


def test_the_listing_resource_lists_the_media_in_order_with_relative_urls():
    client = signed_in()
    empty = new_draft(client)
    assert empty["media"] == []
    listing, (a, b) = with_images(client, 2)
    body = current(client, listing["id"])
    assert set(body) == WIRE_KEYS
    assert [(m["position"], m["is_cover"]) for m in body["media"]] == [(0, True), (1, False)]
    for item, made in zip(body["media"], (a, b), strict=True):
        assert set(item) == ITEM_KEYS
        assert item == made  # the upload response has the same shape
        assert item["url"] == file_url(listing["id"], item["id"])
        assert item["url"].startswith("/") and "://" not in item["url"]


def test_the_wire_never_carries_asset_ids_keys_or_the_owner():
    client = signed_in()
    listing, _ = with_images(client, 2)
    text = json.dumps(current(client, listing["id"]))
    for needle in (
        str(client.account_id),
        *(asset.storage_key for asset in MediaAsset.objects.all()),
        *(str(asset.id) for asset in MediaAsset.objects.all()),
    ):
        assert needle not in text


def test_saving_other_fields_is_unaffected_by_images():
    client = signed_in()
    listing, _ = with_images(client, 2)
    base = current(client, listing["id"])
    response = client.patch(
        detail(listing["id"]),
        json.dumps({"base_revision": base["revision"], "fields": {"title": "A fine family house"}}),
        content_type="application/json",
        **csrf(client),
    )
    assert response.status_code == 200
    assert response.json()["media"] == base["media"]


# --- PATCH .../images --------------------------------------------------------------------------


def test_reorder_returns_the_listing_and_moves_the_revision_once():
    client = signed_in()
    listing, (a, b, c) = with_images(client, 3)
    before = current(client, listing["id"])
    response = patch(
        client,
        listing["id"],
        {"base_revision": before["revision"], "order": [c["id"], a["id"], b["id"]]},
    )
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert set(body) == WIRE_KEYS
    assert ids(body) == [c["id"], a["id"], b["id"]]
    assert [m["position"] for m in body["media"]] == [0, 1, 2]
    assert [m["is_cover"] for m in body["media"]] == [False, True, False]
    assert body["revision"] == before["revision"] + 1
    assert body == current(client, listing["id"])
    assert ListingRevision.objects.get(
        listing_id=listing["id"], revision=body["revision"]
    ).changes == {
        "images": 3,
        "order": True,
        "cover": False,
    }


def test_choosing_a_cover_and_the_null_cover_keeps_it():
    client = signed_in()
    listing, (a, b) = with_images(client, 2)
    first = patch(
        client,
        listing["id"],
        {"base_revision": 3, "order": [a["id"], b["id"]], "cover_id": b["id"]},
    )
    assert [m["is_cover"] for m in first.json()["media"]] == [False, True]
    kept = patch(
        client,
        listing["id"],
        {"base_revision": 4, "order": [b["id"], a["id"]], "cover_id": None},
    )
    assert ids(kept.json()) == [b["id"], a["id"]]
    assert [m["is_cover"] for m in kept.json()["media"]] == [True, False]
    omitted = patch(client, listing["id"], {"base_revision": 5, "order": [a["id"], b["id"]]})
    assert [(m["id"], m["is_cover"]) for m in omitted.json()["media"]] == [
        (a["id"], False),
        (b["id"], True),
    ]


def test_a_request_that_changes_nothing_is_200_with_no_new_revision():
    client = signed_in()
    listing, (a, b) = with_images(client, 2)
    before, rows = current(client, listing["id"]), ListingRevision.objects.count()
    response = patch(client, listing["id"], {"base_revision": 3, "order": [a["id"], b["id"]]})
    assert response.status_code == 200 and response.json() == before
    assert ListingRevision.objects.count() == rows


def test_an_upload_and_a_delete_still_bump_the_revision_and_keep_the_cover_rules():
    client = signed_in()
    listing, (a, b) = with_images(client, 2)
    assert current(client, listing["id"])["revision"] == 3
    gone = client.delete(f"/api/v1/listings/{listing['id']}/images/{a['id']}", **csrf(client))
    assert gone.status_code == 204
    after = current(client, listing["id"])
    assert after["revision"] == 4
    assert [(m["id"], m["position"], m["is_cover"]) for m in after["media"]] == [(b["id"], 0, True)]


def test_the_order_edit_is_not_counted_by_the_upload_rate_limit():
    client = signed_in()
    RateLimitPolicy.objects.filter(scope="image_upload").update(limit=2)
    listing, (a, b) = with_images(client, 2)
    assert upload(client, listing["id"], PNG).status_code == 429
    revision = current(client, listing["id"])["revision"]
    for turn, order in enumerate(([b["id"], a["id"]], [a["id"], b["id"]], [b["id"], a["id"]])):
        response = patch(client, listing["id"], {"base_revision": revision + turn, "order": order})
        assert response.status_code == 200


@pytest.mark.parametrize(
    ("mutate", "fields"),
    [
        pytest.param(lambda a, b: {"order": [a]}, {"order": "invalid"}, id="missing"),
        pytest.param(lambda a, b: {"order": [a, a]}, {"order": "invalid"}, id="duplicate"),
        pytest.param(
            lambda a, b: {"order": [a, b, str(uuid.uuid4())]}, {"order": "invalid"}, id="extra"
        ),
        pytest.param(
            lambda a, b: {"order": [a, str(uuid.uuid4())]}, {"order": "invalid"}, id="unknown"
        ),
        pytest.param(lambda a, b: {"order": "secret-canary"}, {"order": "invalid"}, id="text"),
        pytest.param(lambda a, b: {"order": [a, "secret-canary"]}, {"order": "invalid"}, id="junk"),
        pytest.param(lambda a, b: {"order": [a, 7]}, {"order": "invalid"}, id="number"),
        pytest.param(lambda a, b: {"order": None}, {"order": "required"}, id="null-order"),
        pytest.param(
            lambda a, b: {"order": [a, b], "cover_id": str(uuid.uuid4())},
            {"cover_id": "invalid"},
            id="foreign-cover",
        ),
        pytest.param(
            lambda a, b: {"order": [a, b], "cover_id": "secret-canary"},
            {"cover_id": "invalid"},
            id="junk-cover",
        ),
        pytest.param(
            lambda a, b: {"order": [a, b], "cover_id": 5}, {"cover_id": "invalid"}, id="n"
        ),
        pytest.param(
            lambda a, b: {"order": [a, b], "base_revision": 0},
            {"base_revision": "invalid"},
            id="zero",
        ),
        pytest.param(
            lambda a, b: {"order": [a, b], "base_revision": "3"},
            {"base_revision": "invalid"},
            id="text-base",
        ),
        pytest.param(
            lambda a, b: {"order": [a, b], "base_revision": None},
            {"base_revision": "required"},
            id="null-base",
        ),
    ],
)
def test_a_bad_body_is_a_field_error_with_static_messages_and_writes_nothing(mutate, fields):
    client = signed_in()
    listing, (a, b) = with_images(client, 2)
    before = current(client, listing["id"])
    body = {"base_revision": before["revision"], **mutate(a["id"], b["id"])}
    response = patch(client, listing["id"], body)
    refused(response, 400, "validation_error", {k: [field_message(v)] for k, v in fields.items()})
    assert "secret-canary" not in response.content.decode()
    assert current(client, listing["id"]) == before


def test_a_missing_base_revision_or_order_is_required():
    client = signed_in()
    listing, (a, b) = with_images(client, 2)
    refused(
        patch(client, listing["id"], {"order": [a["id"], b["id"]]}),
        400,
        "validation_error",
        {"base_revision": [field_message("required")]},
    )
    refused(
        patch(client, listing["id"], {"base_revision": 3}),
        400,
        "validation_error",
        {"order": [field_message("required")]},
    )


@pytest.mark.parametrize(
    "body",
    [
        {"base_revision": 3, "order": [], "origin": "ai"},
        {"base_revision": 3, "order": [], "owner_account_id": "x"},
        {"base_revision": 3, "order": [], "images": []},
    ],
)
def test_other_keys_are_unknown_fields(body):
    client = signed_in()
    listing, _ = with_images(client, 2)
    refused(
        patch(client, listing["id"], body),
        400,
        "validation_error",
        {"non_field_errors": [field_message("unknown_field")]},
    )


@pytest.mark.parametrize("raw", ["[]", '"x"', "5", "null"])
def test_a_body_that_is_not_an_object_is_refused(raw):
    client = signed_in()
    listing, _ = with_images(client, 1)
    response = patch(client, listing["id"], raw)
    assert response.status_code == 400 and response.json()["error"]["code"] == "validation_error"


def test_a_stale_revision_is_409_with_the_current_listing_and_nothing_written():
    client = signed_in()
    listing, (a, b) = with_images(client, 2)
    old = current(client, listing["id"])["revision"]
    assert (
        patch(
            client, listing["id"], {"base_revision": old, "order": [b["id"], a["id"]]}
        ).status_code
        == 200
    )
    now = current(client, listing["id"])
    rows = ListingRevision.objects.count()
    # the replay of the earlier request and an old base with a different order are both stale
    for order in ([b["id"], a["id"]], [a["id"], b["id"]]):
        response = patch(client, listing["id"], {"base_revision": old, "order": order})
        assert response.status_code == 409
        assert response["Cache-Control"] == "no-store"
        assert response.json() == {**expected_error(response, "stale_revision"), "current": now}
    assert current(client, listing["id"]) == now and ListingRevision.objects.count() == rows


def test_a_field_edit_makes_a_reorder_with_the_old_base_stale_and_the_other_way_round():
    client = signed_in()
    listing, (a, b) = with_images(client, 2)
    base = current(client, listing["id"])["revision"]
    edit = client.patch(
        detail(listing["id"]),
        json.dumps({"base_revision": base, "fields": {"title": "A fine family house"}}),
        content_type="application/json",
        **csrf(client),
    )
    assert edit.status_code == 200
    stale = patch(client, listing["id"], {"base_revision": base, "order": [b["id"], a["id"]]})
    assert stale.status_code == 409 and ids(stale.json()["current"]) == [a["id"], b["id"]]
    reorder = patch(client, listing["id"], {"base_revision": base + 1, "order": [b["id"], a["id"]]})
    assert reorder.status_code == 200
    stale_edit = client.patch(
        detail(listing["id"]),
        json.dumps({"base_revision": base + 1, "fields": {"title": "Another fine house"}}),
        content_type="application/json",
        **csrf(client),
    )
    assert stale_edit.status_code == 409
    assert ids(stale_edit.json()["current"]) == [b["id"], a["id"]]  # the editor reloads media too


def test_another_listings_image_is_never_accepted():
    client = signed_in()
    one, (a, b) = with_images(client, 2)
    two, (foreign,) = with_images(client, 1)
    response = patch(client, one["id"], {"base_revision": 3, "order": [a["id"], foreign["id"]]})
    refused(response, 400, "validation_error", {"order": [field_message("invalid")]})
    response = patch(
        client,
        one["id"],
        {"base_revision": 3, "order": [a["id"], b["id"]], "cover_id": foreign["id"]},
    )
    refused(response, 400, "validation_error", {"cover_id": [field_message("invalid")]})
    assert ids(current(client, two["id"])) == [foreign["id"]]


# --- who may -----------------------------------------------------------------------------------


def test_a_visitor_is_401_and_a_missing_csrf_token_is_403():
    owner = signed_in()
    listing, (a,) = with_images(owner, 1)
    anonymous = Client(enforce_csrf_checks=True)
    refused(
        patch(anonymous, listing["id"], {"base_revision": 2, "order": [a["id"]]}),
        401,
        "unauthenticated",
    )
    refused(
        patch(owner, listing["id"], {"base_revision": 2, "order": [a["id"]]}, token=False),
        403,
        "forbidden",
    )


BAD_BODIES = [
    pytest.param({}, id="empty"),
    pytest.param({"base_revision": "x", "order": "y", "cover_id": 5}, id="bad-values"),
    pytest.param({"nonsense": True}, id="unknown-key"),
]


@pytest.mark.parametrize("body", BAD_BODIES)
def test_everyone_but_the_owner_gets_the_same_404_whatever_the_body(body):
    owner = signed_in()
    listing, (a,) = with_images(owner, 1)
    stranger = signed_in("other.owner@example.com")
    identity_services.provision_admin(SYSTEM, "root.admin@example.com", ADMIN_PASSWORD)
    admin = sign_in("root.admin@example.com", admin=True)
    for client, target in (
        (stranger, listing["id"]),
        (admin, listing["id"]),
        (owner, str(uuid.uuid4())),
        (owner, "not-a-uuid"),
    ):
        refused(patch(client, target, body), 404, "not_found")
    refused(
        patch(stranger, listing["id"], {"base_revision": 2, "order": [a["id"]]}), 404, "not_found"
    )
    assert ids(current(owner, listing["id"])) == [a["id"]]


@pytest.mark.parametrize("body", BAD_BODIES)
def test_a_suspended_owner_gets_403_whatever_the_body(body):
    owner = signed_in()
    listing, (a,) = with_images(owner, 1)
    Account.objects.filter(id=owner.account_id).update(
        suspended_at=timezone.now(), suspension_reason="r"
    )
    refused(patch(owner, listing["id"], body), 403, "forbidden")
    refused(patch(owner, listing["id"], {"base_revision": 2, "order": [a["id"]]}), 403, "forbidden")


def test_an_unverified_owner_may_reorder():
    owner = signed_in("fresh.owner@example.com", state="unverified")
    listing, (a, b) = with_images(owner, 2)
    response = patch(owner, listing["id"], {"base_revision": 3, "order": [b["id"], a["id"]]})
    assert response.status_code == 200


def test_the_images_route_allows_only_post_and_patch():
    client = signed_in()
    listing = new_draft(client)
    for method in ("get", "put", "delete"):
        response = getattr(client, method)(images_url(listing["id"]), **csrf(client))
        assert response.status_code == 405


def test_the_order_body_must_be_json():
    client = signed_in()
    listing, (a,) = with_images(client, 1)
    response = client.patch(
        images_url(listing["id"]), "base_revision=2", content_type="text/plain", **csrf(client)
    )
    refused(response, 415, "unsupported_media_type")


# --- the media endpoint ------------------------------------------------------------------------


def stored_bytes(image_id) -> bytes:
    link = ListingImage.objects.get(pk=image_id)
    return storage.get_storage().get(MediaAsset.objects.get(pk=link.asset_id).storage_key)


def test_the_owner_gets_the_stored_bytes_with_the_headers():
    client = signed_in()
    listing, (a,) = with_images(client, 1, JPEG)
    response = client.get(a["url"])
    assert response.status_code == 200
    assert response.content == stored_bytes(a["id"])  # byte for byte what was stored
    assert response.content != JPEG  # the re-encoded file, never the upload
    assert response["Content-Type"] == "image/jpeg" == a["content_type"]
    assert response["Content-Length"] == str(len(response.content)) == str(a["bytes"])
    assert response["Cache-Control"] == "private, no-store"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Content-Disposition"] == "inline"  # no file name
    assert response["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert not response.cookies  # no cookie is set
    assert not response.has_header("ETag") and not response.has_header("Set-Cookie")
    assert not response.has_header("Accept-Ranges")
    assert Image.open(io.BytesIO(response.content)).size == (1200, 800)


def test_a_png_is_served_as_a_png():
    client = signed_in()
    listing, (a,) = with_images(client, 1, PNG)
    response = client.get(a["url"])
    assert response["Content-Type"] == "image/png" and response.content == stored_bytes(a["id"])


def test_the_served_bytes_carry_no_metadata():
    client = signed_in()
    listing = new_draft(client)
    made = upload(client, listing["id"], jpeg_with_metadata()).json()
    served = client.get(made["url"]).content
    for needle in (b"Exif", GPS_CANARY.encode(), COMMENT_CANARY, TRAILER_CANARY, b"PK\x03\x04"):
        assert needle not in served
    assert len(Image.open(io.BytesIO(served)).getexif()) == 0


def test_head_answers_the_headers_without_a_body():
    client = signed_in()
    listing, (a,) = with_images(client, 1, JPEG)
    response = client.head(a["url"])
    assert response.status_code == 200 and response.content == b""
    assert response["Content-Length"] == str(a["bytes"])
    assert response["Content-Type"] == "image/jpeg"
    assert response["Cache-Control"] == "private, no-store"
    assert response["Content-Security-Policy"] == "default-src 'none'; sandbox"
    anonymous = Client()
    assert anonymous.head(a["url"]).status_code == 404


def test_range_and_conditional_requests_are_ignored_and_accept_does_not_matter():
    client = signed_in()
    listing, (a,) = with_images(client, 1, JPEG)
    full = client.get(a["url"]).content
    ranged = client.get(a["url"], HTTP_RANGE="bytes=0-9")
    assert ranged.status_code == 200 and ranged.content == full
    assert not ranged.has_header("Content-Range")
    conditional = client.get(
        a["url"],
        HTTP_IF_NONE_MATCH='"anything"',
        HTTP_IF_MODIFIED_SINCE="Wed, 21 Oct 2099 07:28:00 GMT",
    )
    assert conditional.status_code == 200 and conditional.content == full
    for accept in ("application/json", "text/html", "image/png", "*/*"):
        response = client.get(a["url"], HTTP_ACCEPT=accept)
        assert response.status_code == 200 and response.content == full


def test_the_order_decides_nothing_about_which_file_a_url_serves():
    client = signed_in()
    listing = new_draft(client)
    first = upload(client, listing["id"], make_image("PNG", (500, 400), (10, 20, 30))).json()
    second = upload(client, listing["id"], make_image("PNG", (600, 400), (200, 20, 30))).json()
    before = (client.get(first["url"]).content, client.get(second["url"]).content)
    patch(client, listing["id"], {"base_revision": 3, "order": [second["id"], first["id"]]})
    assert (client.get(first["url"]).content, client.get(second["url"]).content) == before
    assert before[0] != before[1]


def assert_not_found(response):
    assert response.status_code == 404, response.content
    assert response["Content-Type"].startswith("application/json")
    assert response.json() == expected_error(response, "not_found")
    assert response["Cache-Control"] == "no-store"
    assert not response.cookies


def test_everyone_but_the_owner_gets_the_identical_404():
    owner = signed_in()
    listing, (a,) = with_images(owner, 1)
    other_listing, (foreign,) = with_images(owner, 1)
    stranger = signed_in("other.owner@example.com")
    identity_services.provision_admin(SYSTEM, "root.admin@example.com", ADMIN_PASSWORD)
    admin = sign_in("root.admin@example.com", admin=True)
    assert owner.get(a["url"]).status_code == 200
    responses = [
        stranger.get(a["url"]),
        admin.get(a["url"]),
        Client().get(a["url"]),  # a Visitor: 404, never 401
        owner.get(file_url(listing["id"], uuid.uuid4())),
        owner.get(file_url(uuid.uuid4(), a["id"])),
        owner.get(file_url(listing["id"], foreign["id"])),  # another Listing's image
        owner.get(file_url(other_listing["id"], a["id"])),
        owner.get(file_url("not-a-uuid", a["id"])),
        owner.get(file_url(listing["id"], "not-a-uuid")),
        owner.get(file_url(listing["id"], a["id"].upper())),
        owner.get(file_url(listing["id"], a["id"].replace("-", ""))),
    ]
    bodies = []
    for response in responses:
        assert_not_found(response)
        bodies.append({k: v for k, v in response.json()["error"].items() if k != "request_id"})
    assert all(body == bodies[0] for body in bodies)  # identical, whoever asks and whatever for


def test_a_suspended_owner_and_an_unverified_owner_may_view_their_own_images():
    suspended = signed_in()
    listing, (a,) = with_images(suspended, 1)
    Account.objects.filter(id=suspended.account_id).update(
        suspended_at=timezone.now(), suspension_reason="r"
    )
    response = suspended.get(a["url"])
    assert response.status_code == 200 and response.content == stored_bytes(a["id"])
    unverified = signed_in("fresh.owner@example.com", state="unverified")
    other, (b,) = with_images(unverified, 1)
    assert unverified.get(b["url"]).status_code == 200


def test_a_deactivated_owner_cannot_view():
    owner = signed_in()
    listing, (a,) = with_images(owner, 1)
    Account.objects.filter(id=owner.account_id).update(deactivated_at=timezone.now())
    assert_not_found(owner.get(a["url"]))


def test_a_listing_that_is_not_a_draft_serves_nothing(monkeypatch):
    client = signed_in()
    listing, (a,) = with_images(client, 1)
    assert client.get(a["url"]).status_code == 200
    with connection.cursor() as cursor:  # undone with the test's transaction
        cursor.execute("ALTER TABLE listings_propertylisting DROP CONSTRAINT listings_status_valid")
    PropertyListing.objects.filter(id=listing["id"]).update(status="published")
    assert_not_found(client.get(a["url"]))


def test_a_removed_image_is_gone_and_a_lost_file_is_404_without_leaking(
    media_root, django_capture_on_commit_callbacks
):
    client = signed_in()
    listing, (a, b) = with_images(client, 2)
    key = MediaAsset.objects.get(pk=ListingImage.objects.get(pk=b["id"]).asset_id).storage_key
    storage.get_storage().delete(key)  # the file vanished
    response = client.get(b["url"])
    assert_not_found(response)
    assert key not in response.content.decode()
    with django_capture_on_commit_callbacks(execute=True):
        client.delete(f"/api/v1/listings/{listing['id']}/images/{a['id']}", **csrf(client))
    assert_not_found(client.get(a["url"]))
    MediaAsset.objects.all().delete()  # a lost row too
    assert_not_found(client.get(b["url"]))


def test_the_media_route_allows_only_get_and_head():
    client = signed_in()
    listing, (a,) = with_images(client, 1)
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)(a["url"], **csrf(client))
        assert response.status_code == 405


def test_no_key_path_or_owner_appears_in_logs_headers_or_errors(caplog):
    client = signed_in()
    listing, (a, b) = with_images(client, 2)
    key = MediaAsset.objects.get(pk=ListingImage.objects.get(pk=a["id"]).asset_id).storage_key
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    caplog.set_level(logging.DEBUG)
    try:
        ok = client.get(a["url"])
        bad = client.get(file_url(listing["id"], uuid.uuid4()))
        gone = Client().get(a["url"])
        listing_body = client.get(detail(listing["id"]))
        reorder = patch(client, listing["id"], {"base_revision": 3, "order": [b["id"], a["id"]]})
    finally:
        root.removeHandler(handler)
    needles = [key, str(client.account_id), str(root_path())]
    surfaces = [stream.getvalue(), caplog.text]
    for response in (ok, bad, gone, listing_body, reorder):
        surfaces.append(str(dict(response.items())))
        if response is not ok:
            surfaces.append(response.content.decode())
    for surface in surfaces:
        for needle in needles:
            assert needle not in surface
    assert key not in ok.content.decode(errors="ignore")


def root_path():
    from django.conf import settings

    return settings.MEDIA_ROOT


# --- ghosts over HTTP, HEAD, the PATCH cap and limit --------------------------------------------


def lose_asset(image):
    MediaAsset.objects.filter(pk=ListingImage.objects.get(pk=image["id"]).asset_id).delete()


def test_a_ghost_is_omitted_by_detail_list_and_the_409_current_and_the_listing_stays_manageable():
    client = signed_in()
    listing, (a, b, c) = with_images(client, 3)
    lose_asset(b)
    detail_body = current(client, listing["id"])
    assert ids(detail_body) == [a["id"], c["id"]]
    listed = client.get("/api/v1/me/listings")
    assert listed.status_code == 200 and ids(listed.json()["results"][0]) == [a["id"], c["id"]]
    stale = patch(client, listing["id"], {"base_revision": 1, "order": [a["id"], c["id"]]})
    assert stale.status_code == 409 and ids(stale.json()["current"]) == [a["id"], c["id"]]
    # the client's view (two images) is a valid order: the ghost is cleaned up under the lock
    ok = patch(
        client,
        listing["id"],
        {"base_revision": detail_body["revision"], "order": [c["id"], a["id"]]},
    )
    assert ok.status_code == 200 and ids(ok.json()) == [c["id"], a["id"]]
    assert [m["position"] for m in ok.json()["media"]] == [0, 1]
    assert ListingImage.objects.filter(listing_id=listing["id"]).count() == 2


def test_head_decides_like_get_and_reads_no_file(monkeypatch):
    client = signed_in()
    listing, (a,) = with_images(client, 1, JPEG)
    stranger = signed_in("other.owner@example.com")

    def boom(self, key):
        raise AssertionError("HEAD must not read the file")

    monkeypatch.setattr(storage.FilesystemStorage, "get", boom)
    ok = client.head(a["url"])
    assert ok.status_code == 200 and ok.content == b""
    assert ok["Content-Length"] == str(a["bytes"]) and ok["Content-Type"] == "image/jpeg"
    assert ok["Cache-Control"] == "private, no-store"
    assert ok["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert stranger.head(a["url"]).status_code == 404
    assert Client().head(a["url"]).status_code == 404


def test_no_validator_range_or_encoding_header_and_conditional_requests_get_a_plain_200():
    client = signed_in()
    listing, (a,) = with_images(client, 1, JPEG)
    for kwargs in (
        {},
        {"HTTP_IF_NONE_MATCH": "*"},
        {"HTTP_RANGE": "bytes=5-"},
        {"HTTP_ACCEPT_ENCODING": "gzip, br"},
        {"HTTP_IF_MODIFIED_SINCE": "Wed, 21 Oct 2099 07:28:00 GMT"},
    ):
        response = client.get(a["url"], **kwargs)
        assert response.status_code == 200 and len(response.content) == a["bytes"]
        for name in ("ETag", "Accept-Ranges", "Content-Encoding", "Content-Range", "Last-Modified"):
            assert not response.has_header(name)


def test_a_lost_file_is_the_uniform_404_on_get(media_root):
    client = signed_in()
    listing, (a,) = with_images(client, 1)
    key = MediaAsset.objects.get().storage_key
    storage.get_storage().delete(key)
    assert_not_found(client.get(a["url"]))


def test_the_reorder_body_is_capped_at_64_kib_and_an_order_over_ten_is_refused():
    client = signed_in()
    listing, (a,) = with_images(client, 1)
    base = current(client, listing["id"])["revision"]
    big = json.dumps({"base_revision": base, "order": [a["id"]], "cover_id": "x" * 70_000})
    response = patch(client, listing["id"], big)
    refused(response, 400, "validation_error", {"order": [field_message("invalid")]})
    long_order = [str(uuid.uuid4()) for _ in range(11)]
    refused(
        patch(client, listing["id"], {"base_revision": base, "order": long_order}),
        400,
        "validation_error",
        {"order": [field_message("invalid")]},
    )
    stranger = signed_in("other.owner@example.com")
    refused(patch(stranger, listing["id"], big), 404, "not_found")  # ownership first
    assert (
        patch(client, listing["id"], {"base_revision": base, "order": [a["id"]]}).status_code == 200
    )


def test_the_reorder_has_its_own_rate_limit_and_does_not_use_the_upload_one():
    client = signed_in()
    listing, (a, b) = with_images(client, 2)
    RateLimitPolicy.objects.filter(scope="image_order").update(limit=2)
    base = current(client, listing["id"])["revision"]
    for turn in range(2):
        order = [b["id"], a["id"]] if turn == 0 else [a["id"], b["id"]]
        assert (
            patch(client, listing["id"], {"base_revision": base + turn, "order": order}).status_code
            == 200
        )
    response = patch(
        client, listing["id"], {"base_revision": base + 2, "order": [b["id"], a["id"]]}
    )
    refused(response, 429, "throttled")
    assert int(response["Retry-After"]) >= 1
    assert ids(current(client, listing["id"])) == [a["id"], b["id"]]  # nothing was written
    other = signed_in("other.owner@example.com")  # per account
    other_listing, (x, y) = with_images(other, 2)
    assert (
        patch(
            other, other_listing["id"], {"base_revision": 3, "order": [y["id"], x["id"]]}
        ).status_code
        == 200
    )
    RateLimitPolicy.objects.filter(scope="image_upload").update(limit=1)
    assert upload(other, other_listing["id"], PNG).status_code == 429  # the upload limit is its own
