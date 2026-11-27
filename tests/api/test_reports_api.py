"""Stories 9.1 and 9.2: reporting a Listing and reviewing Reports, over HTTP with CSRF on."""

import json
import uuid

import pytest
from django.test import Client

from apps.governance.models import Report, ReportTransition
from apps.kernel.models import AuditRecord, Notification, RateLimitPolicy
from apps.listings.models import ListingPrivateAddress, PropertyListing
from tests.api.test_listings_api import CSRF, signed_in
from tests.api.test_public_images_api import admin_client
from tests.listings.support import (
    publish_ready,
    seed_cap,
    seed_media_policies,
    set_account_state,
    set_listing_visibility,
)

QUEUE = "/api/v1/admin/reports"
STREET = "Hidden Lane Seventeen"


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    RateLimitPolicy.objects.update_or_create(
        scope="report_new",
        defaults={"limit": 10, "window_seconds": 86400, "key_kind": "account", "note": "t"},
    )


@pytest.fixture
def owner():
    return signed_in("owner@example.com")


@pytest.fixture
def reporter():
    return signed_in("reporter@example.com")


def post(client, url, body):
    token = client.get(CSRF).json()["csrf_token"]
    return client.post(
        url, json.dumps(body), content_type="application/json", HTTP_X_CSRFTOKEN=token
    )


def code(response):
    return response.json()["error"]["code"]


def report(client, listing_id, reason="misleading", **extra):
    return post(client, f"/api/v1/listings/{listing_id}/reports", {"reason": reason, **extra})


def under_review(owner, listing_id):
    return owner.get(f"/api/v1/listings/{listing_id}").json()["under_review"]


def test_a_report_records_the_public_view_only_and_changes_nothing(owner, reporter):
    listing_id = publish_ready(owner.account_id)
    ListingPrivateAddress.objects.update_or_create(
        listing_id=listing_id, defaults={"street": STREET}
    )
    before = PropertyListing.objects.values().get(id=listing_id)

    response = report(
        reporter, listing_id, "ai_generated_content", description="  Looks made up.  "
    )

    assert response.status_code == 201 and response["Cache-Control"] == "no-store"
    body = response.json()
    assert (body["status"], body["reason"], body["description"]) == (
        "pending",
        "ai_generated_content",
        "Looks made up.",
    )
    row = Report.objects.get()
    public = Client().get(f"/api/v1/public/listings/{listing_id}").json()
    assert row.snapshot["title"] == public["title"]
    text = json.dumps(row.snapshot)
    assert STREET not in text and "phone" not in text and str(owner.account_id) not in text
    assert PropertyListing.objects.values().get(id=listing_id) == before
    assert Client().get(f"/api/v1/public/listings/{listing_id}").status_code == 200
    assert AuditRecord.objects.filter(action="report.submitted").count() == 1


def test_one_open_report_per_reporter_per_listing(owner, reporter):
    listing_id = publish_ready(owner.account_id)
    assert report(reporter, listing_id).status_code == 201
    again = report(reporter, listing_id, "other")
    assert (again.status_code, code(again)) == (409, "conflict")
    assert report(signed_in("second@example.com"), listing_id).status_code == 201
    Report.objects.update(status="dismissed")
    assert report(reporter, listing_id).status_code == 201


def test_your_own_a_hidden_and_an_unknown_listing_are_refused(owner, reporter):
    listing_id = publish_ready(owner.account_id)
    own = report(owner, listing_id)
    assert (own.status_code, code(own)) == (403, "forbidden")
    for status, restricted in (("draft", False), ("published", True), ("removed", False)):
        set_listing_visibility(listing_id, status, restricted)
        assert code(report(reporter, listing_id)) == "not_found"
    assert code(report(reporter, uuid.uuid4())) == "not_found"
    assert report(Client(), listing_id).status_code == 401
    assert not Report.objects.exists()


@pytest.mark.parametrize(
    ("state", "expected"), [("unverified", 201), ("active", 201), ("suspended", 403)]
)
def test_the_report_capability_follows_the_account_state(owner, reporter, state, expected):
    listing_id = publish_ready(owner.account_id)
    set_account_state(reporter.account_id, state)
    assert report(reporter, listing_id).status_code == expected


def test_an_administrator_cannot_report(owner):
    listing_id = publish_ready(owner.account_id)
    assert code(report(admin_client(), listing_id)) == "forbidden"


