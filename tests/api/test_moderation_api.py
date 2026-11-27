"""Stories 9.3 and 9.4: administrator Listing review and moderation actions, including the
administrator column of the FR-75 table."""

import json
from types import SimpleNamespace

import pytest
from django.test import Client
from django.utils import timezone

from apps.governance.models import ModerationAction, Report
from apps.kernel.models import AuditRecord, Notification
from apps.listings import quota
from apps.listings.models import ListingPrivateAddress, PropertyListing, PublicationEvent
from tests.api.test_listings_api import signed_in
from tests.api.test_public_images_api import admin_client
from tests.api.test_reports_api import code, post, report
from tests.listings.support import (
    publish_ready,
    ready_draft,
    seed_cap,
    seed_media_policies,
    set_account_state,
    set_listing_visibility,
)

LISTINGS = "/api/v1/admin/listings"
WHY = "Photos do not match the property"
STREET = "Hidden Lane Seventeen"
PATHS = {
    "restrict": "restrict",
    "lift_restriction": "lift-restriction",
    "remove": "remove",
    "restore": "restore",
    "archive_removed": "archive",
}


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()


@pytest.fixture
def owner():
    return signed_in("owner@example.com")


@pytest.fixture
def admin():
    return admin_client()


def act(client, listing_id, action, reason=WHY):
    return post(client, f"{LISTINGS}/{listing_id}/{PATHS[action]}", {"reason": reason})


def row(listing_id):
    return PropertyListing.objects.get(id=listing_id)


def public(listing_id) -> int:
    return Client().get(f"/api/v1/public/listings/{listing_id}").status_code


def mine(owner, listing_id):
    return owner.get(f"/api/v1/listings/{listing_id}").json()


# FR-75: (status, restricted) -> the administrator's actions
TABLE = {
    ("draft", False): [],
    ("published", False): ["restrict", "remove"],
    ("published", True): ["lift_restriction", "remove"],
    ("archived", False): ["restrict"],
    ("archived", True): ["lift_restriction"],
    ("sold", False): [],
    ("rented", False): [],
    ("removed", False): ["restore", "archive_removed"],
    ("removed", True): ["restore", "archive_removed", "lift_restriction"],
}


@pytest.mark.parametrize(("status", "restricted"), list(TABLE))
def test_the_fr75_administrator_column(owner, admin, status, restricted):
    listing_id = publish_ready(owner.account_id)
    allowed = TABLE[status, restricted]
    for action in PATHS:
        set_listing_visibility(listing_id, status, restricted)
        response = act(admin, listing_id, action)
        assert response.status_code == (200 if action in allowed else 409), action
    set_listing_visibility(listing_id, status, restricted)
    if status != "draft":
        assert admin.get(f"{LISTINGS}/{listing_id}").json()["actions"] == allowed


@pytest.mark.parametrize("action", ["restrict", "remove"])
def test_a_hidden_listing_leaves_public_view_and_the_owner_is_told(
    owner, admin, action, django_capture_on_commit_callbacks
):
    listing_id = publish_ready(owner.account_id)
    image = mine(owner, listing_id)["media"][0]["url"]
    revision = row(listing_id).revision
    with django_capture_on_commit_callbacks(execute=True):
        response = act(admin, listing_id, action)
    assert response.status_code == 200 and response.json()["reason"] == WHY
    assert public(listing_id) == 404
    assert Client().get("/api/v1/public/listings").json()["results"] == []
    assert Client().get(image).status_code == 404
    body = mine(owner, listing_id)
    assert body["restriction_reason"] == WHY and body["visibility"] == "private"
    assert row(listing_id).revision == revision + 1
    kind = "listing.restricted" if action == "restrict" else "listing.removed"
    notice = Notification.objects.get(kind=kind)
    assert notice.account_id == owner.account_id
    assert notice.payload == {"listing_id": str(listing_id), "reason": WHY}
    record = ModerationAction.objects.get()
    assert (record.action, record.reason, record.status_before) == (action, WHY, "published")
    audit = AuditRecord.objects.get(action=kind)
    assert audit.target_id == listing_id and audit.actor_kind == "admin"


def test_restore_consumes_no_quota_and_writes_a_restore_event(
    owner, admin, django_capture_on_commit_callbacks
):
    listing_id = publish_ready(owner.account_id)
    before = quota.allowance_for(owner.account_id, "individual", timezone.now()).used
    assert act(admin, listing_id, "remove").status_code == 200
    with django_capture_on_commit_callbacks(execute=True):
        assert act(admin, listing_id, "restore", "Reviewed again and fine").status_code == 200
    assert public(listing_id) == 200
    assert quota.allowance_for(owner.account_id, "individual", timezone.now()).used == before
    event = PublicationEvent.objects.get(kind="restore")
    assert not event.counted and event.listing_id == listing_id
    assert row(listing_id).removal_reason is None
    assert mine(owner, listing_id)["restriction_reason"] is None
    assert Notification.objects.filter(kind="listing.restored").count() == 1


