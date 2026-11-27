"""POST /api/v1/listings/{id}/republish over HTTP (Story 2.18), with CSRF checks on."""

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
from apps.listings import publication, selectors, services, status
from apps.listings.models import PropertyListing, PublicationEvent
from tests.api.test_listings_api import ADMIN_PASSWORD, CSRF, detail, sign_in, signed_in
from tests.listings.support import (
    SYSTEM,
    ready_draft,
    seed_cap,
    set_account_state,
    set_listing_visibility,
    user,
)

KEY = "key-0001"
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
ENVELOPE = {"code", "message", "fields", "request_id"}
WIRE = {
    *("id", "status", "visibility", "revision", "registry_version"),
    *("fields", "provenance", "media", "restricted", "restriction_reason"),
    "under_review",
}


@pytest.fixture(autouse=True)
def clock(db, monkeypatch):
    seed_cap()
    monkeypatch.setattr("apps.listings.publication.timezone.now", lambda: NOW)


def url(listing_id):
    return f"{detail(listing_id)}/republish"


def csrf(client) -> dict:
    return {"HTTP_X_CSRFTOKEN": client.get(CSRF).json()["csrf_token"]}


def republish(client, listing_id, base, key=KEY, token=True, body=None):
    extra = csrf(client) if token else {}
    if key is not None:
        extra["HTTP_IDEMPOTENCY_KEY"] = key
    body = {"base_revision": base} if body is None else body
    return client.post(url(listing_id), json.dumps(body), content_type="application/json", **extra)


def archived(client, **kw):
    """An Archived Listing of the signed-in account (published once, now), as (id, revision)."""
    account = SimpleNamespace(id=client.account_id)
    view = ready_draft(account, **kw)
    publication.publish(user(account), view.id, view.revision, f"seed-{view.id}", NOW)
    done = selectors.get_owned_listing(user(account), view.id)
    moved = status.change_status(user(account), view.id, done.revision, "archived")
    return str(view.id), moved.revision


def refused(response, status_code, error_code):
    assert (response.status_code, response.json()["error"]["code"]) == (status_code, error_code)
    assert set(response.json()["error"]) == ENVELOPE
    assert response["Cache-Control"] == "no-store"
    return response.json()


def test_a_free_republication_returns_the_listing_and_its_kind():
    client = signed_in()
    listing_id, revision = archived(client)
    response = republish(client, listing_id, revision)
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    body = response.json()
    assert set(body) == {*WIRE, "publication_kind"}
    assert body["publication_kind"] == "republish_free"
    assert (body["status"], body["visibility"], body["revision"]) == (
        "published",
        "public",
        revision + 1,
    )
    assert {k: v for k, v in body.items() if k != "publication_kind"} == client.get(
        detail(listing_id)
    ).json()
    assert client.get("/api/v1/me/publication-allowance").json()["used"] == 1
    assert Client().get(detail(listing_id)).status_code == 401  # the owner route stays private


def test_a_changed_identity_is_counted_and_the_allowance_shows_it():
    client = signed_in()
    listing_id, revision = archived(client)
    account = SimpleNamespace(id=client.account_id)
    edited = services.apply_updates(user(account), listing_id, revision, {"area_value": "11"})
    body = republish(client, listing_id, edited.revision).json()
    assert body["publication_kind"] == "republish_counted"
    assert PublicationEvent.objects.filter(kind="republish_counted", counted=True).count() == 1
    assert client.get("/api/v1/me/publication-allowance").json()["used"] == 2


def test_a_replay_returns_the_same_body_and_charges_once():
    client = signed_in()
    listing_id, revision = archived(client)
    account = SimpleNamespace(id=client.account_id)
    services.apply_updates(user(account), listing_id, revision, {"area_unit": "kanal"})
    revision += 1
    first = republish(client, listing_id, revision)
    again = republish(client, listing_id, revision)
    assert first.json()["publication_kind"] == "republish_counted"
    assert again.status_code == 200 and again.json() == first.json()
    assert PublicationEvent.objects.filter(kind="republish_counted").count() == 1
    assert AuditRecord.objects.filter(action="listing.republished").count() == 1


def test_the_same_key_for_other_content_is_422():
    client = signed_in()
    one, first = archived(client)
    two, second = archived(client)
    republish(client, one, first)
    body = refused(republish(client, two, second), 422, "idempotency_key_reused")
    assert body["error"]["message"] == get_error("idempotency_key_reused").message