def test_bad_values_and_a_missing_token(owner, reporter):
    listing_id = publish_ready(owner.account_id)
    assert report(reporter, listing_id, "rude").json()["error"]["fields"]["reason"]
    long = report(reporter, listing_id, description="x" * 1001)
    assert long.json()["error"]["fields"]["description"]
    bare = reporter.post(
        f"/api/v1/listings/{listing_id}/reports",
        json.dumps({"reason": "other"}),
        content_type="application/json",
    )
    assert bare.status_code == 403 and not Report.objects.exists()


def test_ten_reports_a_day(reporter):
    ids = []
    for number in range(11):
        seller = signed_in(f"seller{number}@example.com")
        ids.append(publish_ready(seller.account_id))
    assert [report(reporter, key).status_code for key in ids] == [201] * 10 + [429]
    assert Report.objects.count() == 10


def test_only_the_reporter_reads_a_report(owner, reporter):
    listing_id = publish_ready(owner.account_id)
    report_id = report(reporter, listing_id).json()["id"]
    url = f"/api/v1/me/reports/{report_id}"
    assert reporter.get(url).json()["listing_id"] == str(listing_id)
    for client in (owner, signed_in("third@example.com"), admin_client()):
        assert code(client.get(url)) == "not_found"
    assert Client().get(url).status_code == 401
    assert code(reporter.get("/api/v1/me/reports/nonsense")) == "not_found"


def test_review_runs_pending_reviewing_resolved(
    owner, reporter, django_capture_on_commit_callbacks
):
    listing_id = publish_ready(owner.account_id)
    report_id = report(reporter, listing_id, "ai_generated_content").json()["id"]
    report(signed_in("second@example.com"), listing_id, "duplicate")
    admin = admin_client()
    page = admin.get(f"{QUEUE}?reason=ai_generated_content").json()
    assert [r["id"] for r in page["results"]] == [report_id]
    assert page["unresolved"] == {"pending": 2, "reviewing": 0}
    assert not under_review(owner, listing_id)

    early = post(admin, f"{QUEUE}/{report_id}/resolve", {"notes": "Too early here"})
    assert code(early) == "conflict"
    started = post(admin, f"{QUEUE}/{report_id}/start", {})
    assert started.json()["status"] == "reviewing" and started.json()["open_for_listing"] == 2
    assert under_review(owner, listing_id)
    assert owner.get("/api/v1/me/listings").json()["results"][0]["under_review"] is True
    assert "under_review" not in Client().get(f"/api/v1/public/listings/{listing_id}").json()
    assert code(post(admin, f"{QUEUE}/{report_id}/start", {})) == "conflict"

    bare = post(admin, f"{QUEUE}/{report_id}/resolve", {})
    assert bare.json()["error"]["fields"]["notes"]
    with django_capture_on_commit_callbacks(execute=True):
        done = post(admin, f"{QUEUE}/{report_id}/resolve", {"notes": "Checked and confirmed"})
    body = done.json()
    assert body["status"] == "resolved" and body["closed_at"]
    assert [m["to_status"] for m in body["history"]] == ["reviewing", "resolved"]
    assert body["history"][1]["notes"] == "Checked and confirmed"
    assert body["reporter"]["masked_email"] == "r***@example.com"
    assert not under_review(owner, listing_id)
    row = PropertyListing.objects.get(id=listing_id)
    assert (row.status, row.restricted) == ("published", False)
    notice = Notification.objects.get(kind="report.resolved")
    assert notice.account_id == reporter.account_id and notice.payload == {}
    assert ReportTransition.objects.count() == 2
    assert AuditRecord.objects.filter(action__startswith="report.").count() == 4
    assert admin.get(f"{QUEUE}?status=resolved").json()["unresolved"] == {
        "pending": 1,
        "reviewing": 0,
    }


def test_dismissing_notifies_the_reporter_only(owner, reporter, django_capture_on_commit_callbacks):
    listing_id = publish_ready(owner.account_id)
    report_id = report(reporter, listing_id).json()["id"]
    admin = admin_client()
    post(admin, f"{QUEUE}/{report_id}/start", {})
    with django_capture_on_commit_callbacks(execute=True):
        post(admin, f"{QUEUE}/{report_id}/dismiss", {"notes": "Nothing wrong here"})
    assert [n.account_id for n in Notification.objects.all()] == [reporter.account_id]


def test_the_report_routes_are_administrator_only(owner, reporter):
    listing_id = publish_ready(owner.account_id)
    report_id = report(reporter, listing_id).json()["id"]
    for client, status in ((Client(), 401), (reporter, 404), (owner, 404)):
        assert client.get(QUEUE).status_code == status
        assert client.get(f"{QUEUE}/{report_id}").status_code == status
