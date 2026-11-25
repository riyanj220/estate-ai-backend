"""GET /api/v1/me/publication-allowance over HTTP (Story 2.14)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.utils import timezone

from apps.identity import services as identity_services
from apps.identity.models import Account
from apps.listings.models import PublicationEvent
from tests.api.test_listings_api import ADMIN_PASSWORD, SYSTEM, code, sign_in, signed_in
from tests.listings.support import seed_cap

URL = "/api/v1/me/publication-allowance"
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
KEYS = {"limit", "used", "remaining", "period_start", "resets_at", "timezone"}


@pytest.fixture(autouse=True)
def clock(db, monkeypatch):
    seed_cap()
    monkeypatch.setattr("apps.presentation.api.listings._now", lambda: NOW)


def event(account_id, at=NOW, kind="initial", counted=True):
    PublicationEvent.objects.create(
        account_id=account_id, listing_id=uuid.uuid4(), kind=kind, counted=counted, created_at=at
    )


@pytest.mark.parametrize(
    ("account_type", "state", "limit"),
    [
        ("individual", "active", 2),
        ("agency", "active", 10),
        ("individual", "unverified", 2),
        ("agency", "unverified", 10),
        ("individual", "suspended", 2),
    ],
)
def test_an_owner_reads_the_whole_body(account_type, state, limit):
    client = signed_in(account_type=account_type, state=state)
    event(client.account_id)
    event(client.account_id, kind="republish_free", counted=False)
    response = client.get(URL)
    assert response.status_code == 200
    assert response.json() == {
        "limit": limit,
        "used": 1,
        "remaining": limit - 1,
        "period_start": "2026-11-01T00:00:00+05:00",
        "resets_at": "2026-12-01T00:00:00+05:00",
        "timezone": "Asia/Karachi",
    }
    assert response["Cache-Control"] == "no-store"


def test_another_accounts_events_never_show():
    mine = signed_in("mine.owner@example.com")
    other = signed_in("other.owner@example.com")
    for _ in range(5):
        event(other.account_id)
    body = mine.get(URL).json()
    assert (body["used"], body["remaining"]) == (0, 2)
    assert set(body) == KEYS
    assert str(other.account_id) not in mine.get(URL).content.decode()


def test_exhausted_and_over_limit_remaining_is_zero():
    client = signed_in()
    for _ in range(3):
        event(client.account_id)
    body = client.get(URL).json()
    assert (body["limit"], body["used"], body["remaining"]) == (2, 3, 0)


def test_last_months_events_do_not_count_and_the_boundary_is_karachi():
    client = signed_in()
    event(client.account_id, at=datetime(2026, 10, 31, 18, 59, 59, tzinfo=UTC))  # October
    event(client.account_id, at=datetime(2026, 10, 31, 19, 0, 0, tzinfo=UTC))  # November
    assert client.get(URL).json()["used"] == 1


def test_a_visitor_is_unauthenticated_with_no_store():
    from django.test import Client

    response = Client().get(URL)
    assert (response.status_code, code(response)) == (401, "unauthenticated")
    assert response["Cache-Control"] == "no-store"


def test_an_administrator_is_forbidden():
    identity_services.provision_admin(SYSTEM, "root.admin@example.com", ADMIN_PASSWORD)
    response = sign_in("root.admin@example.com", admin=True).get(URL)
    assert (response.status_code, code(response)) == (403, "forbidden")
    assert response["Cache-Control"] == "no-store"
    assert set(response.json()) == {"error"}


def test_a_deactivated_account_has_no_session():
    client = signed_in()
    Account.objects.filter(id=client.account_id).update(deactivated_at=timezone.now())
    response = client.get(URL)
    assert (response.status_code, code(response)) == (401, "unauthenticated")


def test_only_get_is_allowed():
    client = signed_in()
    token = client.get("/api/v1/csrf").json()["csrf_token"]
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)(URL, HTTP_X_CSRFTOKEN=token)
        assert (response.status_code, code(response)) == (405, "method_not_allowed"), method
        assert response["Cache-Control"] == "no-store"


def test_the_body_matches_the_published_schema_component():
    import io

    import yaml
    from django.core.management import call_command

    out = io.StringIO()
    call_command("spectacular", stdout=out)
    schema = yaml.safe_load(out.getvalue())["components"]["schemas"]["PublicationAllowance"]
    body = signed_in().get(URL).json()
    assert set(body) == set(schema["properties"]) == set(schema["required"])
    kinds = {"integer": int, "string": str}
    for key, spec in schema["properties"].items():
        kind = kinds[spec.get("type", "string")]  # an enum is a $ref to a string
        assert isinstance(body[key], kind), key


def test_a_missing_policy_is_a_generic_500_envelope():
    from apps.listings.models import ListingQuotaPolicy

    client = signed_in()
    client.raise_request_exception = False
    ListingQuotaPolicy.objects.all().delete()
    response = client.get(URL)
    body = response.json()
    assert response.status_code == 500
    assert set(body) == {"error"} and body["error"]["code"] == "server_error"
    assert set(body["error"]) == {"code", "message", "fields", "request_id"}
    raw = response.content.decode().lower()
    assert "policy" not in raw and "quota" not in raw and "traceback" not in raw
    assert response["Cache-Control"] == "no-store"


def test_the_real_clock_is_used_without_the_stub(monkeypatch):
    import apps.presentation.api.listings as api

    monkeypatch.undo()
    seed_cap()
    client = signed_in()
    body = client.get(URL).json()
    start = datetime.fromisoformat(body["period_start"])
    reset = datetime.fromisoformat(body["resets_at"])
    assert start <= timezone.now() < reset
    assert timedelta(days=27) < reset - start < timedelta(days=32)
    assert api._now().tzinfo is not None


def test_the_schema_documents_the_route():
    import io

    import yaml
    from django.core.management import call_command

    out = io.StringIO()
    call_command("spectacular", stdout=out)
    path = yaml.safe_load(out.getvalue())["paths"]["/api/v1/me/publication-allowance"]
    assert set(path) == {"get"}
