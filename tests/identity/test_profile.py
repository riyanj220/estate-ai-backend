"""Profile and email change at the service level (Story 1.20)."""

import logging
import re
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.core import mail as django_mail
from django.db import connection
from django.utils import timezone

from apps.identity import email_change, services, subscribers, verification
from apps.identity.events import AccountEmailChangeRequested
from apps.identity.models import Account, EmailChangeToken, IndividualProfile
from apps.identity.services import email_changes
from apps.kernel import events, retention, runtime
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord, RateLimitCounter, RateLimitPolicy

SYSTEM = Actor.system("req")
EMAIL = "Jane.Doe@Example.com"
NEW = "New.Address@Example.org"
PHONE = "+923001234567"
PASSWORD = "correct horse battery staple"
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


def make(email=EMAIL, account_type="individual", verified=True):
    view = services.create_account(SYSTEM, email, PASSWORD, account_type)
    if verified:
        services.mark_email_verified(SYSTEM, view.id)
    if account_type == "individual":
        IndividualProfile.objects.create(account_id=view.id, full_name="Jane Doe", phone=PHONE)
    return view


def user(view):
    return Actor.user(view.id, "req")


def change_error(view, current="wrong", new=NEW):
    with pytest.raises(services.FieldsInvalid) as exc:
        services.request_email_change(user(view), new, current)
    return exc.value.fields


def ask(capture, view, new=NEW):
    with capture(execute=True):
        assert services.request_email_change(user(view), new, PASSWORD) is None


def token_to(address):
    message = [m for m in django_mail.outbox if m.to == [address]][0]
    return re.search(r"token=([A-Za-z0-9_-]+)", message.body).group(1)


# --- profile -------------------------------------------------------------------------------------


def test_profile_actor_rules(db):
    view = make()
    with pytest.raises(DomainError) as exc:
        services.get_profile(Actor.visitor())
    assert exc.value.code == "unauthenticated"
    for actor in (Actor.system(), Actor.admin(uuid.uuid4())):
        with pytest.raises(DomainError) as exc:
            services.get_profile(actor)
        assert exc.value.code == "forbidden"
    agency = make("agency@example.com", "agency")
    with pytest.raises(DomainError) as exc:
        services.get_profile(user(agency))
    assert exc.value.code == "forbidden"
    assert services.get_profile(user(view)).phone == PHONE


def test_deactivated_account_has_no_profile(db):
    view = make()
    Account.objects.update(deactivated_at=timezone.now())
    with pytest.raises(DomainError) as exc:
        services.get_profile(user(view))
    assert exc.value.code == "unauthenticated"
    with pytest.raises(DomainError) as exc:
        services.update_profile(user(view), {"full_name": "Janet Doe"})
    assert exc.value.code == "unauthenticated"


def test_update_rejects_unknown_and_empty_changes(db):
    view = make()
    for changes in ({}, {"email": "x@example.com"}, {"full_name": "Janet Doe", "x": 1}, None, []):
        with pytest.raises(DomainError) as exc:
            services.update_profile(user(view), changes)
        assert exc.value.code == "validation_error"


def test_update_failure_keeps_every_field_and_the_audit_clean(db):
    view = make()
    with pytest.raises(services.FieldsInvalid) as exc:
        services.update_profile(user(view), {"full_name": "Janet Doe", "phone": None})
    assert exc.value.fields == {"phone": "required"}
    assert IndividualProfile.objects.get().full_name == "Jane Doe"
    assert not AuditRecord.objects.filter(action="account.profile_updated").exists()


def test_updates_are_serialised_with_the_account_lock(db, monkeypatch):
    view = make()
    seen = []
    real = services.Account.objects.select_for_update

    def spy(*args, **kwargs):
        seen.append(True)
        return real(*args, **kwargs)

    monkeypatch.setattr(services.Account.objects, "select_for_update", spy)
    services.update_profile(user(view), {"full_name": "Janet Doe"})
    assert seen


@pytest.mark.django_db(transaction=True)
def test_parallel_updates_audit_once_per_actual_change():
    runtime.shutdown()
    view = make()
    barrier, errors = threading.Barrier(8), []

    def work():
        try:
            barrier.wait()
            services.update_profile(user(view), {"full_name": "Janet Doe"})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert AuditRecord.objects.filter(action="account.profile_updated").count() == 1


# --- request -------------------------------------------------------------------------------------


