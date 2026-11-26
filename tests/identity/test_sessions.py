"""Sessions at the service level: sign-in, throttle, lifetimes, sign-out, events, retention."""

import hashlib
import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.utils import timezone

from apps.identity import passwords, selectors, services, sessions, subscribers
from apps.identity import policy as identity_policy
from apps.identity.capabilities import Capability
from apps.identity.events import AccountSessionEnded, emit_session_ended
from apps.identity.models import Account, Session
from apps.identity.services import sessions_auth
from apps.identity.state import AccountState
from apps.kernel import events, ratelimit, retention, runtime
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord, RateLimitCounter

SYSTEM = Actor.system("req-1")
VISITOR = Actor.visitor("req-2")
ADMIN = Actor.admin(uuid.UUID("33333333-3333-4333-8333-333333333333"), "req-3")
EMAIL = "Jane.Doe@Example.com"
PASSWORD = "correct horse battery staple"
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
    """A movable clock for every `timezone.now()` call."""
    state = {"now": T0}
    monkeypatch.setattr(timezone, "now", lambda: state["now"])

    def move(**delta):
        state["now"] = state["now"] + timedelta(**delta)

    return move


def make(email=EMAIL, kind="individual", verified=True):
    view = services.create_account(SYSTEM, email, PASSWORD, kind)
    if verified:
        services.mark_email_verified(SYSTEM, view.id)
    return view


def sign_in(email=EMAIL, password=PASSWORD, network=NET, current=None):
    return services.sign_in(VISITOR, email, password, network, current)


def code_of(call, *args):
    with pytest.raises(DomainError) as exc:
        call(*args)
    return exc.value.code


def fail(times, email=EMAIL, network=NET):
    for _ in range(times):
        assert code_of(sign_in, email, "wrong", network) == "invalid_credentials"


# --- sign-in -------------------------------------------------------------------------------------


def test_sign_in_issues_a_session_and_stores_only_the_hash(db, clock):
    account = make()
    result = sign_in("  jane.doe@example.COM ")
    row = Session.objects.get()
    assert row.token_hash == hashlib.sha256(result.token.encode()).hexdigest()
    assert result.token not in (row.token_hash, str(row.id))
    assert len(result.token) == 43 and row.account_id == account.id
    assert (row.created_at, row.last_seen_at) == (T0, T0)
    assert row.expires_at == T0 + timedelta(days=14)
    assert (row.ended_at, row.ended_reason) == (None, "")
    assert result.summary.state is AccountState.ACTIVE
    assert result.summary.email == EMAIL
    for value in vars(row).values():
        assert result.token not in str(value)


@pytest.mark.parametrize(
    ("verified", "state"),
    [(False, AccountState.EMAIL_UNVERIFIED), (True, AccountState.SUSPENDED)],
)
def test_unverified_and_suspended_accounts_can_sign_in(db, verified, state):
    view = make(verified=verified)
    if state is AccountState.SUSPENDED:
        services.suspend_account(ADMIN, view.id, "policy breach")
    result = sign_in()
    assert result.summary.state is state
    assert "sign_in" in result.summary.capabilities
    assert "publish" not in result.summary.capabilities


def test_deactivated_unknown_and_wrong_password_raise_the_same_error(db):
    view = make()
    make("other@example.com")
    services.deactivate(ADMIN, view.id)
    assert code_of(sign_in) == "invalid_credentials"
    assert code_of(sign_in, "nobody@example.com") == "invalid_credentials"
    assert code_of(sign_in, "other@example.com", "wrong") == "invalid_credentials"
    assert Session.objects.count() == 0


@pytest.mark.parametrize("email", ["", "   ", "a" * 300, "a\x00b@example.com"])
def test_malformed_emails_are_invalid_credentials(db, email):
    make()
    assert code_of(sign_in, email) == "invalid_credentials"


@pytest.mark.parametrize("email, password", [(None, "x"), ("a@b.co", None), (1, 2), ([], {})])
def test_non_string_fields_are_validation_errors(db, email, password):
    assert code_of(sign_in, email, password) == "validation_error"


def test_oversized_password_fails_without_hashing(db, monkeypatch):
    make()
    calls = []
    monkeypatch.setattr(sessions_auth, "check_password", lambda *a: calls.append(a) or True)
    assert code_of(sign_in, EMAIL, "x" * 2000) == "invalid_credentials"
    assert calls == []


