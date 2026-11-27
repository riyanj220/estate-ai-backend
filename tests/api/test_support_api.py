"""Story 10.9: support requests, their reasoned read, closing and erasure."""

import json
import uuid

import pytest

from apps.governance.models import ClosureRequest, SupportRequest
from apps.kernel.models import AuditRecord, Notification, RateLimitPolicy
from tests.api.test_listings_api import signed_in
from tests.api.test_public_images_api import admin_client
from tests.api.test_reports_api import code, post
from tests.listings.support import seed_cap, seed_media_policies, set_account_state

OWN = "/api/v1/me/support-requests"
QUEUE = "/api/v1/admin/support-requests"
CLOSURES = "/api/v1/admin/closure-requests"
TEXT = "Please explain why my listing was removed."
WHY = "Answering the support request"


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    RateLimitPolicy.objects.update_or_create(
        scope="support_requests",
        defaults={"limit": 5, "window_seconds": 86400, "key_kind": "account", "note": "t"},
    )


def send(client, category="decision", message=TEXT):
    return post(client, OWN, {"category": category, "message": message})


@pytest.mark.parametrize("state", ["unverified", "active", "suspended"])
def test_a_request_is_recorded_and_seen_by_its_author(state, django_capture_on_commit_callbacks):
    client = signed_in(state=state)
    assert client.get(OWN).json() == {"results": []}
    assert "request_support" in client.get("/api/v1/me").json()["capabilities"]

    response = send(client, message=f"  {TEXT}\r\nThank you.  ")

    assert response.status_code == 201 and response["Cache-Control"] == "no-store"
    body = response.json()
    assert (body["status"], body["category"]) == ("open", "decision")
    assert body["message"] == f"{TEXT}\nThank you."
    assert client.get(OWN).json()["results"] == [body]
    assert client.get(f"{OWN}/{body['id']}").json() == body
    record = AuditRecord.objects.get(action="support.submitted")
    assert (record.metadata, str(record.target_id)) == ({}, body["id"])


def test_the_sixth_request_of_a_day_is_throttled_and_not_recorded():
    client = signed_in()
    for _ in range(5):
        assert send(client).status_code == 201
    response = send(client)
    assert code(response) == "throttled" and response["Retry-After"]
    assert SupportRequest.objects.count() == 5


@pytest.mark.parametrize(
    ("category", "message", "field"),
    [
        (None, TEXT, "category"),
        ("refund", TEXT, "category"),
        ("other", "", "message"),
        ("other", "short", "message"),
        ("other", "x" * 2001, "message"),
        ("other", "has a \x00 character in it", "message"),
        ("other", 12, "message"),
    ],
)
def test_invalid_input_is_refused(category, message, field):
    client = signed_in()
    response = send(client, category, message)
    assert code(response) == "validation_error"
    assert list(response.json()["error"]["fields"]) == [field]
    assert not SupportRequest.objects.exists()


def test_others_cannot_see_a_request():
    owner = signed_in()
    key = send(owner).json()["id"]
    other = signed_in("other.person@example.com")
    for path in (f"{OWN}/{key}", f"{OWN}/{uuid.uuid4()}", f"{OWN}/nonsense"):
        assert code(other.get(path)) == "not_found"
    assert other.get(OWN).json() == {"results": []}
    admin = admin_client()
    assert code(admin.get(OWN)) == "forbidden"
    assert code(send(admin)) == "forbidden"
    for path in (QUEUE, f"{QUEUE}/{key}/read", f"{QUEUE}/{key}/close"):
        response = owner.get(path) if path == QUEUE else post(owner, path, {"reason": WHY})
        assert code(response) == "not_found"


def test_the_queue_hides_the_text_until_a_reason_is_recorded():
    owner = signed_in()
    key = send(owner).json()["id"]
    admin = admin_client()

    listing = admin.get(QUEUE)

    assert listing.status_code == 200 and listing["Cache-Control"] == "no-store"
    row = listing.json()["results"][0]
    assert row["id"] == key and "message" not in row
    assert TEXT not in json.dumps(listing.json())
    assert row["masked_email"] and "***" in row["masked_email"]

    assert code(post(admin, f"{QUEUE}/{key}/read", {"reason": "short"})) == "validation_error"
    assert not AuditRecord.objects.filter(action="privacy.viewed").exists()
    read = post(admin, f"{QUEUE}/{key}/read", {"reason": WHY})
    assert read.json()["message"] == TEXT
    record = AuditRecord.objects.get(action="privacy.viewed")
    assert (record.target_type, str(record.target_id), record.reason) == (
        "support_request",
        key,
        WHY,
    )
    assert code(post(admin, f"{QUEUE}/{uuid.uuid4()}/read", {"reason": WHY})) == "not_found"
    assert code(post(admin, f"{QUEUE}/nonsense/read", {"reason": WHY})) == "not_found"


