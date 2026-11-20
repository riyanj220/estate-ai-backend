"""Password reset and password change at the service level (Story 1.18)."""

import hashlib
import logging
import re
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth.hashers import check_password
from django.core import mail as django_mail
from django.db import connection, transaction
from django.utils import timezone

from apps.identity import password_reset, services, subscribers, verification
from apps.identity.events import AccountSessionEnded
from apps.identity.models import Account, PasswordResetToken, Session
from apps.identity.services import password_flows
from apps.identity.state import AccountState
from apps.kernel import events, retention, runtime
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord, RateLimitCounter

VISITOR = Actor.visitor("req-1")
SYSTEM = Actor.system("req-2")
EMAIL = "Jane.Doe@Example.com"
PASSWORD = "correct horse battery staple"
NEW = "mango-river-lantern-42"
NET = "203.0.113.7"
T0 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)


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


def make(email=EMAIL, verified=True):
    view = services.create_account(SYSTEM, email, PASSWORD, "individual")
    if verified:
        services.mark_email_verified(SYSTEM, view.id)
    return view


def token_from(message):
    return re.search(r"token=([A-Za-z0-9_-]+)", message.body).group(1)


def ask(capture, email=EMAIL):
    with capture(execute=True):
        assert services.request_password_reset(VISITOR, email) is None


def reset_code(token, password=NEW):
    with pytest.raises(DomainError) as exc:
        services.confirm_password_reset(VISITOR, token, password)
    return exc.value


def sign_in(email=EMAIL, password=PASSWORD):
    return services.sign_in(VISITOR, email, password, NET)


def actor_for(view):
    return Actor.user(view.id, "req-3")


def session_ids(view):
    return list(Session.objects.filter(account_id=view.id).values_list("id", flat=True))


def change(view, session, current=PASSWORD, new=NEW):
    services.change_password(actor_for(view), session, current, new)


def change_error(view, session, current=PASSWORD, new=NEW):
    with pytest.raises(services.PasswordInvalid) as exc:
        change(view, session, current, new)
    return exc.value.fields


def open_sessions(view):
    return Session.objects.filter(account_id=view.id, ended_at__isnull=True).count()


# --- request -------------------------------------------------------------------------------------


def test_request_for_a_registered_address_mails_one_link(db, django_capture_on_commit_callbacks):
    view = make()
    ask(django_capture_on_commit_callbacks, "JANE.doe@example.com")
    assert len(django_mail.outbox) == 1
    message = django_mail.outbox[0]
    assert message.to == [EMAIL]
    assert message.subject == "Reset your EstateAI password"
    token = token_from(message)
    assert f"/reset-password?token={token}" in message.body
    assert "This link works once and expires in 1 hour." in message.body
    assert "If you did not ask for this, you can ignore this message." in message.body
    row = PasswordResetToken.objects.get()
    assert row.account_id == view.id
    assert row.expires_at - row.created_at == timedelta(hours=1)
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in row.token_hash


@pytest.mark.parametrize("condition", ["unverified", "suspended"])
def test_unverified_and_suspended_accounts_may_reset(
    db, django_capture_on_commit_callbacks, condition
):
    make(verified=condition != "unverified")
    if condition == "suspended":
        Account.objects.update(suspended_at=timezone.now(), suspension_reason="because")
    ask(django_capture_on_commit_callbacks)
    assert len(django_mail.outbox) == 1


@pytest.mark.parametrize("email", ["nobody@example.com", "", "x" * 300, None, 5, "a@b.co\x00"])
def test_unknown_or_odd_addresses_do_nothing(db, django_capture_on_commit_callbacks, email):
    make()
    ask(django_capture_on_commit_callbacks, email)
    assert django_mail.outbox == [] and PasswordResetToken.objects.count() == 0


