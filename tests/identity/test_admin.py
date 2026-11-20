"""Administrator provisioning, sign-in, lifetimes and the provisioning command (Story 1.22)."""

import io
import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth.hashers import check_password
from django.core import mail as django_mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.utils import timezone

from apps.identity import passwords, selectors, services, sessions, subscribers
from apps.identity import policy as identity_policy
from apps.identity.capabilities import Capability
from apps.identity.models import (
    Account,
    EmailChangeToken,
    IndividualProfile,
    PasswordResetToken,
    Session,
)
from apps.identity.services import sessions_auth
from apps.kernel import audit, events, runtime
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord, RateLimitCounter

SYSTEM = Actor.system("req-1")
VISITOR = Actor.visitor("req-2")
EMAIL = "Root.Admin@Example.com"
PASSWORD = "correct horse battery staple 42"
OTHER = "mango river lantern 77 night"
USER_PASSWORD = "correct horse battery staple"
NET = "203.0.113.7"
T0 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
ADMIN_ACTIONS = {
    "admin.provisioned",
    "admin.enabled",
    "admin.disabled",
    "admin.removed",
    "admin.password_reset",
    "admin.signed_in",
    "admin.sign_in_failed",
}


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    runtime.shutdown()
    subscribers.register()
    yield
    runtime.shutdown()


@pytest.fixture
def clock(monkeypatch):
    state = {"now": T0}
    monkeypatch.setattr(timezone, "now", lambda: state["now"])

    def move(**delta):
        state["now"] = state["now"] + timedelta(**delta)

    return move


def make_admin(email=EMAIL, password=PASSWORD):
    return services.provision_admin(SYSTEM, email, password)


def make_user(email="jane@example.com", kind="individual"):
    view = services.create_account(SYSTEM, email, USER_PASSWORD, kind)
    services.mark_email_verified(SYSTEM, view.id)
    return view


def code_of(call, *args, **kwargs):
    with pytest.raises(DomainError) as exc:
        call(*args, **kwargs)
    return exc.value.code


def refusal_of(call, *args, **kwargs):
    with pytest.raises(services.AdminRefused) as exc:
        call(*args, **kwargs)
    return exc.value.reason


def admin_in(email=EMAIL, password=PASSWORD, network=NET, current=None):
    return services.admin_sign_in(VISITOR, email, password, network, current)


def records(action=None):
    rows = AuditRecord.objects.all()
    return rows.filter(action=action) if action else rows.filter(action__in=ADMIN_ACTIONS)


# --- strong password policy ----------------


@pytest.mark.parametrize(
    ("password", "code"),
    [
        (None, "required"),
        ("", "required"),
        ("   ", "required"),
        (12345, "invalid"),
        ("short1", "admin_password_too_short"),
        ("elevenchars1", None),
        ("elevenchar1", "admin_password_too_short"),
        ("x1" * 600, "password_too_long"),
        ("onlyletterspassword", "admin_password_weak"),
        ("123456789012345", "admin_password_weak"),
        ("!!!!!!!!!!!!!!!!", "admin_password_weak"),
        (" correct horse 42 ", "admin_password_whitespace"),
        ("correct horse 42\n", "admin_password_whitespace"),
        (
            "\u0663\u0664\u0665\u0666\u0667\u0668\u0669\u0660\u0661\u0662\u0663\u0664x",
            "admin_password_weak",
        ),
        (
            "\u0627\u0628\u062a\u062b\u062c\u062d\u062e\u062f\u0630\u0631\u0632\u06331",
            "admin_password_weak",
        ),
        ("correct horse battery staple 42", None),
        ("password1234", "password_too_common"),
        ("root.admin@example.com", "password_matches_email"),
        ("Root.Admin", "admin_password_too_short"),
    ],
)
def test_admin_password_policy(password, code):
    assert passwords.admin_password_error(password, "root.admin@example.com") == code


def test_local_part_of_the_email_is_refused_when_long_enough():
    assert (
        passwords.admin_password_error("administrator.account.1", "administrator.account.1@x.org")
        == "password_matches_email"
    )


def test_policy_picks_the_rule_by_account_type():
    assert passwords.policy_error("individual", "eightchr") is None
    assert passwords.policy_error("admin", "eightchr") == "admin_password_too_short"


# --- provisioning ----------------


def test_provision_creates_a_verified_active_administrator_and_one_audit_record(db):
    view = make_admin()
    account = Account.objects.get(id=view.id)
    assert account.account_type == "admin" and account.state.value == "active"
    assert account.email == EMAIL and account.email_verified_at is not None
    assert check_password(PASSWORD, account.password) and account.password != PASSWORD
    assert not IndividualProfile.objects.exists()
    (record,) = AuditRecord.objects.all()
    assert (record.action, record.target_type, record.target_id) == (
        "admin.provisioned",
        "account",
        account.id,
    )
    assert (record.actor_kind, record.actor_id, record.metadata) == ("system", None, {})


def test_nothing_creates_an_administrator_by_migration_or_setting(db):
    assert not Account.objects.exists()


@pytest.mark.parametrize(
    "password", ["short1", "nodigitsinthispassword", "12345678901234", "password1234", None]
)
def test_weak_passwords_are_refused_without_trace(db, password):
    with pytest.raises(services.PasswordInvalid):
        make_admin(password=password)
    assert not Account.objects.exists() and not records().exists()


def test_password_equal_to_the_email_is_refused(db):
    with pytest.raises(services.PasswordInvalid) as exc:
        make_admin("root.admin.account@example.com", "root.admin.account@example.com")
    assert exc.value.fields == {"password": "password_matches_email"}


@pytest.mark.parametrize("kind", ["individual", "agency", "admin"])
def test_an_address_with_any_account_is_refused(db, kind):
    if kind == "admin":
        make_admin()
    else:
        services.create_account(SYSTEM, EMAIL, USER_PASSWORD, kind)
    before = (Account.objects.count(), AuditRecord.objects.count())
    assert refusal_of(make_admin, EMAIL.lower()) == "email_taken"
    assert (Account.objects.count(), AuditRecord.objects.count()) == before


@pytest.mark.parametrize("email", ["", "not-an-address", "a@b", None, 5, "x\x00@example.com"])
def test_invalid_emails_are_refused(db, email):
    assert refusal_of(make_admin, email) == "invalid_email"
    assert not Account.objects.exists()