def test_closing_notifies_the_author_once(django_capture_on_commit_callbacks):
    owner = signed_in(state="suspended")
    key = send(owner).json()["id"]
    admin = admin_client()

    with django_capture_on_commit_callbacks(execute=True):
        response = post(admin, f"{QUEUE}/{key}/close", {})

    assert response.json()["status"] == "closed" and response.json()["closed_at"]
    assert code(post(admin, f"{QUEUE}/{key}/close", {})) == "conflict"
    assert code(post(admin, f"{QUEUE}/{uuid.uuid4()}/close", {})) == "not_found"
    notification = Notification.objects.get(kind="support.closed")
    assert (str(notification.account_id), notification.payload) == (str(owner.account_id), {})
    assert AuditRecord.objects.filter(action="support.closed").count() == 1
    assert owner.get(f"{OWN}/{key}").json()["status"] == "closed"
    assert admin.get(QUEUE).json()["results"] == []
    assert [r["id"] for r in admin.get(f"{QUEUE}?status=closed").json()["results"]] == [key]
    assert code(admin.get(f"{QUEUE}?status=other")) == "validation_error"
    assert code(admin.get(f"{QUEUE}?cursor=nonsense")) == "validation_error"


def test_a_rolled_back_close_notifies_nobody(django_capture_on_commit_callbacks, monkeypatch):
    from apps.governance import support
    from apps.kernel.actor import Actor

    owner = signed_in()
    key = send(owner).json()["id"]
    admin = admin_client()
    admin_id = uuid.UUID(admin.get("/api/v1/me").json()["id"])

    def boom(*args, **kwargs):
        raise RuntimeError("fault")

    monkeypatch.setattr(support.audit, "record", boom)
    with django_capture_on_commit_callbacks(execute=True), pytest.raises(RuntimeError):
        support.close_request(Actor.admin(admin_id), key)
    assert not Notification.objects.filter(kind="support.closed").exists()
    assert SupportRequest.objects.get().status == "open"


def test_the_queue_pages_oldest_first():
    owner = signed_in()
    from django.utils import timezone

    rows = SupportRequest.objects.bulk_create(
        SupportRequest(
            account_id=owner.account_id,
            category="other",
            message=TEXT,
            submitted_at=timezone.now(),
        )
        for _ in range(21)
    )
    admin = admin_client()
    first = admin.get(QUEUE).json()
    assert len(first["results"]) == 20 and first["next"]
    second = admin.get(first["next"]).json()
    assert second["next"] is None
    seen = [r["id"] for r in first["results"] + second["results"]]
    assert sorted(seen) == sorted(str(r.id) for r in rows)
    assert len(owner.get(OWN).json()["results"]) == 20


def test_account_closure_empties_the_messages_and_closes_open_requests():
    owner = signed_in()
    key = send(owner).json()["id"]
    other = signed_in("other.person@example.com")
    send(other)
    assert post(owner, "/api/v1/me/closure-requests", {"kind": "deletion"}).status_code == 201
    admin = admin_client()
    request_id = ClosureRequest.objects.get().id

    assert post(admin, f"{CLOSURES}/{request_id}/approve", {"reason": WHY}).status_code == 200

    mine = SupportRequest.objects.get(id=key)
    assert (mine.message, mine.status) == ("", "closed") and mine.closed_at
    theirs = SupportRequest.objects.exclude(id=key).get()
    assert (theirs.message, theirs.status) == (TEXT, "open")
    assert post(admin, f"{QUEUE}/{key}/read", {"reason": WHY}).json()["message"] == ""
    assert not Notification.objects.filter(kind="support.closed").exists()


def test_a_deactivated_account_cannot_submit():
    client = signed_in()
    set_account_state(client.account_id, "deactivated")
    assert code(send(client)) in {"unauthenticated", "forbidden"}
    assert not SupportRequest.objects.exists()