def test_deactivated_accounts_get_nothing(db, django_capture_on_commit_callbacks):
    make()
    Account.objects.update(deactivated_at=timezone.now())
    ask(django_capture_on_commit_callbacks)
    assert django_mail.outbox == [] and PasswordResetToken.objects.count() == 0


def test_newest_link_wins(db, django_capture_on_commit_callbacks):
    make()
    ask(django_capture_on_commit_callbacks)
    ask(django_capture_on_commit_callbacks)
    old, new = (token_from(m) for m in django_mail.outbox)
    assert reset_code(old).code == "link_used"
    assert PasswordResetToken.objects.filter(used_at__isnull=True).count() == 1
    services.confirm_password_reset(VISITOR, new, NEW)


def test_account_cap_silently_stops_the_sixth_email(db, django_capture_on_commit_callbacks):
    make()
    for _ in range(8):
        ask(django_capture_on_commit_callbacks)
    assert len(django_mail.outbox) == 5
    assert PasswordResetToken.objects.count() == 5


def test_request_emits_only_the_account_id(db, django_capture_on_commit_callbacks):
    from apps.identity.events import AccountPasswordResetRequested

    view = make()
    seen = []
    events.subscribe(AccountPasswordResetRequested, seen.append)
    ask(django_capture_on_commit_callbacks)
    assert seen == [AccountPasswordResetRequested(account_id=view.id)]
    assert not hasattr(seen[0], "email") and not hasattr(seen[0], "token")


def test_rollback_leaves_no_token_and_no_email(db, django_capture_on_commit_callbacks):
    make()
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                services.request_password_reset(VISITOR, EMAIL)
                raise RuntimeError
    assert PasswordResetToken.objects.count() == 0 and django_mail.outbox == []


def test_handler_failure_is_logged_by_class_and_leaves_no_token(
    db, django_capture_on_commit_callbacks, monkeypatch, caplog
):
    make()

    def boom(*args):
        raise ConnectionError("secret-address@example.com")

    monkeypatch.setattr("apps.identity.notices.send_password_reset", boom)
    caplog.set_level(logging.DEBUG)
    ask(django_capture_on_commit_callbacks)
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    assert "ConnectionError" in logged and "secret-address" not in logged
    assert PasswordResetToken.objects.count() == 0  # the token rolled back: ask again
    monkeypatch.undo()
    ask(django_capture_on_commit_callbacks)
    assert len(django_mail.outbox) == 1


def test_issue_is_system_only_and_skips_unknown_and_deactivated(db):
    view = make()
    assert services.issue_password_reset_token(SYSTEM, uuid.uuid4()) is None
    with pytest.raises(DomainError):
        services.issue_password_reset_token(VISITOR, view.id)
    Account.objects.update(deactivated_at=timezone.now())
    assert services.issue_password_reset_token(SYSTEM, view.id) is None
    assert PasswordResetToken.objects.count() == 0


# --- confirm -------------------------------------------------------------------------------------


@pytest.fixture
def link(db, django_capture_on_commit_callbacks):
    view = make()
    ask(django_capture_on_commit_callbacks)
    return view, token_from(django_mail.outbox[0])


def test_confirm_resets_ends_every_session_audits_and_notifies(
    link, django_capture_on_commit_callbacks
):
    view, token = link
    sign_in(), sign_in(), sign_in()
    seen = []
    events.subscribe(AccountSessionEnded, seen.append)
    django_mail.outbox.clear()
    before = Account.objects.get().email_verified_at
    with django_capture_on_commit_callbacks(execute=True):
        assert services.confirm_password_reset(VISITOR, token, NEW) is None
        # sessions end in the transaction itself, before any after-commit handler runs
        assert open_sessions(view) == 0
    account = Account.objects.get()
    assert check_password(NEW, account.password) and not check_password(PASSWORD, account.password)
    assert account.email_verified_at == before and account.state is AccountState.ACTIVE
    assert PasswordResetToken.objects.get().used_at is not None
    assert {s.ended_reason for s in Session.objects.all()} == {"password_changed"}
    record = AuditRecord.objects.get(action="account.password_reset")
    assert record.metadata == {} and record.target_id == view.id
    assert [(e.account_id, e.reason, e.except_session_id) for e in seen] == [
        (view.id, "password_changed", None)
    ]
    assert seen[0].ended_before is not None
    assert "all of its sessions were signed out" in django_mail.outbox[0].body
    assert [(m.to, m.subject) for m in django_mail.outbox] == [
        ([EMAIL], "Your EstateAI password was changed")
    ]
    body = django_mail.outbox[0].body
    assert NEW not in body and "http" not in body and token not in body
    assert "If this was not you, reset your password again" in body
    assert reset_code(token).code == "link_used"
    with pytest.raises(DomainError):
        sign_in()
    assert sign_in(password=NEW).summary.email == EMAIL


