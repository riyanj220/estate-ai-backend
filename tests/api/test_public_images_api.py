"""The media endpoint under the visibility policy (Story 2.12): public images for anyone with a
short cache, owner-only images private, over HTTP with Django's CSRF checks on."""

import io
import itertools
import json
import logging

import pytest
from django.test import Client

from apps.identity import services as identity_services
from apps.kernel import runtime, storage
from apps.kernel.models import MediaAsset
from apps.kernel.request_logging import JsonFormatter
from apps.listings.models import ListingImage
from tests.api.test_listing_images_api import new_draft, upload
from tests.api.test_listing_media_api import assert_not_found, file_url
from tests.api.test_listings_api import ADMIN_PASSWORD, sign_in, signed_in
from tests.imaging import make_image
from tests.listings.support import (
    SYSTEM,
    seed_cap,
    seed_media_policies,
    set_account_state,
    set_listing_visibility,
)

PNG = make_image("PNG", (500, 400))
OWNER_STATES = ("active", "unverified", "suspended", "deactivated")


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


def no_cookie(response):
    assert "Set-Cookie" not in response
    assert "cookie" not in response.get("Vary", "").lower()
    assert not response.cookies


def owner_with_image(email="jane.doe@example.com"):
    client = signed_in(email)
    listing = new_draft(client)
    image = upload(client, listing["id"], PNG).json()
    return client, listing, image


def stored(image_id) -> bytes:
    link = ListingImage.objects.get(pk=image_id)
    return storage.get_storage().get(MediaAsset.objects.get(pk=link.asset_id).storage_key)


def without_request_id(response) -> dict:
    return {**response.json()["error"], "request_id": ""}


def admin_client():
    identity_services.provision_admin(SYSTEM, "root.admin@example.com", ADMIN_PASSWORD)
    return sign_in("root.admin@example.com", admin=True)


@pytest.mark.parametrize(
    ("status", "restricted", "owner_state"),
    list(
        itertools.product(
            ("draft", "published", "archived", "sold", "rented", "removed"),
            (False, True),
            OWNER_STATES,
        )
    ),
)
def test_the_http_matrix(status, restricted, owner_state):
    owner, listing, image = owner_with_image()
    other = signed_in("other.person@example.com")
    admin = admin_client()
    set_listing_visibility(listing["id"], status, restricted)
    set_account_state(owner.account_id, owner_state)
    public = status == "published" and not restricted and owner_state == "active"
    viewers = {
        "visitor": Client(),
        "other": other,
        "owner": owner,
        "admin": admin,
    }
    reference = Client().get(file_url(listing["id"], "00000000-0000-4000-8000-000000000000"))
    for name, client in viewers.items():
        allowed = (
            public
            or (name == "owner" and owner_state != "deactivated")
            or (name == "admin" and status != "draft")  # moderation review (Story 9.3)
        )
        get = client.get(image["url"])
        head = client.head(image["url"])
        assert get.status_code == head.status_code == (200 if allowed else 404), name
        if allowed:
            assert get.content == stored(image["id"]) and head.content == b""
            expected = "public, max-age=60" if public else "private, no-store"
            assert get["Cache-Control"] == head["Cache-Control"] == expected
            for header in ("Content-Length", "Content-Type", "Content-Disposition"):
                assert get[header] == head[header], header
            assert get["Content-Length"] == str(len(get.content))
            assert get["X-Content-Type-Options"] == "nosniff"
            assert get["Content-Security-Policy"] == "default-src 'none'; sandbox"
            assert get["Content-Disposition"] == "inline"
            assert "ETag" not in get and "Accept-Ranges" not in get
            if public:
                no_cookie(get)
                no_cookie(head)
        else:
            assert_not_found(get)
            assert without_request_id(get) == without_request_id(reference)  # the whole body
            assert get["Cache-Control"] == head["Cache-Control"] == "no-store"
            assert get["Content-Type"] == head["Content-Type"] == reference["Content-Type"]