def test_request_creates_a_pending_row_with_a_throwaway_hash(
    db, django_capture_on_commit_callbacks, monkeypatch
):
    view = make()
    emitted = []
    monkeypatch.setattr("apps.identity.events.emit", emitted.append)
    services.request_email_change(user(view), f"  {NEW} ", PASSWORD)
    row = EmailChangeToken.objects.get()
    assert row.new_email == NEW and row.used_at is None
    assert row.expires_at - row.created_at == email_change.TOKEN_TTL
    event = [e for e in emitted if isinstance(e, AccountEmailChangeRequested)][0]
    assert (event.account_id, event.change_request_id) == (view.id, row.id)
    assert NEW not in repr(event) and EMAIL not in repr(event)
    assert row.link_issued is False
    assert (
        services.selectors.get_profile(view.id).pending_email == NEW
    )  # shown even before any link


def test_a_row_without_a_link_cannot_be_confirmed(db):
    view = make()
    services.request_email_change(user(view), NEW, PASSWORD)  # handler not run: throwaway hash
    for guess in ("a" * 43, "throwaway"):
        with pytest.raises(DomainError) as exc:
            services.confirm_email_change(Actor.visitor(), guess)
        assert exc.value.code == "link_invalid"
    assert Account.objects.get().email == EMAIL


def test_request_actor_and_type_rules(db):
    view = make()
    for actor, code in (
        (Actor.visitor(), "unauthenticated"),
        (Actor.system(), "forbidden"),
        (Actor.admin(uuid.uuid4()), "forbidden"),
    ):
        with pytest.raises(DomainError) as exc:
            services.request_email_change(actor, NEW, PASSWORD)
        assert exc.value.code == code
    admin = make("admin@example.com", "admin")
    with pytest.raises(DomainError) as exc:
        services.request_email_change(Actor.user(admin.id), NEW, PASSWORD)  # wrong kind, admin row
    assert exc.value.code == "forbidden"
    Account.objects.filter(id=view.id).update(deactivated_at=timezone.now())
    with pytest.raises(DomainError) as exc:
        services.request_email_change(user(view), NEW, PASSWORD)
    assert exc.value.code == "unauthenticated"
    assert EmailChangeToken.objects.count() == 0


def test_throttle_window_with_the_clock(db, clock):
    view = make()
    for _ in range(5):
        assert change_error(view) == {"current_password": "incorrect"}
    assert RateLimitCounter.objects.get(scope="password_change_failed").count == 5
    assert change_error(view, PASSWORD) == {"current_password": "incorrect"}  # right, but capped
    assert EmailChangeToken.objects.count() == 0
    clock(seconds=901)
    services.request_email_change(user(view), NEW, PASSWORD)
    assert EmailChangeToken.objects.count() == 1


def test_the_failure_counter_is_shared_with_password_change(db):
    view = make()
    sign_in = services.sign_in(Actor.visitor(), EMAIL, PASSWORD, "203.0.113.7")
    assert sign_in.token
    for _ in range(5):
        change_error(view)
    from apps.identity.models import Session

    with pytest.raises(services.PasswordInvalid) as exc:
        services.change_password(user(view), Session.objects.get().id, PASSWORD, "mango-river-42x")
    assert exc.value.fields == {"current_password": "incorrect"}


@pytest.mark.django_db(transaction=True)
def test_parallel_wrong_attempts_never_push_the_counter_past_the_cap():
    for scope, kind in (("sign_in", "client"), ("password_change_failed", "account")):
        RateLimitPolicy.objects.update_or_create(
            scope=scope,
            defaults={"limit": 5, "window_seconds": 900, "key_kind": kind, "note": "t"},
        )
    runtime.shutdown()
    view = make()
    outcomes, errors = [], []
    barrier = threading.Barrier(20)

    def worker():
        try:
            barrier.wait()
            try:
                services.request_email_change(user(view), NEW, "wrong")
            except services.FieldsInvalid as exc:
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
    assert change_error(view, PASSWORD) == {"current_password": "incorrect"}


def test_wrong_types_and_oversized_passwords(db):
    view = make()
    for args in ((5, PASSWORD), (NEW, 5), (NEW, None)):
        with pytest.raises(DomainError) as exc:
            services.request_email_change(user(view), *args)
        assert exc.value.code == "validation_error"
    assert change_error(view, "x" * 2000) == {"current_password": "incorrect"}