def test_every_failure_runs_exactly_one_hash_comparison(db, monkeypatch):
    view = make()
    make("deact@example.com")
    services.deactivate(ADMIN, selectors_id("deact@example.com"))
    calls = []
    real = sessions_auth.check_password
    monkeypatch.setattr(sessions_auth, "check_password", lambda *a: calls.append(a) or real(*a))
    for email in (EMAIL, "unknown@example.com", "deact@example.com"):
        calls.clear()
        assert code_of(sign_in, email, "wrong") == "invalid_credentials"
        assert len(calls) == 1
    calls.clear()
    sign_in()
    assert len(calls) == 1 and view.id


def selectors_id(email):
    return Account.objects.get(email=email).id


def test_sign_in_ends_a_held_session_as_replaced(db):
    make()
    first = sign_in()
    second = sign_in(current=first.token)
    assert second.token != first.token
    old, new = Session.objects.order_by("created_at")
    assert (old.ended_reason, old.ended_at is not None) == ("replaced", True)
    assert new.ended_at is None
    # a junk or unknown current token changes nothing
    sign_in(current="not-a-token")
    assert Session.objects.filter(ended_at__isnull=True).count() == 2


# --- throttle ------------------------------------------------------------------------------------


def test_lockout_after_five_failures_blocks_even_the_right_password(db, clock, monkeypatch):
    make()
    fail(5)
    calls = []
    real = sessions_auth.check_password
    monkeypatch.setattr(sessions_auth, "check_password", lambda *a: calls.append(a) or real(*a))
    assert code_of(sign_in, EMAIL, "wrong") == "invalid_credentials"  # the 6th
    assert code_of(sign_in) == "invalid_credentials"  # right password, locked
    assert len(calls) == 2  # each of the two locked attempts costs one dummy-hash comparison
    assert Session.objects.count() == 0
    assert RateLimitCounter.objects.get(scope="sign_in").count == 5  # lockout adds nothing
    clock(seconds=900)
    assert sign_in().summary.email == EMAIL  # window over


def test_lockout_is_per_email_and_per_client_network(db):
    make()
    make("other@example.com")
    fail(5)
    assert sign_in("other@example.com").summary.email == "other@example.com"
    assert sign_in(network="198.51.100.9").summary.email == EMAIL
    assert code_of(sign_in) == "invalid_credentials"


def test_lockout_key_ignores_email_case_and_padding(db):
    make()
    fail(3)
    for variant in ("JANE.DOE@example.com", "  jane.doe@example.com "):
        assert code_of(sign_in, variant, "wrong") == "invalid_credentials"
    assert code_of(sign_in) == "invalid_credentials"  # 5 failures reached across the variants


def test_success_does_not_reset_and_unknown_email_throttles_too(db):
    make()
    fail(2)
    sign_in()
    fail(3)
    assert code_of(sign_in) == "invalid_credentials"
    fail(5, "ghost@example.com")
    assert RateLimitCounter.objects.filter(scope="sign_in").count() == 2


def test_failed_and_throttled_sign_ins_write_no_audit_record(db):
    make()
    before = AuditRecord.objects.count()
    sign_in()
    fail(6)
    assert AuditRecord.objects.count() == before


@pytest.mark.django_db(transaction=True)
def test_parallel_failures_never_push_the_counter_past_the_cap():
    from apps.kernel.models import RateLimitPolicy

    RateLimitPolicy.objects.update_or_create(
        scope="sign_in",
        defaults={"limit": 5, "window_seconds": 900, "key_kind": "client", "note": "t"},
    )
    make()
    errors, outcomes = [], []
    barrier = threading.Barrier(20)

    def worker():
        try:
            barrier.wait()
            try:
                sign_in(password="wrong")
            except DomainError as exc:
                outcomes.append(exc.code)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert outcomes == ["invalid_credentials"] * 20
    assert (
        RateLimitCounter.objects.get(scope="sign_in").count == 5
    )  # serialised: peek stops hits at the limit
    assert code_of(sign_in) == "invalid_credentials"  # the right password after saturation
    assert Session.objects.count() == 0


# --- lifetimes and refresh -----------------------------------------------------------------------


def live(token, **_):
    return selectors.live_session(token, timezone.now())


def test_idle_and_absolute_expiry(db, clock):
    make()
    token = sign_in().token
    clock(hours=23, minutes=59)
    assert live(token) is not None
    clock(minutes=1)
    assert live(token) is None  # 24 h idle

    token = sign_in().token
    for _ in range(13):  # active every 12 hours: the idle limit never trips
        clock(hours=12)
        services.refresh_session(SYSTEM, live(token).id)
        clock(hours=12)
        assert live(token) is not None
        services.refresh_session(SYSTEM, live(token).id)
    clock(days=1)
    assert live(token) is None  # the absolute 14 days are over, however active