def test_expired_link_changes_nothing(link):
    view, token = link
    PasswordResetToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    sign_in()
    assert reset_code(token).code == "link_expired"
    account = Account.objects.get()
    assert check_password(PASSWORD, account.password)
    assert PasswordResetToken.objects.get().used_at is None
    assert open_sessions(view) == 1


def test_expiry_boundary_and_precedence(link, monkeypatch):
    _, token = link
    now = timezone.now()
    monkeypatch.setattr("apps.identity.services.timezone.now", lambda: now)
    PasswordResetToken.objects.update(expires_at=now)
    assert reset_code(token).code == "link_expired"
    PasswordResetToken.objects.update(used_at=now)
    assert reset_code(token).code == "link_used"  # used wins over expired


@pytest.mark.parametrize(
    "bad", ["", "nope", "a" * 201, "a" * 43, "bad token", "tok\x00en", None, 5, ["x"], {"a": 1}]
)
def test_unknown_or_malformed_tokens_are_invalid(link, bad):
    assert reset_code(bad).code == "link_invalid"


@pytest.mark.parametrize(
    ("password", "code"),
    [
        ("", "required"),
        (None, "required"),
        ("   ", "required"),
        (5, "invalid"),
        ("short", "password_too_short"),
        ("x" * 1025, "password_too_long"),
        ("jane.doe@example.com", "password_matches_email"),
        ("jane.doe", "password_matches_email"),
        ("password123", "password_too_common"),
    ],
)
def test_bad_password_is_a_field_error_and_does_not_use_up_the_link(link, password, code):
    view, token = link
    sign_in()
    error = reset_code(token, password)
    assert isinstance(error, services.PasswordInvalid) and error.fields == {"password": code}
    assert PasswordResetToken.objects.get().used_at is None
    assert open_sessions(view) == 1
    assert check_password(PASSWORD, Account.objects.get().password)
    assert AuditRecord.objects.filter(action="account.password_reset").count() == 0
    services.confirm_password_reset(VISITOR, token, NEW)  # the same link then works


def test_field_errors_only_appear_for_a_valid_link(link):
    _, token = link
    assert reset_code("unknown-token-value", "short").code == "link_invalid"
    PasswordResetToken.objects.update(used_at=timezone.now())
    assert reset_code(token, "short").code == "link_used"
    PasswordResetToken.objects.update(used_at=None, expires_at=timezone.now())
    assert reset_code(token, "short").code == "link_expired"


def test_failures_before_the_password_check_do_no_hashing(link, monkeypatch):
    _, token = link
    calls = []
    monkeypatch.setattr(password_flows, "make_password", lambda *a: calls.append(a) or "x")
    reset_code("unknown-token-value")
    reset_code(token, "short")
    PasswordResetToken.objects.update(used_at=timezone.now())
    reset_code(token)
    assert calls == []


def test_deactivated_account_link_is_invalid(link):
    _, token = link
    Account.objects.update(deactivated_at=timezone.now())
    assert reset_code(token).code == "link_invalid"
    assert PasswordResetToken.objects.get().used_at is None