@pytest.mark.parametrize("actor", [VISITOR, Actor.user(uuid.uuid4()), Actor.admin(uuid.uuid4())])
def test_provisioning_services_are_system_only(db, actor):
    make_admin()
    for call, args in (
        (services.provision_admin, ("a@example.com", PASSWORD)),
        (services.disable_admin, (EMAIL,)),
        (services.remove_admin, (EMAIL,)),
        (services.reset_admin_password, (EMAIL, OTHER)),
    ):
        with pytest.raises(DomainError):
            call(actor, *args)
    assert Account.objects.count() == 1 and Account.objects.get().deactivated_at is None


@pytest.mark.parametrize("actor", [VISITOR, Actor.user(uuid.uuid4()), Actor.admin(uuid.uuid4())])
def test_create_account_refuses_the_admin_type_for_non_system_actors(db, actor):
    with pytest.raises(DomainError):
        services.create_account(actor, "x@example.com", PASSWORD, "admin")
    assert not Account.objects.exists()


# --- disable ----------------


def test_disable_deactivates_ends_sessions_and_blocks_sign_in(db):
    make_admin()
    make_admin("second@example.com")
    admin_in()
    admin_in()
    assert services.disable_admin(SYSTEM, EMAIL.upper()) is True
    account = Account.objects.get(email=EMAIL)
    assert account.state.value == "deactivated"
    assert set(Session.objects.values_list("ended_reason", flat=True)) == {"deactivated"}
    assert Session.objects.filter(ended_at__isnull=True).count() == 0
    assert code_of(admin_in) == "invalid_credentials"
    assert [r.action for r in records("admin.disabled")] == ["admin.disabled"]
    assert records("admin.disabled").get().target_id == account.id


def test_disable_twice_is_idempotent_and_writes_one_record(db):
    make_admin()
    make_admin("second@example.com")
    assert services.disable_admin(SYSTEM, EMAIL) is True
    assert services.disable_admin(SYSTEM, EMAIL) is False
    assert records("admin.disabled").count() == 1


@pytest.mark.parametrize("kind", ["individual", "agency"])
def test_disable_refuses_non_administrators_and_unknown_addresses(db, kind):
    make_user("person@example.com", kind)
    before = AuditRecord.objects.count()
    assert (
        refusal_of(services.disable_admin, SYSTEM, "person@example.com") == "not_an_administrator"
    )
    assert refusal_of(services.disable_admin, SYSTEM, "ghost@example.com") == "not_an_administrator"
    assert Account.objects.get().deactivated_at is None
    assert AuditRecord.objects.count() == before


# --- remove ----------------


def test_remove_deletes_the_account_sessions_and_tokens_and_audits_the_former_id(db):
    make_admin()
    other = make_admin("second@example.com")
    admin_in()
    former = Account.objects.get(email=EMAIL).id
    services.remove_admin(SYSTEM, EMAIL)
    assert not Account.objects.filter(id=former).exists()
    assert not Session.objects.exists()
    record = records("admin.removed").get()
    assert record.target_id == former and record.metadata == {"forced": "no"}
    assert Account.objects.get().id == other.id


def test_removing_the_last_active_administrator_needs_force_last(db):
    make_admin()
    assert refusal_of(services.remove_admin, SYSTEM, EMAIL) == "last_administrator"
    assert Account.objects.count() == 1 and not records("admin.removed").exists()
    services.remove_admin(SYSTEM, EMAIL, force_last=True)
    assert not Account.objects.exists() and records("admin.removed").count() == 1


def test_a_disabled_administrator_does_not_count_as_active_and_can_always_be_removed(db):
    make_admin()
    make_admin("second@example.com")
    services.disable_admin(SYSTEM, "second@example.com")
    services.remove_admin(SYSTEM, "second@example.com")  # the disabled one goes
    assert refusal_of(services.remove_admin, SYSTEM, EMAIL) == "last_administrator"


def test_the_last_active_check_ignores_non_administrators(db):
    make_user("person@example.com")
    make_admin()
    assert refusal_of(services.remove_admin, SYSTEM, EMAIL) == "last_administrator"
    assert refusal_of(services.remove_admin, SYSTEM, "person@example.com") == "not_an_administrator"
    assert Account.objects.count() == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_removals_never_leave_zero_active_administrators():
    make_admin()
    make_admin("second@example.com")
    barrier = threading.Barrier(2)
    outcomes, errors = [], []

    def worker(email):
        try:
            barrier.wait()
            try:
                services.remove_admin(SYSTEM, email)
                outcomes.append("removed")
            except services.AdminRefused as exc:
                outcomes.append(exc.reason)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(e,)) for e in (EMAIL, "second@example.com")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(outcomes) == ["last_administrator", "removed"]
    assert Account.objects.count() == 1


# --- reset password ----------------


def test_reset_sets_the_password_ends_sessions_voids_tokens_and_audits(db):
    view = make_admin()
    admin_in()
    admin_in()
    account = Account.objects.get(id=view.id)
    PasswordResetToken.objects.create(
        account=account,
        token_hash="a" * 64,
        created_at=timezone.now(),
        expires_at=timezone.now() + timedelta(hours=1),
    )
    EmailChangeToken.objects.create(
        account=account,
        new_email="new@example.com",
        token_hash="b" * 64,
        created_at=timezone.now(),
        expires_at=timezone.now() + timedelta(hours=1),
    )
    services.reset_admin_password(SYSTEM, EMAIL, OTHER)
    assert code_of(admin_in) == "invalid_credentials"  # the old password
    assert admin_in(password=OTHER).summary.account_type == "admin"
    ended = Session.objects.filter(ended_at__isnull=False)
    assert ended.count() == 2 and set(ended.values_list("ended_reason", flat=True)) == {
        "password_changed"
    }
    assert not PasswordResetToken.objects.filter(used_at__isnull=True).exists()
    assert not EmailChangeToken.objects.filter(used_at__isnull=True).exists()
    record = records("admin.password_reset").get()
    assert (record.target_id, record.metadata) == (view.id, {})


@pytest.mark.parametrize("password", ["short1", "nodigitsinthispassword", "password1234"])
def test_reset_enforces_the_strong_policy_and_changes_nothing(db, password):
    make_admin()
    token = admin_in().token
    with pytest.raises(services.PasswordInvalid):
        services.reset_admin_password(SYSTEM, EMAIL, password)
    assert selectors.live_session(token, timezone.now()) is not None
    assert not records("admin.password_reset").exists()
    assert admin_in().summary.account_type == "admin"