def test_ended_unknown_and_malformed_tokens_are_not_live(db):
    make()
    token = sign_in().token
    assert live(token) is not None
    Session.objects.update(ended_at=timezone.now(), ended_reason="sign_out")
    assert live(token) is None
    for bad in (None, "", "x" * 43, "x" * 42, 5, "a" * 200):
        assert live(bad) is None


def test_last_seen_refreshes_at_most_once_per_five_minutes(db, clock):
    make()
    token = sign_in().token
    session = live(token)
    clock(minutes=4, seconds=59)
    services.refresh_session(SYSTEM, session.id)
    assert Session.objects.get().last_seen_at == T0
    clock(seconds=1)
    services.refresh_session(SYSTEM, session.id)
    assert Session.objects.get().last_seen_at == T0 + timedelta(minutes=5)


def test_refresh_is_system_only_and_never_revives_an_ended_session(db, clock):
    make()
    token = sign_in().token
    session = live(token)
    assert code_of(services.refresh_session, VISITOR, session.id) == "unauthenticated"
    Session.objects.update(ended_at=T0, ended_reason="sign_out")
    clock(hours=1)
    services.refresh_session(SYSTEM, session.id)
    assert Session.objects.get().last_seen_at == T0


def test_deactivated_account_session_is_not_live_even_before_the_event_runs(db):
    view = make()
    token = sign_in().token
    Account.objects.filter(id=view.id).update(deactivated_at=timezone.now())
    assert live(token) is None


def test_suspended_account_keeps_its_session_and_shows_suspended(db):
    view = make()
    token = sign_in().token
    services.suspend_account(ADMIN, view.id, "policy breach")
    session = live(token)
    assert session is not None
    summary = selectors.get_actor_summary(session.account_id)
    assert summary.state is AccountState.SUSPENDED


# --- sign-out and events -------------------------------------------------------------------------


def test_sign_out_ends_only_the_current_session_and_emits(db, django_capture_on_commit_callbacks):
    view = make()
    seen = []
    events.subscribe(AccountSessionEnded, seen.append)
    a, b = sign_in(), sign_in()
    session = live(a.token)
    actor = Actor.user(view.id, "r")
    with django_capture_on_commit_callbacks(execute=True):
        services.sign_out(actor, session.id)
    assert seen == [AccountSessionEnded(account_id=view.id, reason="sign_out")]
    assert live(a.token) is None and live(b.token) is not None
    row = Session.objects.get(id=session.id)
    assert row.ended_reason == "sign_out" and row.ended_at is not None
    assert code_of(services.sign_out, actor, session.id) == "unauthenticated"  # twice


def test_sign_out_rejects_visitors_and_other_accounts_sessions(db):
    view = make()
    other = make("other@example.com")
    session = live(sign_in().token)
    assert code_of(services.sign_out, VISITOR, session.id) == "unauthenticated"
    assert code_of(services.sign_out, Actor.user(other.id), session.id) == "unauthenticated"
    assert code_of(services.sign_out, Actor.user(view.id), "junk") == "unauthenticated"
    assert code_of(services.sign_out, Actor.user(view.id), None) == "unauthenticated"
    assert live(sign_in().token) is not None and Session.objects.filter(ended_at=None).count() == 2


def test_sign_out_event_does_not_end_other_sessions(db, django_capture_on_commit_callbacks):
    view = make()
    keep = sign_in()
    gone = live(sign_in().token)
    with django_capture_on_commit_callbacks(execute=True):
        services.sign_out(Actor.user(view.id), gone.id)
    assert live(keep.token) is not None


@pytest.mark.parametrize("reason", ["password_changed", "deactivated"])
def test_these_reasons_end_every_open_session_of_that_account(
    db, django_capture_on_commit_callbacks, reason
):
    view = make()
    other = make("other@example.com")
    mine = [sign_in().token, sign_in().token]
    theirs = sign_in("other@example.com").token
    with django_capture_on_commit_callbacks(execute=True):
        emit_session_ended(view.id, reason)
    assert [live(t) for t in mine] == [None, None]
    assert live(theirs) is not None
    assert {r.ended_reason for r in Session.objects.filter(account_id=view.id)} == {reason}
    assert other.id