def test_a_purged_token_is_invalid(link):
    _, token = link
    PasswordResetToken.objects.update(expires_at=timezone.now() - timedelta(days=8))
    assert password_reset.purge_expired(timezone.now(), False) == 1
    assert reset_code(token).code == "link_invalid"


def test_suspended_account_resets_and_stays_suspended(link):
    _, token = link
    Account.objects.update(suspended_at=timezone.now(), suspension_reason="because")
    services.confirm_password_reset(VISITOR, token, NEW)
    assert Account.objects.get().state is AccountState.SUSPENDED


def test_unverified_account_stays_unverified(db, django_capture_on_commit_callbacks):
    make(verified=False)
    ask(django_capture_on_commit_callbacks)
    services.confirm_password_reset(VISITOR, token_from(django_mail.outbox[0]), NEW)
    account = Account.objects.get()
    assert account.email_verified_at is None and account.state is AccountState.EMAIL_UNVERIFIED


def test_failure_after_hashing_rolls_everything_back(
    link, django_capture_on_commit_callbacks, monkeypatch
):
    view, token = link
    sign_in()
    before = Account.objects.get().password
    django_mail.outbox.clear()

    def boom(*args, **kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr("apps.kernel.audit.record", boom)
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError):
            services.confirm_password_reset(VISITOR, token, NEW)
    assert Account.objects.get().password == before
    assert PasswordResetToken.objects.get().used_at is None
    assert open_sessions(view) == 1
    assert django_mail.outbox == []