def test_reset_refuses_non_administrators_before_judging_the_password(db):
    make_user("person@example.com")
    before = Account.objects.get().password
    assert refusal_of(services.reset_admin_password, SYSTEM, "person@example.com", "x") == (
        "not_an_administrator"
    )
    assert refusal_of(services.reset_admin_password, SYSTEM, "ghost@example.com", OTHER) == (
        "not_an_administrator"
    )
    assert Account.objects.get().password == before and not records().exists()


def test_public_password_reset_treats_administrators_as_unknown(
    db, django_capture_on_commit_callbacks
):
    make_admin()
    make_user()
    django_mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        assert services.request_password_reset(VISITOR, EMAIL) is None
        assert services.request_password_reset(VISITOR, "ghost@example.com") is None
    assert django_mail.outbox == [] and not PasswordResetToken.objects.exists()
    assert services.issue_password_reset_token(SYSTEM, Account.objects.get(email=EMAIL).id) is None
    # even a token that somehow exists cannot be confirmed
    account = Account.objects.get(email=EMAIL)
    from apps.identity import verification

    token = verification.new_token()
    PasswordResetToken.objects.create(
        account=account,
        token_hash=verification.hash_token(token),
        created_at=timezone.now(),
        expires_at=timezone.now() + timedelta(hours=1),
    )
    assert code_of(services.confirm_password_reset, VISITOR, token, OTHER) == "link_invalid"
    assert check_password(PASSWORD, Account.objects.get(email=EMAIL).password)


# --- admin sign-in ----------------


def test_admin_sign_in_starts_a_session_and_audits_it(db):
    view = make_admin()
    result = admin_in("  ROOT.admin@example.com ")
    assert result.summary.id == view.id and result.summary.account_type == "admin"
    assert result.summary.capabilities == ("sign_in", "browse")
    row = Session.objects.get()
    assert row.token_hash == sessions.hash_token(result.token)
    assert row.expires_at - row.created_at == timedelta(hours=8)
    record = records("admin.signed_in").get()
    assert (record.target_id, record.actor_kind, record.actor_id, record.metadata) == (
        view.id,
        "admin",
        view.id,
        {},
    )


@pytest.mark.parametrize("kind", ["individual", "agency"])
def test_non_administrators_cannot_use_the_admin_sign_in(db, kind):
    make_user("person@example.com", kind)
    before = AuditRecord.objects.count()
    assert code_of(admin_in, "person@example.com", USER_PASSWORD) == "invalid_credentials"
    assert not Session.objects.exists() and AuditRecord.objects.count() == before


def test_administrators_cannot_use_the_ordinary_sign_in(db):
    make_admin()
    before = AuditRecord.objects.count()
    assert code_of(services.sign_in, VISITOR, EMAIL, PASSWORD, NET) == "invalid_credentials"
    assert not Session.objects.exists() and AuditRecord.objects.count() == before


def test_failures_are_audited_only_for_an_existing_active_administrator(db):
    view = make_admin()
    make_user("person@example.com")
    make_admin("gone@example.com")
    services.disable_admin(SYSTEM, "gone@example.com")
    base = records("admin.provisioned").count() + records("admin.disabled").count()
    for email, password in (
        ("ghost@example.com", PASSWORD),  # unknown
        ("person@example.com", USER_PASSWORD),  # a non-administrator, right password
        ("gone@example.com", PASSWORD),  # disabled administrator
    ):
        assert code_of(admin_in, email, password) == "invalid_credentials"
    assert records().count() == base  # nothing new
    assert code_of(admin_in, EMAIL, "wrong password 1") == "invalid_credentials"
    failed = records("admin.sign_in_failed").get()
    assert (failed.target_id, failed.metadata, failed.actor_kind) == (view.id, {}, "visitor")
    assert not records("admin.signed_in").exists()


def test_every_admin_sign_in_path_runs_exactly_one_hash_comparison(db, monkeypatch):
    make_admin()
    make_user("person@example.com")
    make_admin("gone@example.com")
    services.disable_admin(SYSTEM, "gone@example.com")
    calls = []
    real = sessions_auth.check_password
    monkeypatch.setattr(sessions_auth, "check_password", lambda *a: calls.append(a) or real(*a))
    for email, password in (
        (EMAIL, "wrong password 1"),
        ("person@example.com", USER_PASSWORD),
        ("ghost@example.com", PASSWORD),
        ("gone@example.com", PASSWORD),
    ):
        calls.clear()
        assert code_of(admin_in, email, password) == "invalid_credentials"
        assert len(calls) == 1
    calls.clear()
    admin_in()
    assert len(calls) == 1
    calls.clear()
    assert code_of(services.sign_in, VISITOR, EMAIL, PASSWORD, NET) == "invalid_credentials"
    assert len(calls) == 1


def test_oversized_password_is_a_failure_without_hashing(db, monkeypatch):
    make_admin()
    calls = []
    monkeypatch.setattr(sessions_auth, "check_password", lambda *a: calls.append(a) or True)
    assert code_of(admin_in, EMAIL, "x" * 2000) == "invalid_credentials"
    assert calls == []


def test_throttle_five_failures_then_silent_lockout_then_clears(db, clock, monkeypatch):
    make_admin()
    for _ in range(5):
        assert code_of(admin_in, EMAIL, "wrong password 1") == "invalid_credentials"
    calls = []
    real = sessions_auth.check_password
    monkeypatch.setattr(sessions_auth, "check_password", lambda *a: calls.append(a) or real(*a))
    assert code_of(admin_in) == "invalid_credentials"  # right password, locked out
    assert len(calls) == 1 and not Session.objects.exists()  # one dummy-hash comparison, no more
    assert RateLimitCounter.objects.get(scope="admin_sign_in").count == 5
    assert records("admin.sign_in_failed").count() == 5  # a throttled attempt adds nothing
    clock(minutes=16)
    assert admin_in().summary.account_type == "admin"