def test_deactivation_stops_sessions_immediately(db, django_capture_on_commit_callbacks):
    view = make()
    token = sign_in().token
    with django_capture_on_commit_callbacks(execute=True):
        services.deactivate(ADMIN, view.id)
    assert live(token) is None
    assert Session.objects.get().ended_reason == "deactivated"
    assert selectors.get_actor_summary(view.id) is None


def test_end_account_sessions_is_system_only_and_validates(db):
    view = make()
    assert code_of(services.end_account_sessions, VISITOR, view.id, "deactivated") == (
        "unauthenticated"
    )
    assert code_of(services.end_account_sessions, SYSTEM, view.id, "sign_out") == (
        "validation_error"
    )
    assert code_of(services.end_account_sessions, SYSTEM, "junk", "deactivated") == (
        "validation_error"
    )


def test_session_end_subscriber_is_registered_once():
    subscribers.register()
    subscribers.register()
    assert events.is_subscribed(AccountSessionEnded, subscribers.end_sessions)


# --- retention -----------------------------------------------------------------------------------


def test_retention_purges_old_ended_and_expired_sessions_only(db):
    view = make()
    now = timezone.now()

    def add(name, **kw):
        base = {"created_at": now, "last_seen_at": now, "expires_at": now + timedelta(days=14)}
        Session.objects.create(
            account_id=view.id,
            token_hash=hashlib.sha256(name.encode()).hexdigest(),
            **{**base, **kw},
        )

    add("live")
    add("ended-recent", ended_at=now - timedelta(days=6), ended_reason="sign_out")
    add("ended-old", ended_at=now - timedelta(days=8), ended_reason="sign_out")
    add("expired-old", expires_at=now - timedelta(days=8))
    add("expired-recent", expires_at=now - timedelta(days=6))
    add("idle-old", last_seen_at=now - timedelta(days=9))
    add("idle-recent", last_seen_at=now - timedelta(days=7, hours=12))
    assert "identity.sessions" in retention.registered()
    assert sessions.purge_stale(now, dry_run=True) == 3
    assert Session.objects.count() == 7
    assert sessions.purge_stale(now, dry_run=False) == 3
    assert Session.objects.count() == 4
    gone = {
        hashlib.sha256(n.encode()).hexdigest() for n in ("ended-old", "expired-old", "idle-old")
    }
    assert not Session.objects.filter(token_hash__in=gone).exists()


def test_account_deletion_cascades_to_sessions(db):
    view = make()
    sign_in()
    Account.objects.filter(id=view.id).delete()
    assert Session.objects.count() == 0


# --- summary and capabilities --------------------------------------------------------------------


def test_summary_carries_profile_name_and_matrix_capabilities(db):
    view = make(verified=True)
    summary = selectors.get_actor_summary(view.id)
    assert (summary.full_name, summary.email, summary.account_type) == ("", EMAIL, "individual")
    assert summary.capabilities == ("sign_in", "browse", "view_drafts", "edit_drafts")
    assert selectors.get_actor_summary("junk") is None
    assert selectors.get_actor_summary(uuid.uuid4()) is None


@pytest.mark.parametrize("kind", ["individual", "agency", "admin"])
@pytest.mark.parametrize("verified", [True, False])
def test_listed_capabilities_match_what_can_grants(db, kind, verified):
    view = make(f"{kind}{verified}@example.com", kind=kind, verified=verified)
    actor = Actor.admin(view.id) if kind == "admin" else Actor.user(view.id)
    granted = [c.value for c in Capability if identity_policy.can(actor, c)]
    assert list(selectors.get_actor_summary(view.id).capabilities) == granted


def test_suspended_individual_capabilities(db):
    view = make()
    services.suspend_account(ADMIN, view.id, "policy breach")
    assert selectors.get_actor_summary(view.id).capabilities == (
        "sign_in",
        "browse",
        "view_drafts",
    )


# --- privacy -------------------------------------------------------------------------------------


def test_no_log_record_carries_password_email_token_or_hash(db, caplog):
    caplog.set_level(logging.DEBUG)
    make("canary.person@example.com")
    result = services.sign_in(VISITOR, "canary.person@example.com", PASSWORD, NET)
    code_of(sign_in, "canary.person@example.com", "canary-wrong-pass")
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    for canary in (
        "canary.person",
        "canary-wrong-pass",
        PASSWORD,
        result.token,
        hashlib.sha256(result.token.encode()).hexdigest(),
    ):
        assert canary not in logged


