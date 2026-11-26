"""GET and POST /api/v1/listings/{id}/correction over HTTP (Story 2.19), with CSRF checks on."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import yaml
from django.core.management import call_command
from django.test import Client

from apps.identity import services as identity_services
from apps.kernel.errors import field_message, get_error
from apps.listings import publication, selectors
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

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
ENVELOPE = {"code", "message", "fields", "request_id"}
CLOSED = {"available": False, "expires_at": None}


@pytest.fixture
def clock(monkeypatch):
    now = {"now": NOW}
    monkeypatch.setattr("apps.listings.publication.timezone.now", lambda: now["now"])
    return now


@pytest.fixture(autouse=True)
def setup(db, clock):
    seed_cap()


def url(listing_id):
    return f"{detail(listing_id)}/correction"


def csrf(client) -> dict:
    return {"HTTP_X_CSRFTOKEN": client.get(CSRF).json()["csrf_token"]}


def post(client, listing_id, base, fields=None, token=True, body=None):
    extra = csrf(client) if token else {}
    fields = {"area_value": "11"} if fields is None else fields
    body = {"base_revision": base, "fields": fields} if body is None else body
    return client.post(url(listing_id), json.dumps(body), content_type="application/json", **extra)


def published(client, at=NOW):
    account = SimpleNamespace(id=client.account_id)
    view = ready_draft(account)
    publication.publish(user(account), view.id, view.revision, f"pub-{view.id}", at)
    done = selectors.get_owned_listing(user(account), view.id)
    return str(view.id), done.revision


def refused(response, status_code, error_code):
    assert (response.status_code, response.json()["error"]["code"]) == (status_code, error_code)
    assert set(response.json()["error"]) == ENVELOPE
    assert response["Cache-Control"] == "no-store"
    return response.json()


def test_the_window_is_read_with_its_karachi_end_then_used_once(clock):
    client = signed_in()
    listing_id, revision = published(client)
    clock["now"] = NOW + timedelta(hours=2)
    response = client.get(url(listing_id))
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    assert response.json() == {"available": True, "expires_at": "2026-11-16T17:00:00+05:00"}
    response = post(client, listing_id, revision)
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    assert response.json() == client.get(detail(listing_id)).json()
    assert response.json()["revision"] == revision + 1
    assert response.json()["fields"]["area_value"] == "11.0000"
    assert client.get(url(listing_id)).json() == CLOSED
    body = refused(post(client, listing_id, revision + 1, {"area_value": "12"}), 409,
                   "correction_unavailable")  # fmt: skip
    assert body["error"]["message"] == get_error("correction_unavailable").message
    assert "current" not in body


def test_exactly_24_hours_after_the_publication_it_is_closed():
    client = signed_in()
    listing_id, revision = published(client, at=NOW - timedelta(hours=24))
    assert client.get(url(listing_id)).json() == CLOSED
    refused(post(client, listing_id, revision), 409, "correction_unavailable")


def test_refusals_carry_codes_never_values_and_write_nothing():
    client = signed_in()
    listing_id, revision = published(client)
    city = str(uuid.UUID(int=7))
    body = refused(post(client, listing_id, revision, {"city": city}), 400, "validation_error")
    assert body["error"]["fields"] == {"city": [field_message("not_editable")]}
    assert city not in json.dumps(body)
    body = refused(post(client, listing_id, revision, {"area_value": "10"}), 400,
                   "validation_error")  # fmt: skip
    assert body["error"]["fields"] == {"non_field_errors": [field_message("no_change")]}
    assert field_message("no_change") == (
        "Nothing would change. Enter a different locality or area."
    )
    for bad in (
        {"base_revision": revision, "fields": {"area_value": "11"}, "origin": "ai"},
        [],
        {"base_revision": revision},
        {"fields": {"area_value": "11"}},
    ):
        refused(post(client, listing_id, revision, body=bad), 400, "validation_error")
    body = refused(post(client, listing_id, revision - 1), 409, "stale_revision")
    assert body["current"]["revision"] == revision
    assert PublicationEvent.objects.get().correction_used is False
    assert PropertyListing.objects.get().revision == revision
    assert client.get(url(listing_id)).json()["available"] is True


@pytest.mark.parametrize(
    ("move", "code"),
    [("archived", (409, "conflict")), ("restricted", (403, "forbidden"))],
)
def test_a_listing_that_cannot_be_corrected(move, code):
    client = signed_in()
    listing_id, revision = published(client)
    restricted = move == "restricted"
    set_listing_visibility(listing_id, "published" if restricted else move, restricted=restricted)
    assert client.get(url(listing_id)).json() == CLOSED
    refused(post(client, listing_id, revision), *code)


def test_a_suspended_owner_reads_no_window_and_is_forbidden():
    client = signed_in()
    listing_id, revision = published(client)
    set_account_state(client.account_id, "suspended")
    assert client.get(url(listing_id)).json() == CLOSED
    refused(post(client, listing_id, revision), 403, "forbidden")


def test_csrf_visitor_other_account_and_administrator():
    client = signed_in()
    listing_id, revision = published(client)
    refused(post(client, listing_id, revision, token=False), 403, "forbidden")
    visitor = Client(enforce_csrf_checks=True)
    refused(visitor.get(url(listing_id)), 401, "unauthenticated")
    assert post(visitor, listing_id, revision).status_code in (401, 403)
    other = signed_in("other.doe@example.com")
    identity_services.provision_admin(SYSTEM, "root@example.com", ADMIN_PASSWORD)
    admin = sign_in("root@example.com", admin=True)
    for stranger in (other, admin):
        refused(stranger.get(url(listing_id)), 404, "not_found")
        refused(post(stranger, listing_id, revision), 404, "not_found")
    refused(client.get(url(uuid.uuid4())), 404, "not_found")
    refused(post(client, "nope", revision), 404, "not_found")
    assert client.put(url(listing_id), **csrf(client)).status_code == 405
    assert PropertyListing.objects.get().revision == revision


def test_the_schema_documents_both_operations(tmp_path):
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", "--file", str(target), "--validate")
    schema = yaml.safe_load(target.read_text())
    path = schema["paths"]["/api/v1/listings/{listing_id}/correction"]
    assert set(path) == {"get", "post"}
    components = schema["components"]["schemas"]
    assert set(components["CorrectionWindow"]["properties"]) == {"available", "expires_at"}
    assert components["CorrectionWindow"]["properties"]["expires_at"]["nullable"] is True
    assert set(components["ListingCorrectionFieldsRequest"]["properties"]) == {
        "locality",
        "area_value",
        "area_unit",
        "marla_basis",
    }
    request = path["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert set(components[request.rsplit("/", 1)[1]]["required"]) == {"base_revision", "fields"}
    assert path["post"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Listing"
    }
    assert {"200", "400", "401", "403", "404", "409"} <= set(path["post"]["responses"])
    assert "correction_unavailable" in target.read_text()
