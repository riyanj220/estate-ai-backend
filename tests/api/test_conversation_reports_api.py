"""Story 9.5: reporting Messages of a Conversation, the reasoned read and anonymisation."""

import uuid

import pytest
from django.test import Client
from django.utils import timezone

from apps.governance import reports
from apps.governance.models import Report
from apps.identity import services as identity_services
from apps.kernel import retention
from apps.kernel.actor import Actor
from apps.kernel.models import AuditRecord, RateLimitPolicy
from apps.messaging.models import Conversation, Message
from tests.api.test_listings_api import signed_in
from tests.api.test_public_images_api import admin_client
from tests.api.test_reports_api import QUEUE, code, post
from tests.listings.support import publish_ready, seed_cap, seed_media_policies, set_account_state

WHY = "Checking the reported messages"


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
def buyer():
    return signed_in("buyer@example.com")


@pytest.fixture
def thread(owner, buyer):
    """Messages 1 to 10, odd by the buyer and even by the owner."""
    conversation = Conversation.objects.create(
        listing_id=publish_ready(owner.account_id),
        initiator_account_id=buyer.account_id,
        owner_account_id=owner.account_id,
        last_seq=10,
    )
    conversation.sent = {
        seq: Message.objects.create(
            conversation=conversation,
            seq=seq,
            sender_account_id=buyer.account_id if seq % 2 else owner.account_id,
            body=f"message number {seq}",
            client_message_id=f"c{seq}",
            created_at=timezone.now(),
        )
        for seq in range(1, 11)
    }
    return conversation


def send_report(client, thread, seqs, reason="abusive", **extra):
    ids = [str(thread.sent[seq].id) for seq in seqs]
    return post(
        client,
        f"/api/v1/conversations/{thread.id}/reports",
        {"message_ids": ids, "reason": reason, **extra},
    )


def read(admin, report_id, reason=WHY):
    return post(admin, f"{QUEUE}/{report_id}/read", {"reason": reason})


def test_the_snapshot_holds_the_selected_and_five_before_only(owner, buyer, thread):
    response = send_report(buyer, thread, [8, 10], description="Rude replies")

    assert response.status_code == 201 and response["Cache-Control"] == "no-store"
    body = response.json()
    assert (body["target_type"], body["conversation_id"]) == ("conversation", str(thread.id))
    assert body["listing_id"] == str(thread.listing_id)
    row = Report.objects.get()
    kept = row.snapshot["messages"]
    assert [m["seq"] for m in kept] == [3, 4, 5, 6, 7, 8, 10]
    assert [m["seq"] for m in kept if m["selected"]] == [8, 10]
    assert row.subject_account_id == owner.account_id
    assert Message.objects.count() == 10
    assert AuditRecord.objects.filter(action="report.submitted").count() == 1


def test_who_may_report_and_what(owner, buyer, thread):
    own = send_report(buyer, thread, [1])  # the buyer's own Message
    assert own.json()["error"]["fields"]["message_ids"]
    url = f"/api/v1/conversations/{thread.id}/reports"
    for bad in ([], [str(uuid.uuid4())], "x", [str(thread.sent[2].id)] * 11):
        answer = post(buyer, url, {"message_ids": bad, "reason": "abusive"})
        assert answer.json()["error"]["fields"]["message_ids"]
    assert code(send_report(signed_in("third@example.com"), thread, [2])) == "not_found"
    assert code(send_report(admin_client(), thread, [2])) == "forbidden"
    assert send_report(Client(), thread, [2]).status_code == 401
    assert not Report.objects.exists()
    assert send_report(owner, thread, [1]).status_code == 201  # either side may report
    assert code(send_report(owner, thread, [3])) == "conflict"


@pytest.mark.parametrize(
    ("state", "expected"), [("unverified", 201), ("active", 201), ("suspended", 403)]
)
def test_the_report_capability_for_conversations(buyer, thread, state, expected):
    set_account_state(buyer.account_id, state)
    assert send_report(buyer, thread, [2]).status_code == expected


def test_administrators_read_messages_only_with_a_reason(buyer, thread):
    report_id = send_report(buyer, thread, [8], description="Rude replies").json()["id"]
    admin = admin_client()

    queue = admin.get(QUEUE)
    assert queue.json()["results"][0]["target_type"] == "conversation"
    plain = admin.get(f"{QUEUE}/{report_id}")
    assert plain.json()["messages"] is None and plain.json()["snapshot"] is None
    assert plain.json()["description"] == ""
    started = post(admin, f"{QUEUE}/{report_id}/start", {})
    for response in (queue, plain, started):
        assert "message number" not in response.content.decode()
        assert "Rude replies" not in response.content.decode()

    assert read(admin, report_id, "").status_code == 400
    assert not AuditRecord.objects.filter(reason=WHY).exists()
    first = read(admin, report_id)
    assert [m["seq"] for m in first.json()["messages"]] == [3, 4, 5, 6, 7, 8]
    assert first.json()["description"] == "Rude replies"
    assert read(admin, report_id).status_code == 200
    assert AuditRecord.objects.filter(reason=WHY, target_type="report_snapshot").count() == 2
    assert admin.get(f"/api/v1/conversations/{thread.id}/messages").status_code == 404
    for client in (buyer, Client()):
        assert read(client, report_id).status_code in (401, 404)
    done = post(admin, f"{QUEUE}/{report_id}/resolve", {"notes": "Confirmed abuse"})
    assert done.json()["status"] == "resolved" and done.json()["messages"] is None


def test_a_listing_report_has_no_reasoned_read(owner, buyer, thread):
    made = post(buyer, f"/api/v1/listings/{thread.listing_id}/reports", {"reason": "other"})
    assert code(read(admin_client(), made.json()["id"])) == "not_found"


def _with_a_phone(thread, seq):
    Message.objects.filter(id=thread.sent[seq].id).update(body="call me on 0300 1234567 now")


def test_deactivation_anonymises_the_snapshot(
    owner, buyer, thread, django_capture_on_commit_callbacks
):
    _with_a_phone(thread, 2)
    send_report(buyer, thread, [2], description="Sent 0300 1234567")
    admin_client()
    administrator = Actor.admin(uuid.uuid4(), "r")
    with django_capture_on_commit_callbacks(execute=True):
        identity_services.deactivate(administrator, owner.account_id)

    row = Report.objects.get()
    senders = {m["seq"]: m["sender"] for m in row.snapshot["messages"]}
    assert senders == {1: "reporter", 2: "deleted_user"}
    assert "1234567" not in row.snapshot["messages"][1]["body"] + row.description
    assert "call me on" in row.snapshot["messages"][1]["body"]
    assert reports.anonymise_participant(owner.account_id) == 0  # once


def test_retention_repairs_a_missed_anonymisation(owner, buyer, thread):
    _with_a_phone(thread, 2)
    send_report(buyer, thread, [2])
    set_account_state(owner.account_id, "deactivated")
    assert "governance.report_snapshots" in retention.registered()
    assert reports.purge_deactivated_snapshots(timezone.now(), dry_run=True) == 1
    assert "1234567" in str(Report.objects.get().snapshot)
    assert reports.purge_deactivated_snapshots(timezone.now()) == 1
    assert "1234567" not in str(Report.objects.get().snapshot)
    assert reports.purge_deactivated_snapshots(timezone.now()) == 0


def test_reporting_a_deactivated_counterpart_stores_it_anonymised(owner, buyer, thread):
    _with_a_phone(thread, 2)
    set_account_state(owner.account_id, "deactivated")
    assert send_report(buyer, thread, [2]).status_code == 201
    assert "1234567" not in str(Report.objects.get().snapshot)