def test_ratelimit_key_is_a_hmac_not_the_email(db):
    make()
    fail(1)
    key = RateLimitCounter.objects.get(scope="sign_in").key_hash
    assert key == ratelimit.derive_key("sign_in", EMAIL.lower(), NET)
    assert "jane" not in key and NET not in key


# --- hardening: races, stale state, cap, hashing paths ----------------------------------------


def _during_check(monkeypatch, side_effect):
    """Run `side_effect` inside the password check, after the lookup and before the lock."""
    real = sessions_auth.check_password

    def check(*args):
        side_effect()
        return real(*args)

    monkeypatch.setattr(sessions_auth, "check_password", check)


def test_account_deactivated_between_lookup_and_lock_fails_and_counts(db, monkeypatch):
    view = make()
    _during_check(
        monkeypatch,
        lambda: Account.objects.filter(id=view.id).update(deactivated_at=timezone.now()),
    )
    assert code_of(sign_in) == "invalid_credentials"
    assert Session.objects.count() == 0
    assert RateLimitCounter.objects.get(scope="sign_in").count == 1  # the failure was committed


def test_password_changed_between_check_and_lock_is_a_failed_sign_in(db, monkeypatch):
    view = make()
    from django.contrib.auth.hashers import make_password

    _during_check(
        monkeypatch,
        lambda: Account.objects.filter(id=view.id).update(password=make_password("new-secret-1")),
    )
    assert code_of(sign_in) == "invalid_credentials"
    assert Session.objects.count() == 0
    assert RateLimitCounter.objects.get(scope="sign_in").count == 1


def test_email_changed_between_check_and_lock_is_a_failed_sign_in(db, monkeypatch):
    view = make()
    _during_check(
        monkeypatch,
        lambda: Account.objects.filter(id=view.id).update(email="moved@example.com"),
    )
    assert code_of(sign_in) == "invalid_credentials"
    assert Session.objects.count() == 0


def test_unusable_password_still_runs_one_hash_comparison(db, monkeypatch):
    view = make()
    Account.objects.filter(id=view.id).update(password="!unusable-marker")
    calls = []
    real = sessions_auth.check_password
    monkeypatch.setattr(sessions_auth, "check_password", lambda *a: calls.append(a) or real(*a))
    assert code_of(sign_in) == "invalid_credentials"
    assert len(calls) == 1 and calls[0][1] == passwords.dummy_hash()


def test_dummy_hash_is_warmed_at_start_up():
    assert passwords.dummy_hash.cache_info().currsize == 1


def test_open_session_cap_ends_the_oldest_as_replaced(db, settings, clock):
    settings.SESSION_MAX_OPEN = 3
    make()
    tokens = []
    for _ in range(5):
        tokens.append(sign_in().token)
        clock(seconds=1)
    assert [live(t) is not None for t in tokens] == [False, False, True, True, True]
    ended = Session.objects.filter(ended_at__isnull=False)
    assert ended.count() == 2 and {r.ended_reason for r in ended} == {"replaced"}


def test_cap_is_per_account(db, settings):
    settings.SESSION_MAX_OPEN = 1
    make()
    make("other@example.com")
    mine, theirs = sign_in().token, sign_in("other@example.com").token
    assert live(mine) is not None and live(theirs) is not None


def test_refresh_never_touches_an_expired_session(db, clock):
    make()
    session = live(sign_in().token)
    clock(days=15)
    services.refresh_session(SYSTEM, session.id)
    assert Session.objects.get().last_seen_at == T0


def test_replaced_is_not_an_announced_reason():
    from apps.identity import events as identity_events
    from apps.identity.models import SESSION_END_REASONS

    assert identity_events.SESSION_END_REASONS == set(SESSION_END_REASONS) - {"replaced"}
    with pytest.raises(ValueError):
        emit_session_ended(uuid.uuid4(), "replaced")


def test_session_settings_are_validated_at_load(monkeypatch):
    import importlib

    from django.core.exceptions import ImproperlyConfigured

    import config.settings.base as base

    for name, value in (
        ("SESSION_TOUCH_SECONDS", "0"),
        ("SESSION_IDLE_SECONDS", "100"),  # not above the 300 s touch interval
        ("SESSION_ABSOLUTE_SECONDS", "3600"),  # below the 24 h idle limit
        ("SESSION_MAX_OPEN", "0"),
    ):
        monkeypatch.setenv(name, value)
        with pytest.raises(ImproperlyConfigured):
            importlib.reload(base)
        monkeypatch.delenv(name)
    importlib.reload(base)