# --- handler (issue_email_change) ----------------------------------------------------------------


def test_issue_rules(db):
    view = make()
    services.request_email_change(user(view), NEW, PASSWORD)
    row = EmailChangeToken.objects.get()
    with pytest.raises(DomainError):
        services.issue_email_change(Actor.visitor(), view.id, row.id)
    assert services.issue_email_change(SYSTEM, "nope", row.id) is None
    assert services.issue_email_change(SYSTEM, view.id, uuid.uuid4()).kind == "none"
    delivery = services.issue_email_change(SYSTEM, view.id, row.id)
    assert (delivery.kind, delivery.target, delivery.current_email) == ("confirm", NEW, EMAIL)
    assert delivery.token not in repr(delivery) and NEW not in repr(delivery)
    row.refresh_from_db()
    assert row.token_hash == verification.hash_token(delivery.token) and row.link_issued
    assert services.selectors.get_profile(view.id).pending_email == NEW


def test_issue_skips_used_expired_deactivated_and_same_address(db):
    view = make()
    services.request_email_change(user(view), NEW, PASSWORD)
    row = EmailChangeToken.objects.get()
    row.used_at = timezone.now()
    row.save()
    assert services.issue_email_change(SYSTEM, view.id, row.id).kind == "none"
    row.used_at = None
    row.expires_at = timezone.now() - timedelta(seconds=1)
    row.save()
    assert services.issue_email_change(SYSTEM, view.id, row.id).kind == "none"
    row.expires_at = timezone.now() + timedelta(hours=1)
    row.new_email = EMAIL.upper()
    row.save()
    assert services.issue_email_change(SYSTEM, view.id, row.id).kind == "none"
    row.new_email = NEW
    row.save()
    Account.objects.update(deactivated_at=timezone.now())
    assert services.issue_email_change(SYSTEM, view.id, row.id) is None


def test_suspended_owner_of_a_taken_address_is_not_mailed(db, django_capture_on_commit_callbacks):
    view = make()
    other = make("taken@example.com")
    Account.objects.filter(id=other.id).update(suspended_at=timezone.now(), suspension_reason="x")
    ask(django_capture_on_commit_callbacks, view, "taken@example.com")
    assert [m.to for m in django_mail.outbox] == [[EMAIL]]  # only the heads-up


def test_mail_is_capped_per_requesting_account_silently(db, django_capture_on_commit_callbacks):
    view = make()
    for n in range(5):  # five sends from the shared per-account verification cap
        ask(django_capture_on_commit_callbacks, view, f"a{n}@example.org")
    assert len(django_mail.outbox) == 10
    RateLimitCounter.objects.filter(scope="email_change_request").delete()
    ask(django_capture_on_commit_callbacks, view, "b@example.org")
    assert [m.to for m in django_mail.outbox[10:]] == [[EMAIL]]  # capped: heads-up only


def test_mail_is_capped_per_target_address_across_accounts(db, django_capture_on_commit_callbacks):
    views = [make(f"owner{n}@example.com") for n in range(7)]
    for view in views:
        ask(django_capture_on_commit_callbacks, view, NEW)
    to_target = [m for m in django_mail.outbox if m.to == [NEW]]
    assert len(to_target) == 5  # a sixth account cannot mail-bomb the same address
    assert len([m for m in django_mail.outbox if m.to != [NEW]]) == 7  # every heads-up still sent


def test_handler_failure_is_logged_by_class_and_rolls_back_the_link(
    db, django_capture_on_commit_callbacks, monkeypatch, caplog
):
    view = make()
    services.request_email_change(user(view), NEW, PASSWORD)  # no handler yet
    before = EmailChangeToken.objects.get()
    original_hash = before.token_hash
    monkeypatch.setattr(
        "apps.identity.notices.send_email_change_confirmation",
        lambda *a: (_ for _ in ()).throw(OSError(NEW)),
    )
    caplog.set_level(logging.DEBUG)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        events._dispatch(
            subscribers.send_email_change_mail,
            AccountEmailChangeRequested(account_id=view.id, change_request_id=before.id),
        )
    assert callbacks == []
    assert django_mail.outbox == []
    row = EmailChangeToken.objects.get()
    assert (row.token_hash, row.link_issued, row.used_at) == (original_hash, False, None)
    assert services.selectors.get_profile(view.id).pending_email == NEW  # same as any branch
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    assert NEW not in logged


