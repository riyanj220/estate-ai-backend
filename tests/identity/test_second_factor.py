"""Story 10.10: the administrator's TOTP second factor."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.identity import services, totp
from apps.identity.models import Account, SecondFactor, Session
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord, RateLimitCounter, RateLimitPolicy
from tests.api.test_admin_api import (
    ADMIN_EMAIL,
    ADMIN_SESSION,
    PASSWORD,
    admin_sign_in,
    browser,
    error_code,
    make_admin,
    make_user,
    post,
    signed_in_admin,
    signed_in_user,
    stable,
    token_for,
)

BASE = "/api/v1/admin/second-factor"
T0 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
SECRET = totp.new_secret()


@pytest.fixture(autouse=True)
def policies(db):
    for scope, limit, window, kind in (
        ("admin_sign_in", 5, 900, "client"),
        ("admin_sign_in_account", 20, 3600, "account"),
        ("admin_second_factor", 5, 900, "account"),
    ):
        RateLimitPolicy.objects.get_or_create(
            scope=scope,
            defaults={"limit": limit, "window_seconds": window, "key_kind": kind, "note": "t"},
        )


@pytest.fixture
def clock(monkeypatch):
    state = {"now": T0}
    monkeypatch.setattr(timezone, "now", lambda: state["now"])

    def move(seconds):
        state["now"] += timedelta(seconds=seconds)

    move.now = lambda: state["now"]
    return move


def call(client, path, body):
    return post(client, path, body, token_for(client))


def current(clock, secret, offset=0):
    return totp.code_at(secret, totp.step_at(clock.now()) + offset)


def enrol(client, clock):
    secret = call(client, f"{BASE}/enrolment", {"password": PASSWORD}).json()["secret"]
    response = call(client, f"{BASE}/confirm", {"code": current(clock, secret)})
    assert response.status_code == 200, response.content
    clock(totp.STEP_SECONDS * 3)  # a fresh code from here on
    return secret


def sign_in(code=None, password=PASSWORD):
    client = browser()
    body = {"email": ADMIN_EMAIL, "password": password}
    if code is not None:
        body["code"] = code
    return post(client, ADMIN_SESSION, body, token_for(client))


def actions():
    """Sorted: the frozen clock gives every record the same time."""
    return sorted(
        AuditRecord.objects.filter(action__startswith="admin.second_factor").values_list(
            "action", flat=True
        )
    )


# RFC 6238 appendix B (SHA-1), truncated to six digits.
@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")],
)
def test_codes_match_the_published_vectors(seconds, expected):
    rfc_secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # gitleaks:allow (RFC 6238 test key)
    assert totp.code_at(rfc_secret, seconds // 30) == expected


def test_one_step_of_skew_and_no_replay():
    step = totp.step_at(T0)
    for offset in (-1, 0, 1):
        code = totp.code_at(SECRET, step + offset)
        assert totp.matching_step(SECRET, code, T0, None) == step + offset
        assert totp.matching_step(SECRET, code, T0, step + offset) is None
    for offset in (-2, 2):
        assert totp.matching_step(SECRET, totp.code_at(SECRET, step + offset), T0, None) is None
    for bad in (None, 123456, "", "12345", "1234567", "abcdef"):
        assert totp.matching_step(SECRET, bad, T0, None) is None


def test_the_secret_is_encrypted_and_unreadable_under_another_key(settings):
    stored = totp.encrypt(SECRET)
    assert SECRET not in stored and totp.decrypt(stored) == SECRET
    settings.SECRET_KEY = "another-test-only-key-" + "y" * 32
    assert totp.decrypt(stored) is None


def test_enrolment_needs_the_password_and_a_first_code(clock):
    make_admin()
    client = signed_in_admin()
    other_session = signed_in_admin()
    assert client.get(BASE).json() == {"enrolled": False}
    assert error_code(call(client, f"{BASE}/confirm", {"code": "000000"})) == "conflict"
    wrong = call(client, f"{BASE}/enrolment", {"password": "not the password 1"})
    assert error_code(wrong) == "validation_error"
    assert not SecondFactor.objects.exists()

    response = call(client, f"{BASE}/enrolment", {"password": PASSWORD})

    assert response["Cache-Control"] == "no-store"
    secret = response.json()["secret"]
    assert response.json()["otpauth_uri"].startswith("otpauth://totp/EstateAI:")
    assert f"secret={secret}" in response.json()["otpauth_uri"]
    assert secret not in SecondFactor.objects.get().secret_encrypted
    assert client.get(BASE).json() == {"enrolled": False}  # pending changes nothing yet
    assert sign_in().status_code == 200

    bad = call(client, f"{BASE}/confirm", {"code": current(clock, secret, 5)})
    assert error_code(bad) == "validation_error"
    assert call(client, f"{BASE}/confirm", {"code": current(clock, secret)}).status_code == 200
    assert client.get(BASE).json() == {"enrolled": True}  # the enrolling session stays
    assert error_code(other_session.get(BASE)) == "unauthenticated"
    assert error_code(call(client, f"{BASE}/enrolment", {"password": PASSWORD})) == "conflict"
    assert actions() == ["admin.second_factor_enrolled", "admin.second_factor_failed"]
    assert all(r.metadata == {} for r in AuditRecord.objects.all() if "factor" in r.action)


def test_wrong_enrolment_attempts_are_throttled(clock):
    make_admin()
    client = signed_in_admin()
    for _ in range(5):
        call(client, f"{BASE}/enrolment", {"password": "not the password 1"})
    response = call(client, f"{BASE}/enrolment", {"password": PASSWORD})
    assert error_code(response) == "throttled" and response["Retry-After"]
    assert not SecondFactor.objects.exists()


def test_once_enrolled_sign_in_needs_an_unused_code(clock):
    make_admin()
    secret = enrol(signed_in_admin(), clock)
    AuditRecord.objects.all().delete()
    open_before = Session.objects.filter(ended_at__isnull=True).count()

    stranger = sign_in(password="not the password 1")
    refused = [sign_in(), sign_in(""), sign_in(current(clock, secret, 4))]

    assert {r.status_code for r in refused} == {401}
    expected = stable(stranger)
    assert all(stable(r) == expected for r in refused)
    assert Session.objects.filter(ended_at__isnull=True).count() == open_before
    assert actions() == ["admin.second_factor_failed"] * 3
    code = current(clock, secret)
    assert sign_in(code).status_code == 200
    assert sign_in(code).status_code == 401  # replayed
    assert AuditRecord.objects.filter(action="admin.second_factor_used").count() == 1
    assert AuditRecord.objects.filter(action="admin.signed_in").count() == 1


def test_repeated_wrong_codes_lock_the_door(clock):
    make_admin()
    secret = enrol(signed_in_admin(), clock)
    RateLimitCounter.objects.all().delete()
    for _ in range(5):
        assert sign_in("000000" if current(clock, secret) != "000000" else "111111").status_code
    assert sign_in(current(clock, secret)).status_code == 401


def test_a_wrong_password_never_spends_or_checks_the_code(clock):
    make_admin()
    secret = enrol(signed_in_admin(), clock)
    code = current(clock, secret)
    before = SecondFactor.objects.get().last_used_step
    assert sign_in(code, password="not the password 1").status_code == 401
    assert SecondFactor.objects.get().last_used_step == before
    assert sign_in(code).status_code == 200


def test_an_unenrolled_administrator_signs_in_as_before_and_a_code_is_ignored(clock):
    make_admin()
    assert admin_sign_in().status_code == 200
    assert sign_in("123456").status_code == 200
    assert actions() == []


def test_the_ordinary_door_takes_no_code(db):
    make_user()
    client = browser()
    response = post(
        client,
        "/api/v1/session",
        {"email": "jane@example.com", "password": "x", "code": "123456"},
        token_for(client),
    )
    assert error_code(response) == "validation_error"


def test_only_administrators_reach_the_routes(db):
    make_user()
    user = signed_in_user()
    assert error_code(user.get(BASE)) == "not_found"
    assert error_code(browser().get(BASE)) == "unauthenticated"
    with pytest.raises(DomainError):
        services.start_second_factor(Actor.user(Account.objects.get().id), PASSWORD)


def test_only_the_command_removes_a_second_factor(clock, capsys):
    make_admin()
    client = signed_in_admin()
    enrol(client, clock)

    call_command("provision_admin", "reset-second-factor", email=ADMIN_EMAIL)
    call_command("provision_admin", "reset-second-factor", email=ADMIN_EMAIL)

    out = capsys.readouterr().out
    assert "Second factor removed" in out and "has no second factor" in out
    assert not SecondFactor.objects.exists()
    assert error_code(client.get(BASE)) == "unauthenticated"
    assert sign_in().status_code == 200
    assert AuditRecord.objects.filter(action="admin.second_factor_reset").count() == 1
    with pytest.raises(DomainError):
        services.reset_second_factor(Actor.admin(Account.objects.get().id), ADMIN_EMAIL)


def test_a_password_reset_keeps_the_second_factor(clock):
    make_admin()
    enrol(signed_in_admin(), clock)
    services.reset_admin_password(Actor.system("t"), ADMIN_EMAIL, "mango river lantern 77 night")
    assert SecondFactor.objects.filter(confirmed_at__isnull=False).exists()
    assert sign_in(password="mango river lantern 77 night").status_code == 401


def test_the_code_never_reaches_a_response(clock):
    make_admin()
    secret = enrol(signed_in_admin(), clock)
    response = sign_in(current(clock, secret))
    assert secret not in json.dumps(response.json())
