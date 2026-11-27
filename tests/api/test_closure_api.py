"""Stories 10.1 to 10.3: closure requests, their decision and the erasure an approval runs."""

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.core import mail as django_mail
from django.test import Client
from django.utils import timezone

from apps.assistant.models import AssistantSession, AssistantTurn
from apps.governance.models import (
    ClosureRequest,
    Report,
    VerificationDecision,
    VerificationRequest,
)
from apps.identity.models import Account, AgencyProfile, IndividualProfile, Session
from apps.kernel import erasure
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord, Notification, RateLimitPolicy
from apps.listings import moderation, publication, quota
from apps.listings.models import (
    ListingPrivateAddress,
    PropertyListing,
    PublicationEvent,
    SavedListing,
)
from apps.messaging.models import Conversation, Message
from apps.valuation.models import PriceEstimate, ValuationModelVersion
from tests.api.test_listings_api import signed_in
from tests.api.test_public_images_api import admin_client
from tests.api.test_reports_api import code, post
from tests.listings.support import (
    make_account,
    publish_ready,
    ready_draft,
    seed_cap,
    seed_media_policies,
    set_account_state,
    set_listing_visibility,
)

OWN = "/api/v1/me/closure-requests"
QUEUE = "/api/v1/admin/closure-requests"
WHY = "Requested by the account holder"


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    RateLimitPolicy.objects.update_or_create(
        scope="report_new",
        defaults={"limit": 10, "window_seconds": 86400, "key_kind": "account", "note": "t"},
    )


def ask(client, kind="deletion"):
    return post(client, OWN, {"kind": kind})


def decide(admin, request_id, action, reason=WHY):
    return post(admin, f"{QUEUE}/{request_id}/{action}", {"reason": reason})


def pending(client):
    return ask(client).json()["request"]["id"]


# --- Story 10.1 --------------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["unverified", "active", "suspended"])
def test_a_request_is_recorded_and_changes_nothing_else(state):
    client = signed_in(state=state)
    before = Account.objects.get(id=client.account_id).state
    assert client.get(OWN).json() == {"can_submit": True, "request": None}
    assert "request_deactivation" in client.get("/api/v1/me").json()["capabilities"]

    response = ask(client, "deactivation")

    assert response.status_code == 201 and response["Cache-Control"] == "no-store"
    body = client.get(OWN).json()
    assert body["can_submit"] is False
    assert (body["request"]["status"], body["request"]["kind"]) == ("pending", "deactivation")
    assert Account.objects.get(id=client.account_id).state == before
    assert code(ask(client)) == "conflict"
    assert ClosureRequest.objects.count() == 1
    assert AuditRecord.objects.filter(action="closure.requested").count() == 1


def test_refusals_and_other_accounts():
    owner = signed_in("owner@example.com")
    other = signed_in("other@example.com")
    request_id = pending(owner)

    assert owner.get(f"{OWN}/{request_id}").json()["status"] == "pending"
    assert code(other.get(f"{OWN}/{request_id}")) == "not_found"
    assert code(other.get(f"{OWN}/{uuid.uuid4()}")) == "not_found"
    assert code(other.get(f"{OWN}/nonsense")) == "not_found"
    assert other.get(OWN).json()["request"] is None
    assert ask(Client()).status_code == 401
    admin = admin_client()
    assert code(ask(admin)) == "forbidden"
    assert code(admin.get(OWN)) == "forbidden"
    for bad in ({}, {"kind": "other"}, {"kind": "deletion", "account_id": "x"}):
        assert post(other, OWN, bad).status_code == 400
    for client in (owner, Client()):
        assert client.get(QUEUE).status_code in (401, 404)
        assert decide(client, request_id, "approve").status_code in (401, 404)
    assert ClosureRequest.objects.get().status == "pending"


# --- Story 10.2: decisions ---------------------------------------------------------------------


def test_a_rejection_leaves_the_account_and_notifies(django_capture_on_commit_callbacks):
    owner = signed_in()
    request_id = pending(owner)
    admin = admin_client()
    row = admin.get(QUEUE).json()["results"][0]
    assert (row["masked_email"], row["account_state"]) == ("j***@example.com", "active")
    assert decide(admin, request_id, "reject", "").status_code == 400
    assert decide(admin, request_id, "reject", "call 0300 1234567").status_code == 400

    with django_capture_on_commit_callbacks(execute=True):
        response = decide(admin, request_id, "reject", "Please settle the open report first")

    assert response.json()["status"] == "rejected"
    body = owner.get(OWN).json()
    assert body["can_submit"] is True
    assert body["request"]["decision_reason"] == "Please settle the open report first"
    assert Account.objects.get(id=owner.account_id).state.value == "active"
    assert Notification.objects.filter(kind="account.closure_rejected").count() == 1
    assert django_mail.outbox[-1].to == ["jane.doe@example.com"]
    assert "settle the open report" in django_mail.outbox[-1].body
    assert code(decide(admin, request_id, "approve")) == "conflict"
    assert code(decide(admin, uuid.uuid4(), "approve")) == "not_found"
    assert admin.get(f"{QUEUE}?status=rejected").json()["results"][0]["id"] == request_id
    assert ask(owner).status_code == 201