def test_throttle_is_per_email_and_network_and_separate_from_the_ordinary_one(db):
    make_admin()
    make_admin("second@example.com")
    for _ in range(5):
        code_of(admin_in, EMAIL, "wrong password 1")
    assert admin_in("second@example.com").summary.account_type == "admin"
    assert admin_in(network="198.51.100.9").summary.account_type == "admin"
    for _ in range(5):
        code_of(services.sign_in, VISITOR, "second@example.com", "wrong", NET)
    assert admin_in("second@example.com", network=NET).summary.account_type == "admin"
    assert code_of(admin_in) == "invalid_credentials"


def test_throttle_key_ignores_email_case_and_padding(db):
    make_admin()
    for email in (EMAIL, EMAIL.upper(), f"  {EMAIL.lower()}  ", EMAIL, EMAIL):
        code_of(admin_in, email, "wrong password 1")
    assert RateLimitCounter.objects.get(scope="admin_sign_in").count == 5


@pytest.mark.django_db(transaction=True)
def test_parallel_admin_failures_never_push_the_counter_past_the_cap():
    from apps.kernel.models import RateLimitPolicy

    RateLimitPolicy.objects.update_or_create(
        scope="admin_sign_in",
        defaults={"limit": 5, "window_seconds": 900, "key_kind": "client", "note": "t"},
    )
    RateLimitPolicy.objects.update_or_create(
        scope="admin_sign_in_account",
        defaults={"limit": 20, "window_seconds": 3600, "key_kind": "account", "note": "t"},
    )
    make_admin()
    errors, outcomes = [], []
    barrier = threading.Barrier(15)

    def worker():
        try:
            barrier.wait()
            try:
                admin_in(password="wrong password 1")
            except DomainError as exc:
                outcomes.append(exc.code)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and outcomes == ["invalid_credentials"] * 15
    assert RateLimitCounter.objects.get(scope="admin_sign_in").count == 5
    assert records("admin.sign_in_failed").count() == 5
    assert code_of(admin_in) == "invalid_credentials"
    assert not Session.objects.exists()


def test_a_held_session_is_replaced_and_the_open_session_cap_applies(db, settings):
    make_admin()
    first = admin_in()
    admin_in(current=first.token)
    assert Session.objects.get(ended_reason="replaced")
    settings.SESSION_MAX_OPEN = 2
    admin_in()
    admin_in()
    assert Session.objects.filter(ended_at__isnull=True).count() == 2


@pytest.mark.parametrize(("email", "password"), [(5, "x"), ("a@b.example", None)])
def test_non_string_fields_are_validation_errors(db, email, password):
    assert code_of(admin_in, email, password) == "validation_error"


# --- lifetimes ----------------


def live(token):
    return selectors.live_session(token, timezone.now())


def test_admin_session_idles_out_after_30_minutes_but_an_ordinary_one_does_not(db, clock):
    make_admin()
    make_user()
    admin_token = admin_in().token
    user_token = services.sign_in(VISITOR, "jane@example.com", USER_PASSWORD, NET).token
    clock(minutes=29, seconds=59)
    assert live(admin_token) is not None
    clock(seconds=1)
    assert live(admin_token) is None
    clock(minutes=1)  # 31 minutes
    assert live(user_token) is not None


def test_admin_session_dies_at_8_hours_whatever_the_activity(db, clock):
    make_admin()
    token = admin_in().token
    for _ in range(16):  # active every 29 minutes: 7 h 44 m, the idle limit never trips
        clock(minutes=29)
        row = live(token)
        assert row is not None
        services.refresh_session(SYSTEM, row.id)
    clock(minutes=15, seconds=59)
    assert live(token) is not None
    clock(seconds=1)
    assert live(token) is None  # exactly 8 hours after sign-in
    clock(minutes=1)
    assert live(token) is None


def test_the_idle_limit_follows_the_account_type_not_the_stored_row(db, clock):
    make_admin()
    token = admin_in().token
    Session.objects.update(expires_at=T0 + timedelta(days=14))  # stored under longer limits
    clock(minutes=31)
    assert live(token) is None
    assert selectors.live_session(token, T0) is not None


def test_sliding_refresh_keeps_the_five_minute_interval(db, clock):
    make_admin()
    token = admin_in().token
    seen = live(token).last_seen_at
    clock(minutes=4)
    services.refresh_session(SYSTEM, live(token).id)
    assert live(token).last_seen_at == seen
    clock(minutes=2)
    services.refresh_session(SYSTEM, live(token).id)
    assert live(token).last_seen_at == seen + timedelta(minutes=6)


def test_admin_session_settings_are_validated_at_load(monkeypatch):
    import importlib

    from django.core.exceptions import ImproperlyConfigured

    import config.settings.base as base

    for name, value in (
        ("ADMIN_SESSION_IDLE_SECONDS", "300"),  # not above the touch interval
        ("ADMIN_SESSION_IDLE_SECONDS", "0"),
        ("ADMIN_SESSION_ABSOLUTE_SECONDS", "600"),  # below the 30 minute idle limit
        ("ADMIN_SESSION_IDLE_SECONDS", "90000"),  # above the ordinary 24 h idle limit
        ("ADMIN_SESSION_ABSOLUTE_SECONDS", str(15 * 24 * 3600)),  # above the ordinary 14 days
    ):
        monkeypatch.setenv(name, value)
        with pytest.raises(ImproperlyConfigured):
            importlib.reload(base)
        monkeypatch.delenv(name)
    importlib.reload(base)
    assert (base.ADMIN_SESSION_IDLE_SECONDS, base.ADMIN_SESSION_ABSOLUTE_SECONDS) == (1800, 28800)


# --- sign-out, password change, email change ----------------


def test_admin_signs_out_and_changes_their_password(db):
    view = make_admin()
    actor = Actor.admin(view.id, "r")
    first, second = admin_in(), admin_in()
    keep = Session.objects.get(token_hash=sessions.hash_token(first.token)).id
    services.change_password(actor, keep, PASSWORD, OTHER)
    assert check_password(OTHER, Account.objects.get().password)
    assert selectors.live_session(second.token, timezone.now()) is None  # other sessions end
    assert selectors.live_session(first.token, timezone.now()) is not None
    services.sign_out(actor, keep)
    assert selectors.live_session(first.token, timezone.now()) is None