def test_a_failed_handler_run_leaves_the_pending_link_alone(
    db, django_capture_on_commit_callbacks, monkeypatch
):
    view = make()
    ask(django_capture_on_commit_callbacks, view)
    token = token_to(NEW)
    django_mail.outbox.clear()
    monkeypatch.setattr(
        "apps.identity.notices.send_email_change_confirmation",
        lambda *a: (_ for _ in ()).throw(OSError("x")),
    )
    ask(django_capture_on_commit_callbacks, view, "second@example.org")
    assert services.selectors.get_profile(view.id).pending_email == "second@example.org"
    services.confirm_email_change(Actor.visitor(), token)  # the older issued link still works
    assert Account.objects.get().email == NEW


def test_a_capped_request_leaves_the_pending_link_alone(db, django_capture_on_commit_callbacks):
    view = make()
    ask(django_capture_on_commit_callbacks, view)
    for _ in range(5):
        services.ratelimit.hit(
            "verification_email", services.ratelimit.derive_key("account", str(view.id))
        )
    ask(django_capture_on_commit_callbacks, view, "second@example.org")
    assert services.selectors.get_profile(view.id).pending_email == "second@example.org"
    services.confirm_email_change(Actor.visitor(), token_to(NEW))
    assert Account.objects.get().email == NEW


def test_request_query_shape_is_the_same_for_free_taken_and_same_addresses(db):
    from django.db import connection as conn
    from django.test.utils import CaptureQueriesContext

    view = make()
    make("taken@example.com")

    def shape(address):
        with CaptureQueriesContext(conn) as ctx:
            services.request_email_change(user(view), address, PASSWORD)
        return [re.sub(r"'[^']*'|\b\d+\b|\"s\w+\"", "?", q["sql"]) for q in ctx.captured_queries]

    free = shape("free@example.org")
    assert shape("taken@example.com") == free
    assert shape(EMAIL.upper()) == free
    assert not any("identity_account" in q and "lower" in q.lower() for q in free)


# --- confirm -------------------------------------------------------------------------------------


def test_confirm_locks_account_then_token(db, monkeypatch):
    view = make()
    services.request_email_change(user(view), NEW, PASSWORD)
    delivery = services.issue_email_change(SYSTEM, view.id, EmailChangeToken.objects.get().id)
    order = []
    for model in (services.Account, services.EmailChangeToken):
        manager = model.objects
        real = manager.select_for_update

        def spy(*a, _real=real, _name=model.__name__, **k):
            order.append(_name)
            return _real(*a, **k)

        monkeypatch.setattr(manager, "select_for_update", spy)
    services.confirm_email_change(Actor.visitor(), delivery.token)
    assert order == ["Account", "EmailChangeToken"]


def test_race_on_the_unique_index_is_link_invalid_not_a_500(db, monkeypatch):
    view = make()
    services.request_email_change(user(view), NEW, PASSWORD)
    delivery = services.issue_email_change(SYSTEM, view.id, EmailChangeToken.objects.get().id)
    make(NEW.lower())  # registered between the check and the write
    monkeypatch.setattr(email_changes, "_email_taken", lambda email: False)
    with pytest.raises(DomainError) as exc:
        services.confirm_email_change(Actor.visitor(), delivery.token)
    assert exc.value.code == "link_invalid"
    assert Account.objects.get(id=view.id).email == EMAIL
    assert EmailChangeToken.objects.get(account_id=view.id).used_at is None


def test_confirm_audit_failure_rolls_everything_back(db, monkeypatch):
    view = make()
    services.request_email_change(user(view), NEW, PASSWORD)
    delivery = services.issue_email_change(SYSTEM, view.id, EmailChangeToken.objects.get().id)

    def boom(*a, **k):
        raise RuntimeError("audit down")

    monkeypatch.setattr("apps.kernel.audit.record", boom)
    with pytest.raises(RuntimeError):
        services.confirm_email_change(Actor.visitor(), delivery.token)
    assert Account.objects.get().email == EMAIL
    assert EmailChangeToken.objects.get().used_at is None


# --- retention -----------------------------------------------------------------------------------