def test_a_failing_eraser_rolls_everything_back(monkeypatch, django_capture_on_commit_callbacks):
    owner = signed_in()
    listing_id = publish_ready(owner.account_id)
    request_id = pending(owner)
    admin = admin_client()

    def fail(actor, account_id):
        raise RuntimeError("boom")

    monkeypatch.setitem(erasure._erasers, "identity", fail)  # the last one called
    admin.raise_request_exception = False
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        assert decide(admin, request_id, "approve").status_code == 500

    assert not callbacks and not django_mail.outbox
    assert ClosureRequest.objects.get().status == "pending"
    assert Account.objects.get(id=owner.account_id).state.value == "active"
    assert PropertyListing.objects.get(id=listing_id).status == "published"
    assert owner.get("/api/v1/me").status_code == 200
    assert not AuditRecord.objects.filter(action="account.deactivated").exists()


def test_every_data_owning_app_registers_an_eraser_in_layer_order():
    assert erasure.registered() == [
        "governance",
        "messaging",
        "assistant",
        "valuation",
        "listings",
        "identity",
    ]


# --- Stories 10.2 and 10.3: erasure ------------------------------------------------------------


def _estimate(account_id, model):
    return PriceEstimate.objects.create(
        account_id=account_id,
        model_version=model,
        feature_schema_version=1,
        inputs={},
        inputs_sha256="0" * 64,
        value=Decimal("100.00"),
        created_at=timezone.now(),
    )


def _everything(owner, other):
    """One of each thing the account holds, and some of another account's."""
    account_id = owner.account_id
    ids = {"published": publish_ready(account_id)}
    for status in ("sold", "removed", "archived"):
        ids[status] = publish_ready(account_id)
        set_listing_visibility(ids[status], status=status)
    PropertyListing.objects.filter(id=ids["removed"]).update(
        removal_reason="r", restricted=True, restriction_reason="r"
    )
    ids["draft"] = ready_draft(SimpleNamespace(id=account_id)).id
    for listing_id in ids.values():
        ListingPrivateAddress.objects.update_or_create(
            listing_id=listing_id, defaults={"street": "Hidden Lane"}
        )
    theirs = publish_ready(other.account_id)
    SavedListing.objects.create(account_id=account_id, listing_id=theirs)
    SavedListing.objects.create(account_id=other.account_id, listing_id=ids["published"])

    model = ValuationModelVersion.objects.create(
        name="m",
        version=1,
        feature_schema_version=1,
        metrics={},
        trained_at=timezone.now(),
        artifact_sha256="a" * 64,
    )
    _estimate(account_id, model)
    _estimate(other.account_id, model)
    for holder, listing_id in ((account_id, ids["draft"]), (other.account_id, theirs)):
        session = AssistantSession.objects.create(
            account_id=holder,
            listing_id=listing_id,
            registry_version=1,
            schema_version=1,
            created_at=timezone.now(),
        )
        AssistantTurn.objects.create(
            session=session,
            sequence=1,
            client_turn_id=uuid.uuid4(),
            user_text="hello",
            created_at=timezone.now(),
        )

    conversation = Conversation.objects.create(
        listing_id=theirs,
        initiator_account_id=account_id,
        owner_account_id=other.account_id,
        last_seq=1,
    )
    Message.objects.create(
        conversation=conversation,
        seq=1,
        sender_account_id=account_id,
        body="call me on 0300 1234567",
        client_message_id="c1",
    )
    message_id = str(Message.objects.get().id)
    assert (
        post(
            other,
            f"/api/v1/conversations/{conversation.id}/reports",
            {"message_ids": [message_id], "reason": "abusive"},
        ).status_code
        == 201
    )
    return ids, conversation