@pytest.mark.parametrize(
    ("new", "code"),
    [
        ("short1", "admin_password_too_short"),
        ("nodigitsinthispassword", "admin_password_weak"),
        ("password1234", "password_too_common"),
        ("x1" * 600, "password_too_long"),
    ],
)
def test_admin_password_change_uses_the_strong_policy(db, new, code):
    view = make_admin()
    keep = Session.objects.create(
        account_id=view.id,
        token_hash="c" * 64,
        created_at=timezone.now(),
        last_seen_at=timezone.now(),
        expires_at=timezone.now() + timedelta(hours=1),
    ).id
    with pytest.raises(services.PasswordInvalid) as exc:
        services.change_password(Actor.admin(view.id), keep, PASSWORD, new)
    assert exc.value.fields == {"new_password": code}
    with pytest.raises(services.PasswordInvalid) as exc:
        services.change_password(Actor.admin(view.id), keep, PASSWORD, PASSWORD)
    assert exc.value.fields == {"new_password": "password_same_as_current"}


def test_an_ordinary_account_keeps_the_ordinary_policy_on_change(db):
    view = make_user()
    keep = Session.objects.create(
        account_id=view.id,
        token_hash="d" * 64,
        created_at=timezone.now(),
        last_seen_at=timezone.now(),
        expires_at=timezone.now() + timedelta(hours=1),
    ).id
    services.change_password(Actor.user(view.id), keep, USER_PASSWORD, "eightchr")


def test_admin_email_change_is_forbidden(db):
    view = make_admin()
    assert code_of(
        services.request_email_change, Actor.admin(view.id), "new@example.com", PASSWORD
    ) == ("forbidden")


# --- listings ----------------


@pytest.mark.parametrize("state", ["active", "unverified", "suspended", "deactivated"])
def test_an_administrator_is_never_an_eligible_owner_and_has_no_listing_capability(db, state):
    view = make_admin()
    account = Account.objects.get(id=view.id)
    if state == "unverified":
        account.email_verified_at = None
    elif state == "suspended":
        account.suspended_at, account.suspension_reason = timezone.now(), "reason"
    elif state == "deactivated":
        account.deactivated_at = timezone.now()
    account.save()
    assert selectors.is_active_owner(account.id) is False
    for actor in (Actor.admin(account.id), Actor.user(account.id)):
        for capability in (Capability.PUBLISH, Capability.EDIT_DRAFTS, Capability.VIEW_DRAFTS):
            assert identity_policy.can(actor, capability) is False
    granted = identity_policy.capabilities_for("admin", account.state)
    assert not {Capability.PUBLISH, Capability.EDIT_DRAFTS} & set(granted)


# --- audit registration ----------------


def test_the_admin_actions_are_registered_with_empty_schemas():
    registered = audit.registered_actions()
    for action in ADMIN_ACTIONS - {"admin.disabled", "admin.removed"}:
        assert registered[action] == {}
    for action in ("admin.disabled", "admin.removed"):  # enumerated flag, no free text
        assert registered[action] == {"forced": frozenset({"yes", "no"})}


def test_failed_provisioning_actions_write_no_record_at_all(db):
    make_admin()
    base = AuditRecord.objects.count()
    for call, args in (
        (services.provision_admin, (SYSTEM, "ghost@example.com", "weak")),
        (services.provision_admin, (SYSTEM, EMAIL, PASSWORD)),
        (services.disable_admin, (SYSTEM, "ghost@example.com")),
        (services.remove_admin, (SYSTEM, "ghost@example.com")),
        (services.reset_admin_password, (SYSTEM, "ghost@example.com", OTHER)),
        (services.remove_admin, (SYSTEM, EMAIL)),  # the last active one
    ):
        with pytest.raises(DomainError):
            call(*args)
    assert AuditRecord.objects.count() == base


# --- the command ----------------


def run(*args, stdin=None, monkeypatch=None):
    out, err = io.StringIO(), io.StringIO()
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    call_command("provision_admin", *args, stdout=out, stderr=err)
    return out.getvalue() + err.getvalue()


def test_command_creates_with_a_password_from_stdin(db, monkeypatch):
    output = run(
        "create",
        "--email",
        EMAIL,
        "--password-stdin",
        stdin=PASSWORD + "\n",
        monkeypatch=monkeypatch,
    )
    assert output.strip() == "Administrator created."
    account = Account.objects.get()
    assert account.account_type == "admin" and check_password(PASSWORD, account.password)
    assert records("admin.provisioned").count() == 1
    assert EMAIL not in output and PASSWORD not in output


def test_command_prompts_twice_without_echo(db, monkeypatch):
    asked = []
    answers = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr(
        "apps.identity.management.commands.provision_admin.getpass.getpass",
        lambda prompt="": asked.append(prompt) or next(answers),
    )
    run("create", "--email", EMAIL)
    assert len(asked) == 2 and Account.objects.get().account_type == "admin"


def test_command_refuses_mismatched_prompts(db, monkeypatch):
    answers = iter([PASSWORD, OTHER])
    monkeypatch.setattr(
        "apps.identity.management.commands.provision_admin.getpass.getpass",
        lambda prompt="": next(answers),
    )
    with pytest.raises(CommandError, match="do not match"):
        run("create", "--email", EMAIL)
    assert not Account.objects.exists() and not records().exists()


@pytest.mark.parametrize(
    "flag", [["--password", OTHER], ["--password=" + OTHER], ["--passw", OTHER]]
)
def test_command_has_no_password_option_and_never_echoes_one(db, flag):
    with pytest.raises(CommandError) as exc:
        call_command("provision_admin", "create", "--email", EMAIL, *flag)
    assert OTHER not in str(exc.value)
    assert not Account.objects.exists()


def test_the_password_option_is_absent_from_the_parser():
    from apps.identity.management.commands.provision_admin import Command

    parser = Command().create_parser("manage.py", "provision_admin")
    options = {o for action in parser._actions for o in action.option_strings}
    assert not any(o.startswith("--password") and o != "--password-stdin" for o in options)


@pytest.mark.parametrize(
    "stdin", ["short1\n", "nodigitsinthispassword\n", "password1234\n", "\n", ""]
)
def test_command_refuses_weak_passwords_with_one_line_and_no_account(db, monkeypatch, stdin):
    with pytest.raises(CommandError) as exc:
        run("create", "--email", EMAIL, "--password-stdin", stdin=stdin, monkeypatch=monkeypatch)
    assert "\n" not in str(exc.value) and (not stdin.strip() or stdin.strip() not in str(exc.value))
    assert not Account.objects.exists() and not records().exists()


