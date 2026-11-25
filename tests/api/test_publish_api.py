"""POST /api/v1/listings/{id}/publish over HTTP (Story 2.15), with CSRF checks on."""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import yaml
from django.core.management import call_command
from django.test import Client

from apps.identity import services as identity_services
from apps.kernel.errors import field_message, get_error
from apps.kernel.models import AuditRecord, IdempotencyRecord
from apps.listings.models import PropertyListing, PublicationEvent
from tests.api.test_listings_api import ADMIN_PASSWORD, CSRF, detail, sign_in, signed_in
from tests.listings.support import (
    SYSTEM,
    add_images,
    ready_draft,
    seed_cap,
    set_account_state,
)

KEY = "key-0001"
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
ENVELOPE = {"code", "message", "fields", "request_id"}


@pytest.fixture(autouse=True)
def clock(db, monkeypatch):
    seed_cap()


def url(listing_id):
    return f"{detail(listing_id)}/publish"


def csrf(client) -> dict:
    return {"HTTP_X_CSRFTOKEN": client.get(CSRF).json()["csrf_token"]}


def publish(client, listing_id, base, key=KEY, token=True, body=None):
    extra = csrf(client) if token else {}
    if key is not None:
        extra["HTTP_IDEMPOTENCY_KEY"] = key
    body = {"base_revision": base} if body is None else body
    return client.post(url(listing_id), json.dumps(body), content_type="application/json", **extra)


def draft(client, **kw):
    """A ready Draft of the signed-in account, as (id, revision)."""
    view = ready_draft(SimpleNamespace(id=client.account_id), **kw)
    return str(view.id), view.revision


def refused(response, status, error_code):
    assert (response.status_code, response.json()["error"]["code"]) == (status, error_code)
    assert set(response.json()["error"]) == ENVELOPE
    assert response["Cache-Control"] == "no-store"
    return response.json()


def test_publishing_returns_the_owner_listing():
    client = signed_in()
    listing_id, revision = draft(client)
    response = publish(client, listing_id, revision)
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    body = response.json()
    assert (body["status"], body["visibility"], body["revision"]) == (
        "published",
        "public",
        revision + 1,
    )
    assert body == client.get(detail(listing_id)).json()
    assert set(body) == {
        *("id", "status", "visibility", "revision", "registry_version"),
        *("fields", "provenance", "media", "restricted", "restriction_reason"),
    }
    assert PublicationEvent.objects.count() == 1
    assert client.get("/api/v1/me/publication-allowance").json()["remaining"] == 1
    assert Client().get(detail(listing_id)).status_code == 401  # the owner route stays private


def test_a_replay_returns_the_same_body_and_charges_once():
    client = signed_in()
    listing_id, revision = draft(client)
    first = publish(client, listing_id, revision)
    again = publish(client, listing_id, revision)
    assert again.status_code == 200 and again.json() == first.json()
    assert PublicationEvent.objects.count() == 1
    assert AuditRecord.objects.filter(action="listing.published").count() == 1


def test_the_same_key_for_other_content_is_422():
    client = signed_in()
    one, revision = draft(client)
    two, second = draft(client)
    publish(client, one, revision)
    body = refused(publish(client, two, second), 422, "idempotency_key_reused")
    assert body["error"]["message"] == get_error("idempotency_key_reused").message
    assert PublicationEvent.objects.count() == 1


@pytest.mark.parametrize("key", [None, "", "has space", "x" * 129, "é", "a/b"])
def test_a_missing_or_bad_key_is_a_400_on_the_header(key):
    client = signed_in()
    listing_id, revision = draft(client)
    response = publish(client, listing_id, revision, key=key)
    body = refused(response, 400, "validation_error")
    expected = "required" if key is None else "invalid"
    assert list(body["error"]["fields"]) == ["idempotency_key"]
    assert body["error"]["fields"]["idempotency_key"] == [field_message(expected)]
    assert not PublicationEvent.objects.exists()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"base_revision": "2"},
        {"base_revision": 0},
        {"base_revision": True},
        {"base_revision": None},
    ],
)
def test_a_bad_revision_is_a_400(body):
    client = signed_in()
    listing_id, _ = draft(client)
    response = publish(client, listing_id, None, body=body)
    assert list(refused(response, 400, "validation_error")["error"]["fields"]) == ["base_revision"]


