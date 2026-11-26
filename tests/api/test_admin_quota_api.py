"""The administrator quota routes over HTTP, with Django's CSRF checks on (Story 2.20)."""

import json
import uuid

import pytest
from django.utils import timezone

from apps.kernel.models import AuditRecord
from apps.listings.models import ListingQuotaPolicy, PublicationEvent
from tests.api.test_admin_api import (
    browser,
    error_code,
    make_admin,
    make_user,
    signed_in_admin,
    signed_in_user,
    token_for,
)

POLICY = "/api/v1/admin/quota-policy"
USAGE = "/api/v1/admin/quota-usage"


@pytest.fixture
def admin(db):
    make_admin()
    return signed_in_admin()


def put(client, account_type, body, csrf=True):
    extra = {"HTTP_X_CSRFTOKEN": token_for(client)} if csrf else {}
    return client.put(
        f"{POLICY}/{account_type}", json.dumps(body), content_type="application/json", **extra
    )


def used(account_id):
    PublicationEvent.objects.create(
        account_id=account_id,
        listing_id=uuid.uuid4(),
        kind="initial",
        counted=True,
        created_at=timezone.now(),
    )


def test_view_and_change(admin):
    response = admin.get(POLICY)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["items"][0] == {
        "account_type": "individual",
        "monthly_limit": 2,
        "effective_from": "2000-01-01T05:00:00+05:00",
    }

    changed = put(admin, "individual", {"monthly_limit": 4})
    assert changed.status_code == 200
    assert changed.headers["Cache-Control"] == "no-store"
    body = changed.json()
    assert (body["account_type"], body["monthly_limit"]) == ("individual", 4)
    assert body["effective_from"].endswith("+05:00")
    assert admin.get(POLICY).json()["items"][0] == body
    assert put(admin, "individual", {"monthly_limit": 4}).json() == body  # no-op
    assert AuditRecord.objects.filter(action="quota.policy_changed").count() == 1


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "monthly_limit"),
        ({"monthly_limit": "4"}, "monthly_limit"),
        ({"monthly_limit": 1001}, "monthly_limit"),
        ({"monthly_limit": 4, "effective_from": "x"}, "non_field_errors"),
        ([4], "non_field_errors"),
    ],
)
def test_invalid_bodies(admin, body, field):
    response = put(admin, "individual", body)
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert list(response.json()["error"]["fields"]) == [field]
    assert ListingQuotaPolicy.objects.count() == 2


def test_unknown_type_and_missing_csrf(admin):
    response = put(admin, "admin", {"monthly_limit": 4})
    assert (response.status_code, error_code(response)) == (404, "not_found")
    assert response.headers["Cache-Control"] == "no-store"
    response = put(admin, "individual", {"monthly_limit": 4}, csrf=False)
    assert (response.status_code, error_code(response)) == (403, "forbidden")
    assert ListingQuotaPolicy.objects.count() == 2


@pytest.mark.parametrize(("who", "status"), [("visitor", 401), ("user", 404)])
def test_non_administrators_are_refused_on_all_three_routes(db, who, status):
    account = make_user()
    used(account.id)
    client = signed_in_user() if who == "user" else browser()
    for response in (
        client.get(POLICY),
        put(client, "individual", {"monthly_limit": 4}),
        client.get(USAGE),
    ):
        assert response.status_code == status
        assert response.headers["Cache-Control"] == "no-store"
        assert "jane@example.com" not in response.content.decode()
    assert ListingQuotaPolicy.objects.count() == 2


def test_usage_pages(admin):
    first, second = make_user("one@example.com"), make_user("two@example.com", "agency")
    used(first.id)
    used(second.id)
    response = admin.get(USAGE, {"page_size": 1})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert set(body) == {"period_start", "resets_at", "next", "previous", "results"}
    assert body["previous"] is None
    assert body["period_start"].endswith("+05:00") and body["resets_at"].endswith("+05:00")
    assert body["results"] == [
        {
            "account_id": str(second.id),
            "email": "t***@example.com",
            "account_type": "agency",
            "used": 1,
            "limit": 10,
        }
    ]
    following = admin.get(body["next"])
    rest = following.json()
    assert [r["email"] for r in rest["results"]] == ["o***@example.com"]
    for full in ("one@example.com", "two@example.com"):
        assert full not in response.content.decode() + following.content.decode()
    assert rest["next"] is None
    bad = admin.get(USAGE, {"cursor": "nope"})
    assert (bad.status_code, list(bad.json()["error"]["fields"])) == (400, ["cursor"])