@pytest.mark.parametrize("key", [None, "", "has space", "x" * 129, "é", "a/b"])
def test_a_missing_or_bad_key_is_a_400_on_the_header(key):
    client = signed_in()
    listing_id, revision = archived(client)
    body = refused(republish(client, listing_id, revision, key=key), 400, "validation_error")
    expected = "required" if key is None else "invalid"
    assert body["error"]["fields"] == {"idempotency_key": [field_message(expected)]}


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
    listing_id, _ = archived(client)
    response = republish(client, listing_id, None, body=body)
    assert list(refused(response, 400, "validation_error")["error"]["fields"]) == ["base_revision"]


@pytest.mark.parametrize(
    "body", [{"base_revision": 2, "fields": {}}, {"base_revision": 2, "x": 1}, [], "x"]
)
def test_unknown_body_keys_and_other_shapes_are_400(body):
    client = signed_in()
    listing_id, _ = archived(client)
    refused(republish(client, listing_id, None, body=body), 400, "validation_error")


def test_incomplete_data_is_listed_at_once_with_static_sentences():
    client = signed_in()
    listing_id, revision = archived(client)
    account = SimpleNamespace(id=client.account_id)
    view = services.apply_updates(user(account), listing_id, revision, {"title": None})
    for image in view.media:
        services.remove_image(user(account), listing_id, image.id)
    revision = selectors.get_owned_listing(user(account), listing_id).revision
    body = refused(republish(client, listing_id, revision), 400, "validation_error")
    assert body["error"]["fields"] == {
        "title": [field_message("required")],
        "images": ["Add at least one image."],
    }
    assert PropertyListing.objects.get().status == "archived"
    assert (
        PublicationEvent.objects.count() == 1
        and not IdempotencyRecord.objects.filter(key=KEY).exists()
    )


def test_a_stale_revision_carries_current():
    client = signed_in()
    listing_id, revision = archived(client)
    body = refused(republish(client, listing_id, revision - 1), 409, "stale_revision")
    assert body["current"] == client.get(detail(listing_id)).json()


@pytest.mark.parametrize("current", ["draft", "published", "sold", "rented", "removed"])
def test_a_listing_that_is_not_archived_is_a_conflict(current):
    client = signed_in()
    listing_id, revision = archived(client)
    set_listing_visibility(listing_id, current)
    refused(republish(client, listing_id, revision), 409, "conflict")
    assert PublicationEvent.objects.count() == 1


def test_a_restricted_listing_is_a_403_and_writes_nothing():
    client = signed_in()
    listing_id, revision = archived(client)
    set_listing_visibility(listing_id, "archived", restricted=True)
    refused(republish(client, listing_id, revision), 403, "forbidden")
    assert (
        PublicationEvent.objects.count() == 1 and PropertyListing.objects.get().status == "archived"
    )


def test_an_exhausted_allowance_is_a_409_for_a_counted_republication_only():
    client = signed_in()
    account = SimpleNamespace(id=client.account_id)
    one, first = archived(client)
    PublicationEvent.objects.create(  # the second of two publications this month
        account_id=client.account_id, listing_id=uuid.uuid4(), kind="initial", counted=True,
        created_at=NOW,
    )  # fmt: skip
    edited = services.apply_updates(user(account), one, first, {"area_value": "12"})
    body = refused(republish(client, one, edited.revision), 409, "quota_exhausted")
    assert body["details"] == {"limit": 2, "used": 2, "resets_at": "2026-12-01T00:00:00+05:00"}
    assert body["error"]["message"] == get_error("quota_exhausted").message
    assert body["error"]["fields"] == {} and set(body) == {"error", "details"}
    assert IdempotencyRecord.objects.filter(key=KEY).count() == 0
    services.apply_updates(user(account), one, edited.revision, {"area_value": "10"})
    free = republish(client, one, edited.revision + 1, key="key-2")  # back to the baseline
    assert free.status_code == 200 and free.json()["publication_kind"] == "republish_free"