@pytest.mark.django_db(transaction=True)
def test_concurrent_confirmations_yield_exactly_one_success():
    runtime.shutdown()
    view = make()
    token = "concurrent-reset-token"
    PasswordResetToken.objects.create(
        account_id=view.id,
        token_hash=verification.hash_token(token),
        created_at=timezone.now(),
        expires_at=timezone.now() + timedelta(hours=1),
    )
    workers = 8
    barrier = threading.Barrier(workers)
    outcomes = []

    def work():
        try:
            barrier.wait()
            services.confirm_password_reset(VISITOR, token, NEW)
            outcomes.append("ok")
        except DomainError as exc:
            outcomes.append(exc.code)
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)
        finally:
            connection.close()

    threads = [threading.Thread(target=work) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("ok") == 1
    assert outcomes.count("link_used") == workers - 1
    assert AuditRecord.objects.filter(action="account.password_reset").count() == 1


# --- change --------------------------------------------------------------------------------------


def test_change_keeps_the_current_session_and_ends_the_others(
    db, django_capture_on_commit_callbacks
):
    view = make()
    tokens = [sign_in().token for _ in range(3)]
    keep = Session.objects.order_by("created_at", "id")[0]
    seen = []
    events.subscribe(AccountSessionEnded, seen.append)
    django_mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        change(view, keep.id)
        assert open_sessions(view) == 1  # ended in the transaction, before the handler
    assert Session.objects.get(ended_at=None).id == keep.id
    assert {s.ended_reason for s in Session.objects.exclude(id=keep.id)} == {"password_changed"}
    assert [(e.account_id, e.reason, e.except_session_id) for e in seen] == [
        (view.id, "password_changed", keep.id)
    ]
    assert seen[0].ended_before is not None
    assert "its other sessions were signed out" in django_mail.outbox[0].body
    account = Account.objects.get()
    assert check_password(NEW, account.password)
    record = AuditRecord.objects.get(action="account.password_changed")
    assert record.metadata == {} and record.actor_id == view.id
    assert [m.subject for m in django_mail.outbox] == ["Your EstateAI password was changed"]
    assert NEW not in django_mail.outbox[0].body and PASSWORD not in django_mail.outbox[0].body
    assert len(tokens) == 3


def test_event_exclusion_keeps_the_session_after_the_handler_runs(
    db, django_capture_on_commit_callbacks
):
    view = make()
    sign_in(), sign_in()
    keep, other = Session.objects.order_by("created_at", "id")
    with django_capture_on_commit_callbacks(execute=True):
        subscribers.end_sessions(
            AccountSessionEnded(view.id, "password_changed", except_session_id=keep.id)
        )
    keep.refresh_from_db()
    other.refresh_from_db()
    assert keep.ended_at is None and other.ended_reason == "password_changed"


def test_end_account_sessions_exclusion_validates(db):
    view = make()
    sign_in()
    for bad in ("junk", 5):
        with pytest.raises(DomainError):
            services.end_account_sessions(SYSTEM, view.id, "password_changed", bad)
    assert open_sessions(view) == 1


def test_wrong_current_password_changes_nothing(db):
    view = make()
    sign_in()
    session = Session.objects.get()
    before = Account.objects.get().password
    assert change_error(view, session.id, current="wrong") == {"current_password": "incorrect"}
    assert Account.objects.get().password == before
    assert open_sessions(view) == 1
    assert AuditRecord.objects.filter(action="account.password_changed").count() == 0


def test_same_and_weak_new_passwords(db):
    view = make()
    sign_in()
    sid = Session.objects.get().id
    assert change_error(view, sid, new=PASSWORD) == {"new_password": "password_same_as_current"}
    assert change_error(view, sid, new="short") == {"new_password": "password_too_short"}
    assert change_error(view, sid, new="password123") == {"new_password": "password_too_common"}
    assert change_error(view, sid, new="jane.doe") == {"new_password": "password_matches_email"}
    assert change_error(view, sid, new="x" * 1025) == {"new_password": "password_too_long"}
    assert check_password(PASSWORD, Account.objects.get().password)
    assert RateLimitCounter.objects.filter(scope="password_change_failed").count() == 0


def test_throttle_answers_incorrect_without_checking_and_clears_with_the_window(
    db, clock, monkeypatch
):
    view = make()
    sign_in()
    sid = Session.objects.get().id
    for _ in range(5):
        assert change_error(view, sid, current="wrong") == {"current_password": "incorrect"}
    calls = []
    real = password_flows.check_password
    monkeypatch.setattr(password_flows, "check_password", lambda *a: calls.append(a) or real(*a))
    assert change_error(view, sid) == {"current_password": "incorrect"}  # correct, but locked
    assert calls == []
    assert RateLimitCounter.objects.get(scope="password_change_failed").count == 5
    assert check_password(PASSWORD, Account.objects.get().password)
    clock(seconds=900)
    change(view, sid)
    assert check_password(NEW, Account.objects.get().password)


def test_throttle_is_per_account_and_success_does_not_reset(db):
    first = make()
    second = make("other@example.com")
    sign_in()
    sign_in("other@example.com")
    s1 = Session.objects.get(account_id=first.id).id
    s2 = Session.objects.get(account_id=second.id).id
    for _ in range(5):
        change_error(first, s1, current="wrong")
    change(second, s2)  # another account is unaffected
    assert change_error(first, s1) == {"current_password": "incorrect"}


def test_oversized_current_password_is_incorrect_without_hashing(db, monkeypatch):
    view = make()
    sign_in()
    calls = []
    real = password_flows.check_password
    monkeypatch.setattr(password_flows, "check_password", lambda *a: calls.append(a) or real(*a))
    assert change_error(view, Session.objects.get().id, current="x" * 1025) == {
        "current_password": "incorrect"
    }
    assert calls == []


def test_change_needs_a_signed_in_actor_and_a_live_session_of_the_account(db):
    view = make()
    other = make("other@example.com")
    sign_in()
    sign_in("other@example.com")
    mine = Session.objects.get(account_id=view.id)
    theirs = Session.objects.get(account_id=other.id)
    with pytest.raises(DomainError) as exc:
        services.change_password(VISITOR, mine.id, PASSWORD, NEW)
    assert exc.value.code == "unauthenticated"
    for sid in (None, "junk", uuid.uuid4(), theirs.id):  # no session, or someone else's
        with pytest.raises(DomainError) as exc:
            change(view, sid)
        assert exc.value.code == "unauthenticated"
    Session.objects.filter(id=mine.id).update(ended_at=timezone.now(), ended_reason="sign_out")
    with pytest.raises(DomainError) as exc:
        change(view, mine.id)
    assert exc.value.code == "unauthenticated"
    assert check_password(PASSWORD, Account.objects.get(id=view.id).password)


def test_deactivated_account_cannot_change(db):
    view = make()
    sign_in()
    sid = Session.objects.get().id
    Account.objects.update(deactivated_at=timezone.now())
    with pytest.raises(DomainError) as exc:
        change(view, sid)
    assert exc.value.code == "unauthenticated"


@pytest.mark.parametrize(
    ("current", "new"), [(None, NEW), (PASSWORD, None), (5, NEW), (PASSWORD, [])]
)
def test_non_string_passwords_are_validation_errors(db, current, new):
    view = make()
    sign_in()
    with pytest.raises(DomainError) as exc:
        change(view, Session.objects.get().id, current, new)
    assert exc.value.code == "validation_error"


def test_failure_after_hashing_rolls_the_change_back(
    db, django_capture_on_commit_callbacks, monkeypatch
):
    view = make()
    sign_in(), sign_in()
    sid = Session.objects.order_by("created_at", "id")[0].id
    before = Account.objects.get().password
    django_mail.outbox.clear()

    def boom(*args, **kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr("apps.kernel.audit.record", boom)
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError):
            change(view, sid)
    assert Account.objects.get().password == before
    assert open_sessions(view) == 2 and django_mail.outbox == []


@pytest.mark.django_db(transaction=True)
def test_parallel_wrong_attempts_never_push_the_counter_past_the_cap():
    from apps.kernel.models import RateLimitPolicy

    for scope, kind in (("sign_in", "client"), ("password_change_failed", "account")):
        RateLimitPolicy.objects.update_or_create(
            scope=scope,
            defaults={"limit": 5, "window_seconds": 900, "key_kind": kind, "note": "t"},
        )
    runtime.shutdown()
    view = make()
    sign_in()
    sid = Session.objects.get().id
    outcomes, errors = [], []
    barrier = threading.Barrier(20)

    def worker():
        try:
            barrier.wait()
            try:
                change(view, sid, current="wrong")
            except services.PasswordInvalid as exc:
                outcomes.append(exc.fields["current_password"])
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and outcomes == ["incorrect"] * 20
    assert RateLimitCounter.objects.get(scope="password_change_failed").count == 5
    assert change_error(view, sid) == {"current_password": "incorrect"}


# --- audit, privacy, retention -------------------------------------------------------------------


def test_only_the_two_audit_actions_exist_and_nothing_leaks(
    db, django_capture_on_commit_callbacks, caplog
):
    caplog.set_level(logging.DEBUG)
    view = make()
    ask(django_capture_on_commit_callbacks)
    token = token_from(django_mail.outbox[0])
    reset_code(token, "short")
    with django_capture_on_commit_callbacks(execute=True):
        services.confirm_password_reset(VISITOR, token, NEW)
    sign_in(password=NEW)
    with django_capture_on_commit_callbacks(execute=True):
        change(view, Session.objects.get(ended_at=None).id, NEW, "another-long-phrase-77")
    actions = sorted(AuditRecord.objects.values_list("action", flat=True))
    assert actions == [
        "account.created",
        "account.email_verified",
        "account.password_changed",
        "account.password_reset",
    ]
    blob = " ".join(str(a.metadata) + a.action + a.reason for a in AuditRecord.objects.all())
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    for secret in (
        token,
        verification.hash_token(token),
        EMAIL,
        EMAIL.lower(),
        NEW,
        PASSWORD,
        "another-long-phrase-77",
        Account.objects.get().password,
    ):
        assert secret not in blob and secret not in logged


def test_retention_purges_stale_used_or_expired_tokens(db):
    view = make()
    now = timezone.now()

    def make_token(label, expires, used=None):
        PasswordResetToken.objects.create(
            account_id=view.id,
            token_hash=label,
            created_at=now - timedelta(days=30),
            expires_at=expires,
            used_at=used,
        )

    make_token("expired-old", now - timedelta(days=8))
    make_token("used-old", now + timedelta(hours=1), now - timedelta(days=8))
    make_token("expired-recent", now - timedelta(days=6))
    make_token("used-recent", now + timedelta(hours=1), now - timedelta(days=1))
    make_token("live", now + timedelta(hours=1))
    assert password_reset.purge_expired(now, True) == 2
    assert PasswordResetToken.objects.count() == 5
    assert password_reset.purge_expired(now, False) == 2
    assert set(PasswordResetToken.objects.values_list("token_hash", flat=True)) == {
        "expired-recent",
        "used-recent",
        "live",
    }
    assert "identity.password_reset_tokens" in retention.registered()


def test_subscribers_are_registered_once():
    from apps.identity.events import AccountPasswordResetRequested

    subscribers.register()
    subscribers.register()
    assert events.is_subscribed(
        AccountPasswordResetRequested, subscribers.send_password_reset_email
    )


def test_account_deletion_cascades_to_tokens(db):
    view = make()
    services.issue_password_reset_token(SYSTEM, view.id)
    Account.objects.all().delete()
    assert PasswordResetToken.objects.count() == 0


# --- review follow-ups ---------------------------------------------------------------------------


def _insert_token(view, label, **extra):
    return PasswordResetToken.objects.create(
        account_id=view.id,
        token_hash=verification.hash_token(label),
        created_at=timezone.now(),
        expires_at=timezone.now() + timedelta(hours=1),
        **extra,
    )


def test_confirming_one_of_two_unused_tokens_marks_the_other_used(db):
    view = make()
    _insert_token(view, "first-token-value")
    other = _insert_token(view, "second-token-value")
    services.confirm_password_reset(VISITOR, "first-token-value", NEW)
    other.refresh_from_db()
    assert other.used_at is not None
    assert reset_code("second-token-value").code == "link_used"


def test_change_voids_outstanding_reset_links(db, django_capture_on_commit_callbacks):
    view = make()
    ask(django_capture_on_commit_callbacks)
    token = token_from(django_mail.outbox[0])
    sign_in()
    change(view, Session.objects.get().id)
    assert PasswordResetToken.objects.filter(used_at__isnull=True).count() == 0
    assert reset_code(token, "another-long-phrase-77").code == "link_used"


def test_counter_stays_at_the_failures_after_a_success_and_success_alone_adds_none(db):
    view = make()
    sign_in()
    sid = Session.objects.get().id
    assert RateLimitCounter.objects.filter(scope="password_change_failed").count() == 0
    for _ in range(3):
        change_error(view, sid, current="wrong")
    change(view, sid)
    assert RateLimitCounter.objects.get(scope="password_change_failed").count == 3
    view2 = make("other@example.com")
    sign_in("other@example.com")
    change(view2, Session.objects.get(account_id=view2.id).id)
    assert RateLimitCounter.objects.filter(scope="password_change_failed").count() == 1


def test_reset_audit_actor_is_the_system_actor(link):
    _, token = link
    services.confirm_password_reset(VISITOR, token, NEW)
    record = AuditRecord.objects.get(action="account.password_reset")
    assert (record.actor_kind, record.actor_id) == ("system", None)


def test_unusable_password_accounts_are_treated_like_unknown(
    db, django_capture_on_commit_callbacks
):
    from django.contrib.auth.hashers import make_password

    view = make()
    Account.objects.update(password=make_password(None))
    ask(django_capture_on_commit_callbacks)
    assert django_mail.outbox == [] and PasswordResetToken.objects.count() == 0
    assert services.issue_password_reset_token(SYSTEM, view.id) is None


def test_request_path_writes_one_counter_row_for_every_branch(
    db, django_capture_on_commit_callbacks
):
    make()
    make("gone@example.com")
    Account.objects.filter(email="gone@example.com").update(deactivated_at=timezone.now())

    def counters():
        return RateLimitCounter.objects.filter(scope="password_reset_email")

    for email in (EMAIL, "nobody@example.com", "gone@example.com", "not-an-address", " "):
        before = sum(c.count for c in counters())
        ask(django_capture_on_commit_callbacks, email)
        assert sum(c.count for c in counters()) == before + 1, email
    assert counters().count() == 5  # known and unknown keys differ; none holds a raw address
    assert all("example.com" not in c.key_hash for c in counters())


def test_unknown_addresses_are_capped_silently_and_nothing_is_sent(
    db, django_capture_on_commit_callbacks
):
    for _ in range(8):
        ask(django_capture_on_commit_callbacks, "nobody@example.com")
    assert django_mail.outbox == []
    assert RateLimitCounter.objects.get(scope="password_reset_email").count == 6  # capped store


def test_delayed_handler_with_ended_before_spares_a_later_session(db, clock):
    view = make()
    sign_in()
    cutoff = timezone.now()
    clock(seconds=5)
    sign_in()
    with pytest.raises(DomainError):
        services.end_account_sessions(SYSTEM, view.id, "password_changed", None, "not-a-date")
    subscribers.end_sessions(AccountSessionEnded(view.id, "password_changed", ended_before=cutoff))
    first, later = Session.objects.order_by("created_at")
    assert first.ended_reason == "password_changed" and later.ended_at is None


def test_stale_handler_after_reset_spares_a_session_signed_in_afterwards(
    link, clock, django_capture_on_commit_callbacks
):
    view, token = link
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        services.confirm_password_reset(VISITOR, token, NEW)
    clock(seconds=5)
    sign_in(password=NEW)  # opened after the commit, before the handler runs
    for callback in callbacks:
        callback()
    assert open_sessions(view) == 1


def test_confirm_leaves_suspended_and_unverified_state_and_verification_unchanged(
    db, django_capture_on_commit_callbacks
):
    make(verified=False)
    ask(django_capture_on_commit_callbacks)
    services.confirm_password_reset(VISITOR, token_from(django_mail.outbox[-1]), NEW)
    assert Account.objects.get().email_verified_at is None
    Account.objects.update(suspended_at=timezone.now(), suspension_reason="because")
    before = Account.objects.get()
    ask(django_capture_on_commit_callbacks)
    services.confirm_password_reset(
        VISITOR, token_from(django_mail.outbox[-1]), "another-long-phrase-77"
    )
    after = Account.objects.get()
    assert (after.state, after.email_verified_at) == (AccountState.SUSPENDED, None)
    assert before.email_verified_at is None


def test_seed_migration_is_idempotent_and_unseeds_only_defaults(db):
    import importlib
    from types import SimpleNamespace

    from django.apps import apps
    from django.db import connection

    from apps.kernel.models import RateLimitPolicy

    module = importlib.import_module("apps.kernel.migrations.0007_seed_password_policies")
    schema = SimpleNamespace(connection=connection)
    scopes = ["password_reset_request", "password_reset_confirm", "password_change_failed"]
    module.seed(apps, schema)
    module.seed(apps, schema)
    assert RateLimitPolicy.objects.filter(scope__in=scopes).count() == 3
    RateLimitPolicy.objects.filter(scope="password_reset_confirm").update(limit=99)
    module.unseed(apps, schema)
    assert list(
        RateLimitPolicy.objects.filter(scope__in=scopes).values_list("scope", flat=True)
    ) == ["password_reset_confirm"]