def test_retention_purges_old_tokens_and_dry_run_counts(db):
    view = make()
    now = timezone.now()
    old = now - timedelta(days=8)
    for n, (used, expires) in enumerate(
        [(old, now), (None, old), (None, now + timedelta(hours=1)), (now, now)]
    ):
        EmailChangeToken.objects.create(
            account_id=view.id,
            new_email=NEW,
            token_hash=f"{n:064d}",
            created_at=old,
            used_at=used,
            expires_at=expires,
        )
    assert "identity.email_change_tokens" in retention.registered()
    assert email_change.purge_expired(now, dry_run=True) == 2
    assert EmailChangeToken.objects.count() == 4
    assert email_change.purge_expired(now, dry_run=False) == 2
    assert EmailChangeToken.objects.count() == 2


def test_audit_actions_are_registered_without_metadata():
    from apps.kernel import audit

    actions = audit.registered_actions()
    assert actions["account.profile_updated"] == {}
    assert actions["account.email_changed"] == {}


def test_confirm_supersedes_other_issued_links_and_ignores_unissued_rows(db):
    view = make()
    now = timezone.now()
    rows = {}
    for name, issued in (("first", True), ("second", True), ("unissued", False)):
        rows[name] = EmailChangeToken.objects.create(
            account_id=view.id,
            new_email=f"{name}@example.org",
            token_hash=verification.hash_token(f"token-{name}"),
            created_at=now,
            expires_at=now + timedelta(hours=1),
            link_issued=issued,
        )
    with pytest.raises(DomainError) as exc:
        services.confirm_email_change(Actor.visitor(), "token-unissued")
    assert exc.value.code == "link_invalid"
    services.confirm_email_change(Actor.visitor(), "token-first")
    assert Account.objects.get().email == "first@example.org"
    assert not EmailChangeToken.objects.filter(used_at__isnull=True).exists()
    with pytest.raises(DomainError) as exc:
        services.confirm_email_change(Actor.visitor(), "token-second")
    assert exc.value.code == "link_used"


def test_rolled_back_confirm_sends_no_old_address_notice(
    db, django_capture_on_commit_callbacks, monkeypatch
):
    view = make()
    services.request_email_change(user(view), NEW, PASSWORD)
    delivery = services.issue_email_change(SYSTEM, view.id, EmailChangeToken.objects.get().id)
    monkeypatch.setattr(
        "apps.kernel.audit.record", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError):
            services.confirm_email_change(Actor.visitor(), delivery.token)
    assert django_mail.outbox == []


def test_confirm_works_for_a_suspended_account_and_keeps_it_suspended(
    db, django_capture_on_commit_callbacks
):
    view = make()
    ask(django_capture_on_commit_callbacks, view)
    Account.objects.update(suspended_at=timezone.now(), suspension_reason="x")
    services.confirm_email_change(Actor.visitor(), token_to(NEW))
    account = Account.objects.get()
    assert account.email == NEW and account.suspended_at is not None


def _issued(view):
    services.request_email_change(user(view), NEW, PASSWORD)
    return services.issue_email_change(SYSTEM, view.id, EmailChangeToken.objects.get().id).token


def test_password_change_voids_outstanding_email_change_links(db):
    view = make()
    token = _issued(view)
    services.sign_in(Actor.visitor(), EMAIL, PASSWORD, "203.0.113.7")
    from apps.identity.models import Session

    services.change_password(user(view), Session.objects.get().id, PASSWORD, "mango-river-42x")
    with pytest.raises(DomainError) as exc:
        services.confirm_email_change(Actor.visitor(), token)
    assert exc.value.code == "link_used"
    assert Account.objects.get().email == EMAIL


def test_password_reset_voids_outstanding_email_change_links(db):
    from apps.identity.models import PasswordResetToken

    view = make()
    token = _issued(view)
    now = timezone.now()
    PasswordResetToken.objects.create(
        account_id=view.id,
        token_hash=verification.hash_token("reset-token-x"),
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    services.confirm_password_reset(Actor.visitor(), "reset-token-x", "mango-river-42x")
    with pytest.raises(DomainError) as exc:
        services.confirm_email_change(Actor.visitor(), token)
    assert exc.value.code == "link_used"


def test_deactivation_voids_outstanding_email_change_links(db):
    view = make()
    _issued(view)
    admin = services.create_account(SYSTEM, "admin@example.com", PASSWORD, "admin")
    services.deactivate(Actor.admin(admin.id), view.id)
    assert not EmailChangeToken.objects.filter(used_at__isnull=True).exists()
