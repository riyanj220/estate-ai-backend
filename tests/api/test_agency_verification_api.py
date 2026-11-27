"""Stories 8.3 and 8.4: Agency Verification requests and their review, over HTTP with CSRF on."""

import uuid

import pytest
from django.db import transaction

from apps.catalog.models import City
from apps.governance import selectors
from apps.governance import services as governance
from apps.governance.models import VerificationDecision, VerificationRequest
from apps.identity import selectors as identity
from apps.identity import services
from apps.identity.models import Account, AgencyProfile
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord, Notification
from tests.api.test_admin_api import (
    SYSTEM,
    browser,
    error_code,
    make_admin,
    make_user,
    post,
    signed_in_admin,
    signed_in_user,
    token_for,
    wired,  # noqa: F401  (autouse)
)

OWN = "/api/v1/me/agency-verification"
SUBMIT = f"{OWN}/requests"
WITHDRAW = f"{OWN}/withdraw"
QUEUE = "/api/v1/admin/verification-requests"
EMAIL = "agency@example.com"
WHY = "Reviewing the verification queue"
BODY = {"business_name": "Harbour Homes", "registration_number": "REG/2024-17", "note": "Hello"}


def make_agency(email=EMAIL, name="Harbour Homes"):
    view = make_user(email, "agency")
    AgencyProfile.objects.create(
        account_id=view.id,
        agency_name=name,
        business_phone="+923001234567",
        office_address="Block 5, Clifton",
        city_id=City.objects.get(name="Karachi").id,
    )
    return view


@pytest.fixture
def agency(db):
    return make_agency()


@pytest.fixture
def owner(agency):
    return signed_in_user(EMAIL)


@pytest.fixture
def admin(db):
    make_admin()
    return signed_in_admin()


def admin_actor():
    return Actor.admin(Account.objects.get(account_type="admin").id)


def call(client, path, body=None):
    return post(client, path, body if body is not None else {}, token_for(client))


def submit(client, **changes):
    return call(client, SUBMIT, {**BODY, **changes})


def only_request():
    return VerificationRequest.objects.get()


def decide(client, action, request_id, reason=None):
    body = {} if reason is None else {"reason": reason}
    return call(client, f"{QUEUE}/{request_id}/{action}", body)


def verified(agency):
    return identity.agency_facts([agency.id])[agency.id].verified


def test_submit_records_one_pending_request(owner, agency, django_capture_on_commit_callbacks):
    assert owner.get(OWN).json() == {
        "status": "unverified",
        "can_submit": True,
        "can_withdraw": False,
        "request": None,
    }
    response = submit(owner, registration_number="  REG/2024-17 ")
    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert (body["status"], body["can_submit"], body["can_withdraw"]) == ("pending", False, True)
    assert body["request"]["registration_number"] == "REG/2024-17"
    again = submit(owner)
    assert (again.status_code, error_code(again)) == (409, "conflict")
    assert only_request().agency_account_id == agency.id
    record = AuditRecord.objects.get(action="verification.requested")
    assert (record.target_id, record.metadata, record.reason) == (agency.id, {}, "")


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"business_name": " "}, "business_name"),
        ({"registration_number": "ab"}, "registration_number"),
        ({"registration_number": "<script>"}, "registration_number"),
        ({"registration_number": 12345}, "registration_number"),
        ({"note": "x" * 501}, "note"),
    ],
)
def test_submit_validates_fields_without_echoing(owner, changes, field):
    response = submit(owner, **changes)
    assert response.status_code == 400
    assert list(response.json()["error"]["fields"]) == [field]
    assert not VerificationRequest.objects.exists()


def test_a_client_cannot_set_the_status(owner, agency):
    response = call(owner, SUBMIT, {**BODY, "status": "verified"})
    assert response.status_code == 400
    assert not verified(agency)


def test_withdraw_then_submit_again(owner):
    assert call(owner, WITHDRAW).status_code == 409
    submit(owner)
    response = call(owner, WITHDRAW)
    assert response.status_code == 200
    assert (response.json()["status"], response.json()["can_submit"]) == ("withdrawn", True)
    assert submit(owner).status_code == 201
    assert VerificationDecision.objects.get().action == "withdrawn"


def test_only_agencies_may_use_the_owner_routes(db, admin):
    make_user("jane@example.com")
    for client, status in ((signed_in_user(), 403), (browser(), 401), (admin, 403)):
        assert client.get(OWN).status_code == status
        assert submit(client).status_code == status
        assert call(client, WITHDRAW).status_code == status
    assert not VerificationRequest.objects.exists()


def test_a_suspended_agency_reads_but_cannot_act(owner, agency, admin):
    submit(owner)
    services.suspend_account(admin_actor(), agency.id, "Policy breach")
    assert owner.get(OWN).json()["can_withdraw"] is False
    assert call(owner, WITHDRAW).status_code == 403
    assert submit(owner).status_code == 403
    response = decide(admin, "approve", only_request().id)
    assert (response.status_code, error_code(response)) == (409, "conflict")
    assert not verified(agency)