@pytest.mark.parametrize(
    ("state", "error_code"),
    [("unverified", "email_not_verified"), ("suspended", "publishing_unavailable")],
)
def test_an_account_that_cannot_publish_gets_a_neutral_403(state, error_code):
    client = signed_in()
    listing_id, revision = archived(client)
    set_account_state(client.account_id, state)
    body = refused(republish(client, listing_id, revision), 403, error_code)
    assert body["error"]["message"] == get_error(error_code).message
    assert body["error"]["fields"] == {} and set(body) == {"error"}
    assert PublicationEvent.objects.count() == 1


def test_another_account_and_an_admin_get_404_and_a_visitor_401():
    owner = signed_in()
    listing_id, revision = archived(owner)
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
        body = refused(republish(caller, target, revision), 404, "not_found")
        body["error"]["request_id"] = ""
        bodies.append(body)
    assert all(body == bodies[0] for body in bodies)  # the whole body, not just the code
    refused(
        republish(Client(enforce_csrf_checks=True), listing_id, revision, token=False),
        401,
        "unauthenticated",
    )
    assert (
        PropertyListing.objects.get().status == "archived" and PublicationEvent.objects.count() == 1
    )


def test_a_missing_csrf_token_is_403_and_nothing_is_republished():
    client = signed_in()
    listing_id, revision = archived(client)
    response = republish(client, listing_id, revision, token=False)
    assert (response.status_code, response.json()["error"]["code"]) == (403, "forbidden")
    assert PublicationEvent.objects.count() == 1


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_only_post_is_allowed(method):
    client = signed_in()
    listing_id, _ = archived(client)
    assert getattr(client, method)(url(listing_id), **csrf(client)).status_code == 405


def test_the_publish_response_is_unchanged():
    client = signed_in()
    view = ready_draft(SimpleNamespace(id=client.account_id))
    response = client.post(
        f"{detail(view.id)}/publish",
        json.dumps({"base_revision": view.revision}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="pub-1",
        **csrf(client),
    )
    assert response.status_code == 200 and set(response.json()) == WIRE


def test_the_fingerprint_never_leaves_the_service():
    client = signed_in()
    listing_id, revision = archived(client)
    response = republish(client, listing_id, revision)
    fingerprint = PublicationEvent.objects.get(kind="republish_free").identity_fingerprint
    assert len(fingerprint) == 64
    assert fingerprint not in response.content.decode()
    record = AuditRecord.objects.get(action="listing.republished")
    assert fingerprint not in repr((record.metadata, record.reason, record.target_type))


def test_the_openapi_describes_the_route(tmp_path):
    target = tmp_path / "schema.yaml"
    call_command("spectacular", "--file", str(target), verbosity=0)
    schema = yaml.safe_load(target.read_text())
    operation = schema["paths"]["/api/v1/listings/{listing_id}/republish"]["post"]
    assert operation["operationId"] == "republish_listing"
    assert {"200", "400", "401", "403", "404", "409", "422"} <= set(operation["responses"])
    header = [p for p in operation["parameters"] if p["in"] == "header"]
    assert [(p["name"], p["required"]) for p in header] == [("Idempotency-Key", True)]
    ok = schema["components"]["schemas"]["RepublishedListing"]
    assert "publication_kind" in ok["properties"] and "publication_kind" in ok["required"]
    published = schema["paths"]["/api/v1/listings/{listing_id}/publish"]["post"]["responses"]["200"]
    assert "publication_kind" not in json.dumps(published)


def test_a_deactivated_account_gets_a_neutral_refusal_and_nothing_is_written():
    client = signed_in()
    listing_id, revision = archived(client)
    set_account_state(client.account_id, "deactivated")
    response = republish(client, listing_id, revision)
    assert response.status_code in (401, 403)  # no session survives, or the service refuses
    assert response.json()["error"]["code"] in ("unauthenticated", "forbidden")
    assert response["Cache-Control"] == "no-store"
    assert (
        PublicationEvent.objects.count() == 1 and PropertyListing.objects.get().status == "archived"
    )


def test_a_restricted_draft_cannot_be_published_over_http():
    client = signed_in()
    view = ready_draft(SimpleNamespace(id=client.account_id))
    set_listing_visibility(view.id, "draft", restricted=True)
    response = client.post(
        f"{detail(view.id)}/publish",
        json.dumps({"base_revision": view.revision}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="pub-r",
        **csrf(client),
    )
    refused(response, 403, "forbidden")
    assert not PublicationEvent.objects.exists()