@pytest.mark.parametrize(
    "body", [{"base_revision": 2, "fields": {}}, {"base_revision": 2, "x": 1}, [], "x"]
)
def test_unknown_body_keys_and_other_shapes_are_400(body):
    client = signed_in()
    listing_id, _ = draft(client)
    refused(publish(client, listing_id, None, body=body), 400, "validation_error")


def test_missing_items_are_listed_at_once_with_static_sentences():
    client = signed_in()
    created = client.post(
        "/api/v1/listings",
        json.dumps({"purpose": "sale", "category": "house", "subtype": "house"}),
        content_type="application/json",
        **csrf(client),
    ).json()
    body = refused(publish(client, created["id"], 1), 400, "validation_error")
    fields = body["error"]["fields"]
    assert set(fields) == {
        "title", "asking_price", "price_basis", "area_value", "area_unit", "bedrooms",
        "bathrooms", "city", "locality", "images",
    }  # fmt: skip
    assert fields["images"] == ["Add at least one image."]
    assert fields["title"] == [field_message("required")]
    assert PropertyListing.objects.get().status == "draft" and not PublicationEvent.objects.exists()


def test_an_unconfirmed_assistant_value_is_named():
    client = signed_in()
    listing_id, revision = draft(client)
    row = PropertyListing.objects.get()
    row.provenance = {**row.provenance, "category": {"origin": "ai", "confirmed": False}}
    row.save(update_fields=["provenance"])
    fields = refused(publish(client, listing_id, revision), 400, "validation_error")["error"][
        "fields"
    ]
    assert fields == {"category": ["Confirm this value before publishing."]}


def test_several_unconfirmed_assistant_values_are_all_named():
    client = signed_in()
    listing_id, revision = draft(client, extra={"street": "Mango Lane", "house_number": "Plum 12"})
    row = PropertyListing.objects.get()
    row.provenance = {
        **row.provenance,
        "street": {"origin": "ai", "confirmed": False},
        "house_number": {"origin": "ai"},
        "category": {"origin": "ai", "confirmed": None},
    }
    row.save(update_fields=["provenance"])
    fields = refused(publish(client, listing_id, revision), 400, "validation_error")["error"][
        "fields"
    ]
    assert fields == dict.fromkeys(
        ("street", "house_number", "category"), ["Confirm this value before publishing."]
    )


def test_the_fingerprint_never_leaves_the_service():
    from apps.kernel.models import AuditRecord

    client = signed_in()
    listing_id, revision = draft(client)
    response = publish(client, listing_id, revision)
    again = publish(client, listing_id, revision)
    stale = publish(client, listing_id, revision, key="key-2")
    fingerprint = PublicationEvent.objects.get().identity_fingerprint
    assert len(fingerprint) == 64
    for body in (response, again, stale, client.get(detail(listing_id))):
        assert fingerprint not in body.content.decode()
    record = AuditRecord.objects.get(action="listing.published")
    assert fingerprint not in repr((record.metadata, record.reason, record.target_type))
    from apps.listings.events import ListingPublished

    assert fingerprint not in repr(
        ListingPublished(listing_id=uuid.uuid4(), account_id=uuid.uuid4())
    )
    assert stale.status_code == 409


def test_a_stale_revision_carries_current():
    client = signed_in()
    listing_id, revision = draft(client)
    body = refused(publish(client, listing_id, revision - 1), 409, "stale_revision")
    assert body["current"] == client.get(detail(listing_id)).json()


def test_a_published_listing_is_a_conflict():
    client = signed_in()
    listing_id, revision = draft(client)
    publish(client, listing_id, revision)
    refused(publish(client, listing_id, revision + 1, key="key-2"), 409, "conflict")