def test_command_refuses_an_existing_address(db, monkeypatch):
    make_user(EMAIL)
    with pytest.raises(CommandError, match="already exists"):
        run("create", "--email", EMAIL, "--password-stdin", stdin=PASSWORD, monkeypatch=monkeypatch)
    assert (
        Account.objects.count() == 1
        and not AuditRecord.objects.filter(action__startswith="admin").exists()
    )


def test_command_disable_remove_and_reset(db, monkeypatch):
    run("create", "--email", EMAIL, "--password-stdin", stdin=PASSWORD, monkeypatch=monkeypatch)
    run(
        "create",
        "--email",
        "two@example.com",
        "--password-stdin",
        stdin=PASSWORD,
        monkeypatch=monkeypatch,
    )
    admin_in()
    run(
        "reset-password",
        "--email",
        EMAIL,
        "--password-stdin",
        stdin=OTHER + "\n",
        monkeypatch=monkeypatch,
    )
    assert not Session.objects.filter(ended_at__isnull=True).exists()
    assert admin_in(password=OTHER).summary.account_type == "admin"
    assert "disabled" in run("disable", "--email", EMAIL)
    assert "already disabled" in run("disable", "--email", EMAIL)
    run("remove", "--email", EMAIL)
    assert Account.objects.count() == 1
    with pytest.raises(CommandError, match="last active administrator"):
        run("remove", "--email", "two@example.com")
    run("remove", "--email", "two@example.com", "--force-last")
    assert not Account.objects.exists()
    assert {r.action for r in records()} >= {
        "admin.provisioned",
        "admin.password_reset",
        "admin.disabled",
        "admin.removed",
    }


@pytest.mark.parametrize("action", ["disable", "remove", "reset-password"])
def test_command_refuses_non_administrators_and_unknown_addresses(db, monkeypatch, action):
    make_user("person@example.com")
    extra = ["--password-stdin"] if action == "reset-password" else []
    for email in ("person@example.com", "ghost@example.com"):
        with pytest.raises(CommandError, match="not belong to an administrator"):
            run(action, "--email", email, *extra, stdin=OTHER, monkeypatch=monkeypatch)
    assert Account.objects.count() == 1 and not records().exists()


def test_command_rejects_misplaced_flags_and_unknown_actions(db):
    for args in (
        ["disable", "--email", EMAIL, "--password-stdin"],
        ["create", "--email", EMAIL, "--force-last"],
        ["explode", "--email", EMAIL],
        ["create"],
    ):
        with pytest.raises(CommandError):
            call_command("provision_admin", *args)