def test_a_public_image_carries_no_cookie_even_when_the_request_has_session_cookies():
    owner, listing, image = owner_with_image()
    set_listing_visibility(listing["id"])
    for client in (owner, signed_in("other.person@example.com")):  # real session cookies
        for method in ("get", "head"):
            response = getattr(client, method)(image["url"])
            assert response.status_code == 200
            assert response["Cache-Control"] == "public, max-age=60"
            no_cookie(response)
    stale = Client()
    stale.cookies["sessionid"] = "stale"
    stale.cookies["csrftoken"] = "x"
    for method in ("get", "head"):
        response = getattr(stale, method)(image["url"])
        assert response.status_code == 200 and response["Cache-Control"] == "public, max-age=60"
        no_cookie(response)


def test_the_owner_of_a_public_listing_gets_the_public_headers_too():
    """The answer for a public image does not depend on who asks, the owner included."""
    owner, listing, image = owner_with_image()
    set_listing_visibility(listing["id"])
    for method in ("get", "head"):
        response = getattr(owner, method)(image["url"])
        assert response.status_code == 200
        assert response["Cache-Control"] == "public, max-age=60"
        no_cookie(response)


def test_an_owner_only_image_stays_private_even_for_a_published_restricted_listing():
    owner, listing, image = owner_with_image()
    set_listing_visibility(listing["id"], "published", True)
    assert owner.get(image["url"])["Cache-Control"] == "private, no-store"
    assert_not_found(Client().get(image["url"]))


def test_a_listing_that_becomes_non_public_is_404_on_the_next_request():
    owner, listing, image = owner_with_image()
    set_listing_visibility(listing["id"])
    visitor = Client()
    assert visitor.get(image["url"]).status_code == 200
    set_listing_visibility(listing["id"], "published", True)  # moderation restricts it
    assert_not_found(visitor.get(image["url"]))
    assert owner.get(image["url"]).status_code == 200


def test_suspended_then_reactivated_owner_hides_then_restores_public_images():
    owner, listing, image = owner_with_image()
    set_listing_visibility(listing["id"])
    visitor = Client()
    set_account_state(owner.account_id, "suspended")
    assert_not_found(visitor.get(image["url"]))
    assert visitor.head(image["url"]).status_code == 404
    assert owner.get(image["url"]).status_code == 200  # still sees their own
    set_account_state(owner.account_id, "active")
    assert visitor.get(image["url"]).status_code == 200


def test_another_listings_image_and_malformed_ids_are_the_same_404_for_a_visitor():
    owner, listing, image = owner_with_image()
    other_listing = new_draft(owner)
    set_listing_visibility(listing["id"])
    set_listing_visibility(other_listing["id"])
    visitor = Client()
    paths = [
        file_url(other_listing["id"], image["id"]),
        file_url(listing["id"], "not-a-uuid"),
        file_url("not-a-uuid", image["id"]),
        file_url(listing["id"], image["id"].upper()),
    ]
    bodies = []
    for path in paths:
        response = visitor.get(path)
        assert_not_found(response)
        bodies.append({k: v for k, v in response.json()["error"].items() if k != "request_id"})
    assert all(body == bodies[0] for body in bodies)


def test_no_private_data_appears_in_a_public_response_or_the_logs(caplog):
    owner, listing, image = owner_with_image()
    set_listing_visibility(listing["id"])
    key = MediaAsset.objects.get().storage_key
    sink = io.StringIO()
    stream = logging.StreamHandler(sink)
    stream.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(stream)
    caplog.set_level(logging.DEBUG)
    try:
        ok = Client().get(image["url"])
        bad = Client().get(file_url(listing["id"], "00000000-0000-4000-8000-000000000000"))
    finally:
        root.removeHandler(stream)
    needles = [key, str(owner.account_id), str(MediaAsset.objects.get().id)]
    surfaces = [
        sink.getvalue(),
        caplog.text,
        str(dict(ok.items())),
        str(dict(bad.items())),
    ]
    surfaces.append(json.dumps(bad.json()))
    for surface in surfaces:
        for needle in needles:
            assert needle not in surface
    assert key not in ok.content.decode(errors="ignore")


def test_an_unknown_status_serves_nothing_even_to_the_owner():
    from django.db import connection

    owner, listing, image = owner_with_image()
    with connection.cursor() as cursor:  # undone with the test's transaction
        cursor.execute("ALTER TABLE listings_propertylisting DROP CONSTRAINT listings_status_valid")
    set_listing_visibility(listing["id"], "pending_review")
    for client in (owner, Client()):
        assert_not_found(client.get(image["url"]))
        assert client.head(image["url"]).status_code == 404