def test_a_restriction_persists_across_removal_restoration_and_republication(owner, admin):
    listing_id = publish_ready(owner.account_id)
    act(admin, listing_id, "restrict", "Serious risk suspected")
    act(admin, listing_id, "remove", "Removed after the check")
    assert mine(owner, listing_id)["restriction_reason"] == "Removed after the check"
    act(admin, listing_id, "restore", "Back again for now")
    assert (row(listing_id).status, row(listing_id).restricted) == ("published", True)
    assert public(listing_id) == 404
    assert mine(owner, listing_id)["restriction_reason"] == "Serious risk suspected"
    revision = row(listing_id).revision
    url = f"/api/v1/listings/{listing_id}"
    archived = post(owner, f"{url}/status", {"base_revision": revision, "status": "archived"})
    assert archived.status_code == 200
    token = owner.get("/api/v1/csrf").json()["csrf_token"]
    again = owner.post(
        f"{url}/republish",
        json.dumps({"base_revision": revision + 1}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
        HTTP_IDEMPOTENCY_KEY="again-1",
    )
    assert code(again) == "forbidden"
    act(admin, listing_id, "lift_restriction", "Cleared after review")
    assert (row(listing_id).status, row(listing_id).restricted) == ("archived", False)


def test_a_removed_listing_can_be_archived(owner, admin):
    listing_id = publish_ready(owner.account_id)
    act(admin, listing_id, "remove")
    assert act(admin, listing_id, "archive_removed", "Kept out of public view").status_code == 200
    assert row(listing_id).status == "archived" and row(listing_id).removal_reason is None


def test_a_reported_listing_stays_visible_and_reports_do_not_change(owner, admin):
    listing_id = publish_ready(owner.account_id)
    report(signed_in("reporter@example.com"), listing_id)
    assert public(listing_id) == 200
    act(admin, listing_id, "restrict")
    assert Report.objects.get().status == "pending"


@pytest.mark.parametrize(
    "reason",
    ["", "abc", "x" * 121, "Call 0300 1234567 now", "mail me a@b.com", "see www.example.com now"],
)
def test_a_bad_reason_changes_nothing(owner, admin, reason):
    listing_id = publish_ready(owner.account_id)
    response = act(admin, listing_id, "restrict", reason)
    assert response.status_code == 400 and response.json()["error"]["fields"]["reason"]
    assert not row(listing_id).restricted and not ModerationAction.objects.exists()


def test_the_review_list_filters_and_hides_draft_content(owner, admin):
    published = publish_ready(owner.account_id)
    other = signed_in("agency@example.com", "agency")
    draft = ready_draft(SimpleNamespace(id=other.account_id))

    def ids(query=""):
        return [r["id"] for r in admin.get(f"{LISTINGS}{query}").json()["results"]]

    assert ids() == [str(published)]
    assert ids("?status=draft") == [str(draft.id)]
    drafted = admin.get(f"{LISTINGS}?status=draft").json()["results"][0]
    assert drafted["title"] is None and drafted["owner"]["masked_email"] == "a***@example.com"
    assert ids(f"?owner={owner.account_id}") == [str(published)]
    assert ids(f"?owner={other.account_id}") == []
    assert ids("?category=house") == [str(published)] and ids("?category=plot") == []
    assert ids("?status=sold") == []
    assert ids("?created_from=2000-01-01") == [str(published)]
    assert ids("?created_to=2000-01-01") == []
    for query in ("?status=x", "?owner=x", "?category=x", "?created_from=x", "?cursor=!"):
        assert admin.get(f"{LISTINGS}{query}").status_code == 400, query


def test_detail_shows_public_content_and_images_never_the_private_address(owner, admin):
    listing_id = publish_ready(owner.account_id)
    ListingPrivateAddress.objects.update_or_create(
        listing_id=listing_id, defaults={"street": STREET}
    )
    set_listing_visibility(listing_id, "archived")
    response = admin.get(f"{LISTINGS}/{listing_id}")
    body = response.json()
    assert body["title"] and body["media"] and body["city"] and body["actions"] == ["restrict"]
    assert STREET not in response.content.decode()
    set_account_state(owner.account_id, "suspended")
    assert admin.get(f"{LISTINGS}/{listing_id}").status_code == 200


def test_a_draft_is_read_only_with_a_recorded_reason(owner, admin):
    draft = ready_draft(SimpleNamespace(id=owner.account_id))
    ListingPrivateAddress.objects.update_or_create(listing_id=draft.id, defaults={"street": STREET})
    assert code(admin.get(f"{LISTINGS}/{draft.id}")) == "not_found"
    assert code(act(admin, draft.id, "remove")) == "conflict"
    url = f"{LISTINGS}/{draft.id}/read"
    assert post(admin, url, {}).status_code == 400
    response = post(admin, url, {"reason": "Checking a user complaint"})
    body = response.json()
    assert body["title"] and body["media"] == [] and body["image_count"] == 1
    assert STREET not in response.content.decode()
    viewed = AuditRecord.objects.get(action="privacy.viewed")
    assert viewed.target_type == "listing_draft" and viewed.reason == "Checking a user complaint"
    published = publish_ready(owner.account_id)
    assert code(post(admin, f"{LISTINGS}/{published}/read", {"reason": "x" * 20})) == "not_found"


def test_everyone_else_is_denied(owner):
    listing_id = publish_ready(owner.account_id)
    for client, status in ((Client(), 401), (owner, 404)):
        assert client.get(LISTINGS).status_code == status
        assert client.get(f"{LISTINGS}/{listing_id}").status_code == status
    assert act(owner, listing_id, "restrict").status_code == 404
    assert not row(listing_id).restricted