def test_the_queue_holds_no_private_detail_and_reading_needs_a_reason(owner, agency, admin):
    submit(owner)
    page = admin.get(QUEUE)
    assert page.status_code == 200
    (row,) = page.json()["results"]
    assert set(row) == {
        "id",
        "status",
        "business_name",
        "agency_name",
        "agency_id",
        "account_state",
        "submitted_at",
    }
    assert "REG/2024-17" not in page.content.decode()
    read = f"{QUEUE}/{row['id']}/read"
    assert call(admin, read, {}).status_code == 400
    assert not AuditRecord.objects.filter(action="privacy.viewed").exists()
    for _ in range(2):
        response = call(admin, read, {"reason": WHY})
        assert response.status_code == 200
    assert response.json()["registration_number"] == "REG/2024-17"
    assert response.json()["note"] == "Hello"
    records = AuditRecord.objects.filter(action="privacy.viewed")
    assert [(r.target_type, str(r.target_id), r.reason) for r in records] == [
        ("verification_request", row["id"], WHY)
    ] * 2
    assert admin.get(read).status_code == 405
    missing = call(admin, f"{QUEUE}/{uuid.uuid4()}/read", {"reason": WHY})
    assert missing.status_code == 404


def test_approve_then_revoke_with_history_notices_and_public_flag(
    owner, agency, admin, django_capture_on_commit_callbacks
):
    submit(owner)
    request = only_request()
    agency_id = AgencyProfile.objects.get(account_id=agency.id).id
    public = f"/api/v1/public/agencies/{agency_id}"
    assert browser().get(public).json()["verified"] is False

    with django_capture_on_commit_callbacks(execute=True):
        response = decide(admin, "approve", request.id)
    assert (response.status_code, response.json()["status"]) == (200, "verified")
    assert verified(agency)
    assert browser().get(public).json()["verified"] is True
    assert identity.public_advertisers([agency.id])[agency.id].verified is True
    assert owner.get(OWN).json()["status"] == "verified"
    assert submit(owner).status_code == 409
    assert decide(admin, "approve", request.id).status_code == 409
    assert decide(admin, "reject", request.id, "Too late now").status_code == 409

    assert decide(admin, "revoke", request.id).status_code == 400
    assert verified(agency)
    with django_capture_on_commit_callbacks(execute=True):
        response = decide(admin, "revoke", request.id, "Licence has lapsed")
    assert response.json()["status"] == "revoked"
    assert browser().get(public).json()["verified"] is False
    own = owner.get(OWN).json()
    assert (own["status"], own["can_submit"]) == ("revoked", True)
    assert own["request"]["decision_reason"] == "Licence has lapsed"

    history = call(admin, f"{QUEUE}/{request.id}/read", {"reason": WHY}).json()["history"]
    assert [h["action"] for h in history] == ["revoked", "approved"]
    assert all(h["by_account_id"] and h["at"] for h in history)
    assert sorted(
        AuditRecord.objects.filter(action__startswith="verification.").values_list(
            "action", flat=True
        )
    ) == ["verification.approved", "verification.requested", "verification.revoked"]
    assert sorted(
        Notification.objects.filter(account_id=agency.id).values_list("kind", "payload")
    ) == [("verification.approved", {}), ("verification.revoked", {})]
    assert submit(owner).status_code == 201


def test_reject_needs_a_clean_reason_and_allows_resubmission(
    owner, agency, admin, django_capture_on_commit_callbacks
):
    submit(owner)
    request = only_request()
    for reason in (None, "no", "Call me on 0300 1234567"):
        assert decide(admin, "reject", request.id, reason).status_code == 400
    assert decide(admin, "revoke", request.id, "Not approved yet").status_code == 409
    with django_capture_on_commit_callbacks(execute=True):
        assert decide(admin, "reject", request.id, "Number does not match").status_code == 200
    own = owner.get(OWN).json()
    assert (own["status"], own["request"]["decision_reason"]) == (
        "rejected",
        "Number does not match",
    )
    assert not verified(agency)
    assert Notification.objects.get().kind == "verification.rejected"
    assert submit(owner).status_code == 201
    assert [r["status"] for r in admin.get(f"{QUEUE}?status=rejected").json()["results"]] == [
        "rejected"
    ]


def test_admin_routes_are_closed_to_everyone_else(owner, agency):
    submit(owner)
    key = only_request().id
    for client, status in ((owner, 404), (browser(), 401)):
        assert client.get(QUEUE).status_code == status
        for action in ("read", "approve", "reject", "revoke"):
            assert call(client, f"{QUEUE}/{key}/{action}", {"reason": WHY}).status_code == status
    assert only_request().status == "pending"


def test_unknown_ids_status_and_cursor(admin):
    for action in ("approve", "reject", "revoke"):
        for key in (uuid.uuid4(), "nope"):
            body = None if action == "approve" else "A good reason"
            assert decide(admin, action, key, body).status_code == 404
    assert admin.get(f"{QUEUE}?status=bogus").status_code == 400
    assert admin.get(f"{QUEUE}?cursor=bogus").status_code == 400


def test_the_queue_pages_oldest_first(admin, db, monkeypatch):
    monkeypatch.setattr(selectors, "PAGE_SIZE", 2)
    for number in range(3):
        view = make_agency(f"a{number}@example.com", f"Agency {number}")
        governance.submit_request(Actor.user(view.id), f"Agency {number}", "REG-100")
    first = admin.get(QUEUE).json()
    second = admin.get(first["next"]).json()
    assert [r["business_name"] for r in first["results"] + second["results"]] == [
        "Agency 0",
        "Agency 1",
        "Agency 2",
    ]
    assert second["next"] is None


def test_only_an_administrator_can_set_the_flag(agency):
    for actor in (Actor.user(agency.id), SYSTEM):
        with pytest.raises(DomainError), transaction.atomic():
            services.set_agency_verification(actor, agency.id, True)
    assert not verified(agency)