def test_an_approval_erases_the_account(django_capture_on_commit_callbacks):
    owner = signed_in("owner@example.com", "agency")
    AgencyProfile.objects.create(
        account_id=owner.account_id,
        agency_name="Real Estates",
        business_phone="+923001234567",
        office_address="12 Street",
        city_id=uuid.uuid4(),
        description="About us",
        verification_status="verified",
    )
    other = signed_in("other@example.com")
    ids, conversation = _everything(owner, other)
    for status in ("verified", "pending"):
        VerificationRequest.objects.create(
            agency_account_id=owner.account_id,
            business_name="Real Estates",
            registration_number="REG-1",
            note="a note",
            status=status,
            submitted_at=timezone.now(),
        )
    events = PublicationEvent.objects.count()
    audits = AuditRecord.objects.count()
    request_id = pending(owner)
    admin = admin_client()

    with django_capture_on_commit_callbacks(execute=True):
        response = decide(admin, request_id, "approve")

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["account_state"], body["masked_email"]) == (
        "approved",
        "deactivated",
        None,
    )
    account = Account.objects.get(id=owner.account_id)
    assert account.email == f"closed-{account.id.hex}@closed.estate-ai.invalid"
    assert not account.password.startswith(("argon", "pbkdf2", "bcrypt", "scrypt", "md5"))
    profile = AgencyProfile.objects.get(account_id=account.id)
    assert (profile.agency_name, profile.business_phone, profile.office_address) == (
        "Deleted agency",
        "",
        "",
    )
    assert (profile.description, profile.verification_status) == ("", "unverified")
    assert not Session.objects.filter(account_id=account.id, ended_at=None).exists()
    assert owner.get("/api/v1/me").status_code == 401

    statuses = {k: PropertyListing.objects.get(id=v).status for k, v in ids.items()}
    assert statuses == {
        "published": "archived",
        "sold": "archived",
        "removed": "archived",
        "archived": "archived",
        "draft": "draft",
    }
    assert PropertyListing.objects.get(id=ids["removed"]).restricted
    assert not ListingPrivateAddress.objects.filter(listing__owner_account_id=account.id).exists()
    assert not SavedListing.objects.filter(account_id=account.id).exists()
    assert SavedListing.objects.filter(account_id=other.account_id).count() == 1
    assert list(PriceEstimate.objects.values_list("account_id", flat=True)) == [other.account_id]
    assert list(AssistantSession.objects.values_list("account_id", flat=True)) == [other.account_id]
    assert AssistantTurn.objects.count() == 1

    requests = VerificationRequest.objects.all()
    assert {r.status for r in requests} == {"revoked", "withdrawn"}
    assert {(r.registration_number, r.note, r.business_name) for r in requests} == {
        ("", "", "Deleted agency")
    }
    assert VerificationDecision.objects.count() == 2

    assert Message.objects.get().body == "call me on 0300 1234567"  # the other side keeps it
    seen = other.get(f"/api/v1/conversations/{conversation.id}").json()
    assert seen["counterpart_name"] is None
    snapshot = Report.objects.get().snapshot
    assert snapshot["messages"][0]["sender"] == "deleted_user"
    assert "1234567" not in str(snapshot)

    assert PublicationEvent.objects.count() == events
    assert AuditRecord.objects.count() > audits
    for app in erasure.registered():
        assert AuditRecord.objects.filter(action=f"{app}.account_erased").count() == 1
    sent = [m for m in django_mail.outbox if m.to == ["owner@example.com"]]
    assert len(sent) == 1 and WHY in sent[0].body
    assert not Notification.objects.filter(account_id=account.id).exists()
    assert code(decide(admin, request_id, "approve")) == "conflict"
    assert Client().get(f"/api/v1/public/listings/{ids['published']}").status_code == 404


def test_a_suspended_account_can_be_closed_and_the_address_starts_afresh(
    django_capture_on_commit_callbacks,
):
    owner = signed_in("owner@example.com")
    publish_ready(owner.account_id)
    set_account_state(owner.account_id, "suspended")
    request_id = pending(owner)
    with django_capture_on_commit_callbacks(execute=True):
        assert decide(admin_client(), request_id, "approve").status_code == 200

    assert IndividualProfile.objects.filter(phone__gt="").count() == 0
    again = make_account("owner@example.com")
    assert again.id != owner.account_id
    assert not PublicationEvent.objects.filter(account_id=again.id).exists()
    assert quota.allowance_for(again.id, "individual", timezone.now()).used == 0


def test_a_closed_accounts_listing_never_returns(django_capture_on_commit_callbacks):
    owner = signed_in("owner@example.com")
    listing_id = publish_ready(owner.account_id)
    administrator = Actor.admin(uuid.uuid4(), "r")
    moderation.remove(administrator, listing_id, "Misleading photos")
    set_account = Account.objects.filter(id=owner.account_id)
    set_account.update(deactivated_at=timezone.now())  # still Removed: no erasure ran

    with pytest.raises(DomainError) as refused:
        publication.restore_after_moderation(administrator, listing_id)
    assert refused.value.code == "conflict"
    assert PropertyListing.objects.get(id=listing_id).status == "removed"
