"""Story 2.21: the administrator account routes over HTTP, with Django's CSRF checks on."""

import logging
import threading
import uuid

import pytest
from django.db import connection

from apps.identity import services
from apps.identity.models import Account, Session
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord, Notification
from tests.api.test_admin_api import (
    ADMIN_EMAIL,
    SESSION,
    SYSTEM,
    USER_PASSWORD,
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

BASE = "/api/v1/admin/accounts"
LOOKUP = f"{BASE}/lookup"
ME = "/api/v1/me"
EMAIL = "jane@example.com"
WHY = "Reviewing an abuse complaint"
REASON = "Repeated policy canary breaches"


@pytest.fixture
def admin(db):
    make_admin()
    return signed_in_admin()


def call(client, path, body, csrf=True):
    return post(client, path, body, token_for(client) if csrf else None)


def lookup(client, email=EMAIL, reason=WHY):
    return call(client, LOOKUP, {"email": email, "reason": reason})


def suspend(client, account_id, reason=REASON):
    return call(client, f"{BASE}/{account_id}/suspend", {"reason": reason})


def reactivate(client, account_id, reason="Appeal upheld"):
    return call(client, f"{BASE}/{account_id}/reactivate", {"reason": reason})


def test_lookup_returns_the_summary_and_records_the_reason(admin):
    user = make_user(EMAIL)
    response = lookup(admin, "  JANE@Example.com ")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "id": str(user.id),
        "email": EMAIL,
        "account_type": "individual",
        "display_name": "",
        "state": "active",
        "email_verified": True,
        "suspension_reason": "",
        "suspended_at": None,
    }
    record = AuditRecord.objects.get(action="privacy.viewed")
    assert (record.target_type, record.target_id, record.reason) == ("account", user.id, WHY)


def test_unknown_and_administrator_addresses_are_not_found_and_unaudited(admin):
    for email in ("nobody@example.com", ADMIN_EMAIL):
        response = lookup(admin, email)
        assert (response.status_code, error_code(response)) == (404, "not_found")
        assert response.headers["Cache-Control"] == "no-store"
    assert not AuditRecord.objects.filter(action="privacy.viewed").exists()


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"email": EMAIL}, "reason"),
        ({"reason": WHY}, "email"),
        ({"email": EMAIL, "reason": "short"}, "reason"),
        ({"email": EMAIL, "reason": "ask jane@example.com now"}, "reason"),
        ({"email": EMAIL, "reason": "first line\nsecond line"}, "reason"),
        ({"email": "a\x00b", "reason": WHY}, "email"),
        ({"email": EMAIL, "reason": WHY, "x": 1}, "non_field_errors"),
    ],
)
def test_invalid_lookups(admin, body, field):
    make_user(EMAIL)
    response = call(admin, LOOKUP, body)
    assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert list(response.json()["error"]["fields"]) == [field]
    assert not AuditRecord.objects.filter(action="privacy.viewed").exists()


def test_suspend_then_reactivate(admin, django_capture_on_commit_callbacks, caplog):
    caplog.set_level(logging.DEBUG)
    user = make_user(EMAIL)
    owner = signed_in_user(EMAIL)
    assert owner.get(ME).json()["suspension_reason"] is None

    with django_capture_on_commit_callbacks(execute=True):
        response = suspend(admin, user.id, f"  {REASON}  ")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert (body["state"], body["suspension_reason"]) == ("suspended", REASON)
    assert body["suspended_at"] and "email" not in body
    again = suspend(admin, user.id)
    assert (again.status_code, error_code(again)) == (409, "conflict")

    # the session stays; the account alone sees the reason, there and at the next sign-in
    assert Session.objects.filter(account_id=user.id, ended_at=None).count() == 1
    assert owner.get(ME).json()["suspension_reason"] == REASON
    fresh = browser()
    signed = post(fresh, SESSION, {"email": EMAIL, "password": USER_PASSWORD}, token_for(fresh))
    assert signed.json()["suspension_reason"] == REASON
    assert lookup(admin).json()["suspension_reason"] == REASON
    assert REASON not in owner.get("/api/v1/notifications").content.decode()

    with django_capture_on_commit_callbacks(execute=True):
        response = reactivate(admin, user.id)
    assert response.json() == {
        "id": str(user.id),
        "account_type": "individual",
        "state": "active",
        "suspension_reason": "",
        "suspended_at": None,
    }
    assert reactivate(admin, user.id).status_code == 409
    assert owner.get(ME).json()["suspension_reason"] is None

    notes = Notification.objects.filter(account_id=user.id).order_by("seq")
    assert [(n.kind, n.payload) for n in notes] == [
        ("account.suspended", {}),
        ("account.reactivated", {}),
    ]
    changes = AuditRecord.objects.filter(
        action__in=("account.suspended", "account.reactivated")
    ).order_by("created_at")
    assert [(r.action, r.target_id, r.metadata, r.reason) for r in changes] == [
        ("account.suspended", user.id, {}, ""),
        ("account.reactivated", user.id, {}, ""),
    ]
    assert "canary" not in caplog.text and "Appeal upheld" not in caplog.text


@pytest.mark.parametrize(
    "body", [{}, {"reason": "tiny"}, {"reason": 7}, {"reason": REASON, "state": "active"}]
)
def test_invalid_change_bodies(admin, body):
    user = make_user(EMAIL)
    for action in ("suspend", "reactivate"):
        response = call(admin, f"{BASE}/{user.id}/{action}", body)
        assert (response.status_code, error_code(response)) == (400, "validation_error")
    assert Account.objects.get(id=user.id).suspended_at is None


def test_targets_csrf_and_non_administrators(admin):
    user = make_user(EMAIL)
    own = Account.objects.get(account_type="admin")
    for target in (own.id, uuid.uuid4(), "nope"):
        assert suspend(admin, target).status_code == 404
        assert reactivate(admin, target).status_code == 404
    response = call(admin, f"{BASE}/{user.id}/suspend", {"reason": REASON}, csrf=False)
    assert (response.status_code, error_code(response)) == (403, "forbidden")
    for client, expected in ((browser(), 401), (signed_in_user(EMAIL), 404)):
        assert suspend(client, user.id).status_code == expected
        assert lookup(client).status_code == expected
    assert admin.get(LOOKUP).status_code == 405
    assert Account.objects.get(id=user.id).suspended_at is None


@pytest.mark.django_db(transaction=True)
def test_two_suspensions_at_once_have_one_winner():
    view = services.create_account(SYSTEM, EMAIL, USER_PASSWORD, "individual")
    barrier = threading.Barrier(2)
    results = []

    def run():
        try:
            barrier.wait(timeout=10)
            services.suspend_account(Actor.admin(uuid.uuid4()), view.id, REASON)
            results.append("ok")
        except DomainError as exc:
            results.append(exc.code)
        finally:
            connection.close()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(results) == ["conflict", "ok"]
    assert AuditRecord.objects.filter(action="account.suspended").count() == 1
    assert Notification.objects.filter(kind="account.suspended").count() == 1


def test_a_rolled_back_suspension_notifies_nobody(
    db, monkeypatch, django_capture_on_commit_callbacks
):
    view = services.create_account(SYSTEM, EMAIL, USER_PASSWORD, "individual")

    def fail(*args, **kwargs):
        raise RuntimeError

    monkeypatch.setattr(services.audit, "record", fail)
    with django_capture_on_commit_callbacks(execute=True), pytest.raises(RuntimeError):
        services.suspend_account(Actor.admin(uuid.uuid4()), view.id, REASON)
    assert not Notification.objects.exists()
    assert Account.objects.get(id=view.id).suspended_at is None