def test_command_output_logs_audit_and_errors_never_carry_secrets(db, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    seen = []
    secret_email = "canary.operator@example.com"
    seen.append(
        run(
            "create",
            "--email",
            secret_email,
            "--password-stdin",
            stdin=PASSWORD,
            monkeypatch=monkeypatch,
        )
    )
    seen.append(
        run(
            "reset-password",
            "--email",
            secret_email,
            "--password-stdin",
            stdin=OTHER,
            monkeypatch=monkeypatch,
        )
    )
    for args in (
        ["create", "--email", secret_email, "--password-stdin"],
        ["disable", "--email", "ghost@example.com"],
    ):
        try:
            run(*args, stdin=PASSWORD, monkeypatch=monkeypatch, **{})
        except CommandError as exc:
            seen.append(str(exc))
    admin_in(secret_email, OTHER)
    code_of(admin_in, secret_email, "canary wrong pass 9")
    account = Account.objects.get()
    haystack = " ".join(
        seen
        + [str(r.__dict__) + r.getMessage() for r in caplog.records]
        + [
            f"{r.action}{r.metadata}{r.reason}{r.request_id}{r.target_type}"
            for r in AuditRecord.objects.all()
        ]
    )
    for canary in ("canary.operator", "canary wrong pass", PASSWORD, OTHER, account.password):
        assert canary not in haystack
    assert records().count() >= 4


# --- budget: only failures count ----------------


def test_successful_sign_ins_never_consume_the_admin_budget(db):
    make_admin()
    for _ in range(7):
        admin_in()
    assert not RateLimitCounter.objects.filter(
        scope__in=["admin_sign_in", "admin_sign_in_account"]
    ).exists()


def test_four_failures_a_success_and_a_failure_leave_five(db):
    make_admin()
    for _ in range(4):
        code_of(admin_in, EMAIL, "wrong password 1")
    admin_in()
    code_of(admin_in, EMAIL, "wrong password 1")
    assert RateLimitCounter.objects.get(scope="admin_sign_in").count == 5
    assert RateLimitCounter.objects.get(scope="admin_sign_in_account").count == 5


# --- per-account ceiling, audit ordering ----------------


def test_the_per_account_ceiling_holds_across_many_networks(db, monkeypatch):
    make_admin()
    for i in range(20):  # one failure each from 20 networks: the per-network cap never trips
        assert code_of(admin_in, EMAIL, "wrong password 1", f"198.51.100.{i}") == (
            "invalid_credentials"
        )
    assert RateLimitCounter.objects.get(scope="admin_sign_in_account").count == 20
    assert records("admin.sign_in_failed").count() == 20
    calls = []
    real = sessions_auth.check_password
    monkeypatch.setattr(sessions_auth, "check_password", lambda *a: calls.append(a) or real(*a))
    assert code_of(admin_in, EMAIL, PASSWORD, "203.0.113.200") == "invalid_credentials"
    assert code_of(admin_in, EMAIL, "wrong password 1", "203.0.113.201") == "invalid_credentials"
    assert len(calls) == 2  # throttled: one dummy-hash comparison each, never the real hash
    assert all(c[1] == passwords.dummy_hash() for c in calls)
    assert RateLimitCounter.objects.get(scope="admin_sign_in_account").count == 20
    assert records("admin.sign_in_failed").count() == 20  # no audit flood from throttled calls
    assert not Session.objects.exists()


def test_the_per_account_ceiling_is_per_email(db):
    make_admin()
    make_admin("second@example.com")
    for i in range(20):
        code_of(admin_in, EMAIL, "wrong password 1", f"198.51.100.{i}")
    assert admin_in("second@example.com").summary.account_type == "admin"


def test_throttled_branch_runs_one_dummy_hash_on_the_ordinary_door_too(db, monkeypatch):
    make_user()
    for _ in range(5):
        code_of(services.sign_in, VISITOR, "jane@example.com", "wrong", NET)
    calls = []
    real = sessions_auth.check_password
    monkeypatch.setattr(sessions_auth, "check_password", lambda *a: calls.append(a) or real(*a))
    code_of(services.sign_in, VISITOR, "jane@example.com", USER_PASSWORD, NET)
    assert len(calls) == 1 and calls[0][1] == passwords.dummy_hash()


def test_an_audit_failure_neither_rolls_back_the_count_nor_breaks_the_401(db, monkeypatch, caplog):
    make_admin()
    real = audit.record

    def broken(actor, action, *args, **kwargs):
        if action == "admin.sign_in_failed":
            raise RuntimeError("audit store down: " + EMAIL)
        return real(actor, action, *args, **kwargs)

    monkeypatch.setattr(services.audit, "record", broken)
    caplog.set_level(logging.WARNING)
    assert code_of(admin_in, EMAIL, "wrong password 1") == "invalid_credentials"
    assert RateLimitCounter.objects.get(scope="admin_sign_in").count == 1
    assert RateLimitCounter.objects.get(scope="admin_sign_in_account").count == 1
    assert not records("admin.sign_in_failed").exists()
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    assert "RuntimeError" in logged and EMAIL not in logged and "audit store down" not in logged
    assert admin_in().summary.account_type == "admin"  # the sign-in path is unharmed


@pytest.mark.parametrize(
    ("door", "kind", "succeeds"),
    [
        ("admin", "admin", True),
        ("admin", "individual", False),
        ("ordinary", "admin", False),
        ("ordinary", "individual", True),
    ],
)
def test_each_door_accepts_only_its_own_account_type(db, monkeypatch, door, kind, succeeds):
    email = "who@example.com"
    if kind == "admin":
        services.provision_admin(SYSTEM, email, PASSWORD)
        password = PASSWORD
    else:
        make_user(email)
        password = USER_PASSWORD
    calls = []
    real = sessions_auth.check_password
    monkeypatch.setattr(sessions_auth, "check_password", lambda *a: calls.append(a) or real(*a))
    call = admin_in if door == "admin" else sign_in_ordinary
    if succeeds:
        assert call(email, password).summary.account_type == kind
        assert Session.objects.count() == 1
    else:
        assert code_of(call, email, password) == "invalid_credentials"
        assert not Session.objects.exists()
    assert len(calls) == 1


def sign_in_ordinary(email, password):
    return services.sign_in(VISITOR, email, password, NET)


# --- tokens and cascades ----------------


def _all_tokens(account):
    from apps.identity.models import EmailVerificationToken

    now = timezone.now()
    common = {"account": account, "created_at": now, "expires_at": now + timedelta(hours=1)}
    PasswordResetToken.objects.create(token_hash="1" * 64, **common)
    EmailVerificationToken.objects.create(token_hash="2" * 64, **common)
    EmailChangeToken.objects.create(token_hash="3" * 64, new_email="n@example.com", **common)


def test_remove_cascades_sessions_and_every_token_table(db):
    from apps.identity.models import EmailVerificationToken

    make_admin()
    make_admin("second@example.com")
    admin_in()
    _all_tokens(Account.objects.get(email=EMAIL))
    services.remove_admin(SYSTEM, EMAIL)
    assert not Session.objects.exists()
    assert not PasswordResetToken.objects.exists()
    assert not EmailVerificationToken.objects.exists()
    assert not EmailChangeToken.objects.exists()


def test_reset_voids_verification_tokens_too(db):
    from apps.identity.models import EmailVerificationToken

    make_admin()
    _all_tokens(Account.objects.get())
    services.reset_admin_password(SYSTEM, EMAIL, OTHER)
    assert not EmailVerificationToken.objects.filter(used_at__isnull=True).exists()
    assert not PasswordResetToken.objects.filter(used_at__isnull=True).exists()
    assert not EmailChangeToken.objects.filter(used_at__isnull=True).exists()


def test_an_administrator_cannot_change_their_address_by_link_or_verify_by_link(db):
    view = make_admin()
    account = Account.objects.get(id=view.id)
    verified_at = account.email_verified_at
    from apps.identity.models import EmailVerificationToken

    now = timezone.now()
    token = verification_token = verification_new()
    EmailChangeToken.objects.create(
        account=account,
        new_email="hijack@example.com",
        token_hash=verification_hash(token),
        link_issued=True,
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    assert code_of(services.confirm_email_change, VISITOR, token) == "link_invalid"
    other = verification_new()
    EmailVerificationToken.objects.create(
        account=account,
        token_hash=verification_hash(other),
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    services.confirm_email(VISITOR, other)  # consumes the token but changes nothing
    services.request_verification_link(VISITOR, EMAIL)
    account.refresh_from_db()
    assert account.email == EMAIL and account.email_verified_at == verified_at
    assert verification_token and account.state.value == "active"


def verification_new():
    from apps.identity import verification

    return verification.new_token()


def verification_hash(token):
    from apps.identity import verification

    return verification.hash_token(token)


# --- disable / enable / force-last ----------------


def test_disabling_the_last_active_administrator_needs_force_last(db):
    make_admin()
    assert refusal_of(services.disable_admin, SYSTEM, EMAIL) == "last_administrator"
    assert Account.objects.get().deactivated_at is None and not records("admin.disabled").exists()
    assert services.disable_admin(SYSTEM, EMAIL, force_last=True) is True
    assert records("admin.disabled").get().metadata == {"forced": "yes"}


def test_forced_is_no_when_the_flag_was_not_needed(db):
    make_admin()
    make_admin("second@example.com")
    services.disable_admin(SYSTEM, EMAIL, force_last=True)  # flag given, not needed
    services.remove_admin(SYSTEM, "second@example.com", force_last=True)  # the last one: needed
    forced = [r.metadata for r in records("admin.disabled")] + [
        r.metadata for r in records("admin.removed")
    ]
    assert forced == [{"forced": "no"}, {"forced": "yes"}]


def test_enable_clears_the_deactivated_state_and_keeps_sessions_ended(db):
    view = make_admin()
    make_admin("second@example.com")
    admin_in()
    services.disable_admin(SYSTEM, EMAIL)
    assert code_of(admin_in) == "invalid_credentials"
    assert services.enable_admin(SYSTEM, EMAIL) is True
    assert services.enable_admin(SYSTEM, EMAIL) is False  # idempotent, no second record
    assert Account.objects.get(id=view.id).state.value == "active"
    assert not Session.objects.filter(ended_at__isnull=True).exists()
    assert admin_in().summary.account_type == "admin"
    record = records("admin.enabled").get()
    assert (record.target_id, record.metadata) == (view.id, {})


def test_enable_refuses_non_administrators_and_unknown_addresses(db):
    make_user("person@example.com")
    before = AuditRecord.objects.count()
    for email in ("person@example.com", "ghost@example.com"):
        assert refusal_of(services.enable_admin, SYSTEM, email) == "not_an_administrator"
    assert AuditRecord.objects.count() == before
    with pytest.raises(DomainError):
        services.enable_admin(Actor.admin(uuid.uuid4()), "x@example.com")


def _race(first, second):
    barrier = threading.Barrier(2)
    outcomes, errors = [], []

    def worker(call):
        try:
            barrier.wait()
            try:
                call()
                outcomes.append("ok")
            except services.AdminRefused as exc:
                outcomes.append(exc.reason)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(c,)) for c in (first, second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    return sorted(outcomes)


@pytest.mark.django_db(transaction=True)
def test_concurrent_disable_and_remove_never_leave_zero_active_administrators():
    make_admin()
    make_admin("second@example.com")
    outcomes = _race(
        lambda: services.disable_admin(SYSTEM, EMAIL),
        lambda: services.remove_admin(SYSTEM, "second@example.com"),
    )
    assert outcomes == ["last_administrator", "ok"]
    assert Account.objects.filter(account_type="admin", deactivated_at__isnull=True).count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_disables_never_leave_zero_active_administrators():
    make_admin()
    make_admin("second@example.com")
    outcomes = _race(
        lambda: services.disable_admin(SYSTEM, EMAIL),
        lambda: services.disable_admin(SYSTEM, "second@example.com"),
    )
    assert outcomes == ["last_administrator", "ok"]
    assert Account.objects.filter(account_type="admin", deactivated_at__isnull=True).count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_enable_and_disable_do_not_deadlock():
    make_admin()
    make_admin("second@example.com")
    services.disable_admin(SYSTEM, "second@example.com")
    outcomes = _race(
        lambda: services.enable_admin(SYSTEM, "second@example.com"),
        lambda: services.disable_admin(SYSTEM, EMAIL),
    )
    assert len(outcomes) == 2 and set(outcomes) <= {"ok", "last_administrator"}
    assert Account.objects.filter(account_type="admin", deactivated_at__isnull=True).count() >= 1


# --- retention of admin sessions ----------------


def test_purge_uses_the_idle_limit_of_the_account_type(db):
    admin = make_admin()
    user = make_user()
    now = timezone.now()

    def row(account_id, seen, **kw):
        return Session.objects.create(
            account_id=account_id,
            token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            created_at=seen,
            last_seen_at=seen,
            expires_at=now + timedelta(days=1),
            **kw,
        )

    old = now - timedelta(days=7, hours=1)
    stale_admin = row(admin.id, old)  # idle more than 30 minutes + 7 days
    kept_user = row(user.id, old)  # ordinary idle limit is 24 h: not yet 7 days past idle
    valid_admin = row(admin.id, now - timedelta(minutes=5))
    stale_user = row(user.id, now - timedelta(days=9))
    assert sessions.purge_stale(now, True) == 2
    assert sessions.purge_stale(now, False) == 2
    left = set(Session.objects.values_list("id", flat=True))
    assert left == {kept_user.id, valid_admin.id}
    assert stale_admin.id not in left and stale_user.id not in left


# --- more command behaviour ----------------


def test_command_enable_and_force_last(db, monkeypatch):
    run("create", "--email", EMAIL, "--password-stdin", stdin=PASSWORD, monkeypatch=monkeypatch)
    with pytest.raises(CommandError, match="last active administrator"):
        run("disable", "--email", EMAIL)
    assert "disabled" in run("disable", "--email", EMAIL, "--force-last")
    assert "enabled" in run("enable", "--email", EMAIL)
    assert "already enabled" in run("enable", "--email", EMAIL)
    with pytest.raises(CommandError):
        run("enable", "--email", EMAIL, "--force-last")
    with pytest.raises(CommandError):
        run("enable", "--email", EMAIL, "--password-stdin", stdin="x", monkeypatch=monkeypatch)
    assert admin_in().summary.account_type == "admin"


@pytest.mark.parametrize("stdin", ["", "\n", "\r\n"])
def test_command_stdin_empty_is_refused(db, monkeypatch, stdin):
    with pytest.raises(CommandError, match="required"):
        run("create", "--email", EMAIL, "--password-stdin", stdin=stdin, monkeypatch=monkeypatch)
    assert not Account.objects.exists()


def test_command_stdin_strips_a_carriage_return_and_reads_one_line(db, monkeypatch):
    run(
        "create",
        "--email",
        EMAIL,
        "--password-stdin",
        stdin=PASSWORD + "\r\nsecond line ignored\n",
        monkeypatch=monkeypatch,
    )
    assert admin_in().summary.account_type == "admin"


def test_command_stdin_over_long_is_cut_and_refused(db, monkeypatch):
    class Endless(io.StringIO):
        read_sizes: list = []

        def readline(self, size=-1):
            self.read_sizes.append(size)
            return super().readline(size)

    huge = Endless("x1" * 50000)
    monkeypatch.setattr("sys.stdin", huge)
    with pytest.raises(CommandError, match="at most 1024"):
        call_command("provision_admin", "create", "--email", EMAIL, "--password-stdin")
    assert huge.read_sizes == [1026]
    assert not Account.objects.exists()


def test_command_refuses_password_stdin_on_a_terminal(db, monkeypatch):
    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdin", Terminal(PASSWORD))
    with pytest.raises(CommandError, match="hidden prompt"):
        call_command("provision_admin", "create", "--email", EMAIL, "--password-stdin")
    assert not Account.objects.exists()


def test_command_turns_any_domain_error_into_one_fixed_line(db, monkeypatch):
    def boom(*a, **k):
        raise DomainError("server_error")

    monkeypatch.setattr(services, "disable_admin", boom)
    with pytest.raises(CommandError) as exc:
        run("disable", "--email", EMAIL)
    assert str(exc.value) == "The request was refused."