def test_an_exhausted_allowance_is_a_409_with_the_callers_numbers(monkeypatch):
    monkeypatch.setattr("apps.listings.publication.timezone.now", lambda: NOW)
    client = signed_in()
    for index in range(2):
        listing_id, revision = draft(client)
        assert publish(client, listing_id, revision, key=f"key-{index}").status_code == 200
    listing_id, revision = draft(client)
    body = refused(publish(client, listing_id, revision, key="key-9"), 409, "quota_exhausted")
    assert body["details"] == {"limit": 2, "used": 2, "resets_at": "2026-12-01T00:00:00+05:00"}
    assert body["error"]["message"] == get_error("quota_exhausted").message
    assert body["error"]["fields"] == {} and set(body) == {"error", "details"}
    assert PropertyListing.objects.filter(status="draft").count() == 1
    assert IdempotencyRecord.objects.filter(key="key-9").count() == 0


@pytest.mark.parametrize(
    ("state", "error_code"),
    [("unverified", "email_not_verified"), ("suspended", "publishing_unavailable")],
)
def test_an_account_that_cannot_publish_gets_a_neutral_403(state, error_code):
    client = signed_in()
    listing_id, revision = draft(client)
    set_account_state(client.account_id, state)
    body = refused(publish(client, listing_id, revision), 403, error_code)
    assert body["error"]["message"] == get_error(error_code).message
    assert body["error"]["fields"] == {} and set(body) == {"error"}
    assert not PublicationEvent.objects.exists()


def test_another_account_and_an_admin_get_404_and_a_visitor_401():
    owner = signed_in()
    listing_id, revision = draft(owner)
    other = signed_in("other.owner@example.com")
    identity_services.provision_admin(SYSTEM, "root.admin@example.com", ADMIN_PASSWORD)
    admin = sign_in("root.admin@example.com", admin=True)
    bodies = []
    for caller, target in (
        (other, listing_id),
        (admin, listing_id),
        (owner, str(uuid.uuid4())),
        (owner, "not-an-id"),
    ):
        response = publish(caller, target, revision)
        body = refused(response, 404, "not_found")
        body["error"]["request_id"] = ""
        bodies.append((response.content and body, dict(response.headers)))
    assert all(body == bodies[0][0] for body, _ in bodies)  # the whole body, not just the code
    assert {h.get("Content-Type") for _, h in bodies} == {"application/json"}
    refused(
        publish(Client(enforce_csrf_checks=True), listing_id, revision, token=False),
        401,
        "unauthenticated",
    )
    assert PropertyListing.objects.get().status == "draft" and not PublicationEvent.objects.exists()


def test_a_missing_csrf_token_is_403_and_nothing_is_published():
    client = signed_in()
    listing_id, revision = draft(client)
    response = publish(client, listing_id, revision, token=False)
    assert (response.status_code, response.json()["error"]["code"]) == (403, "forbidden")
    assert not PublicationEvent.objects.exists()


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_only_post_is_allowed(method):
    client = signed_in()
    listing_id, _ = draft(client)
    response = getattr(client, method)(url(listing_id), **csrf(client))
    assert response.status_code == 405


def test_the_images_are_required_and_adding_one_fixes_it():
    client = signed_in()
    listing_id, revision = draft(client, images=0)
    fields = refused(publish(client, listing_id, revision), 400, "validation_error")["error"][
        "fields"
    ]
    assert fields == {"images": ["Add at least one image."]}
    add_images(listing_id)
    assert publish(client, listing_id, revision).status_code == 200


def test_the_openapi_describes_the_route(tmp_path):
    target = tmp_path / "schema.yaml"
    call_command("spectacular", "--file", str(target), verbosity=0)
    schema = yaml.safe_load(target.read_text())
    operation = schema["paths"]["/api/v1/listings/{listing_id}/publish"]["post"]
    assert operation["operationId"] == "publish_listing"
    assert {"200", "400", "401", "403", "404", "409", "422"} <= set(operation["responses"])
    header = [p for p in operation["parameters"] if p["in"] == "header"]
    assert [(p["name"], p["required"]) for p in header] == [("Idempotency-Key", True)]
    components = schema["components"]["schemas"]
    for code in ("quota_exhausted", "email_not_verified", "publishing_unavailable"):
        assert code in components["ErrorCode"]["enum"]
