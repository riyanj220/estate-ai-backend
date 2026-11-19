import logging
import threading
import uuid

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.identity import events as identity_events
from apps.identity import selectors, services
from apps.identity.events import AccountSessionEnded, emit_session_ended
from apps.identity.models import Account
from apps.identity.state import AccountState as S
from apps.kernel import events
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord

SYSTEM = Actor.system("req-1")
ADMIN = Actor.admin(uuid.UUID("33333333-3333-4333-8333-333333333333"), "req-2")
EMAIL = "Jane.Doe@Example.com"
PASSWORD = "correct horse battery staple"


def create(email=EMAIL, kind="individual"):
    return services.create_account(SYSTEM, email, PASSWORD, kind)


@pytest.fixture
def received():
    seen = []
    events.subscribe(AccountSessionEnded, seen.append)
    yield seen
    events.clear()


def codes(call, *args):
    with pytest.raises(DomainError) as exc:
        call(*args)
    return exc.value.code


# --- create ---------------------------------------------------------------------------------


def test_create_stores_hashed_password_unverified_and_audits(db):
    view = create()
    row = Account.objects.get(id=view.id)
    assert row.email == EMAIL
    assert row.password != PASSWORD and "$" in row.password
    assert (row.email_verified_at, row.suspended_at, row.deactivated_at) == (None, None, None)
    assert (view.type, view.state, view.suspension_reason) == ("individual", S.EMAIL_UNVERIFIED, "")
    audit = AuditRecord.objects.get(action="account.created")
    assert (audit.target_type, audit.target_id) == ("account", view.id)
    assert audit.metadata == {"account_type": "individual"}
    assert audit.actor_kind == "system"


def test_email_is_trimmed(db):
    create("  a@b.co  ")
    assert Account.objects.get().email == "a@b.co"


@pytest.mark.parametrize("email", ["JANE.DOE@example.COM", " jane.doe@example.com "])
def test_duplicate_email_in_any_case_is_refused_without_writes(db, email):
    create()
    assert codes(create, email) == "validation_error"
    assert Account.objects.count() == 1
    assert AuditRecord.objects.count() == 1


BAD_EMAILS = [
    "", "nope", "a@b", "a b@c.de", "@c.de", "a@@c.de", None, 5,
    "a\x00@b.co", "a@b.co\x07", "a\u200b@b.co", "a@b..c", "a@.b.co", "a@b.co.",
]  # fmt: skip


@pytest.mark.parametrize("email", BAD_EMAILS)
def test_bad_email_is_refused(db, email):
    assert codes(create, email) == "validation_error"
    assert not Account.objects.exists()


@pytest.mark.parametrize("password", ["", "   ", "\t\n", None, 5, "x" * 1025])
def test_bad_password_is_refused(db, password):
    assert codes(services.create_account, SYSTEM, EMAIL, password, "individual") == (
        "validation_error"
    )


def test_bad_account_type_is_refused(db):
    assert codes(create, EMAIL, "root") == "validation_error"


@pytest.mark.parametrize("kind", ["individual", "agency", "admin"])
def test_all_account_types_can_be_created(db, kind):
    assert create(kind=kind).type == kind


def test_database_enforces_case_insensitive_uniqueness(db):
    create()
    with pytest.raises(IntegrityError), transaction.atomic():
        Account.objects.create(email=EMAIL.upper(), password="x", account_type="individual")


def test_race_on_unique_constraint_is_a_validation_error(db, monkeypatch):
    create()
    monkeypatch.setattr(services, "_email_taken", lambda email: False)
    with transaction.atomic():
        assert codes(create, EMAIL.lower()) == "validation_error"
        assert Account.objects.count() == 1


@pytest.mark.parametrize(
    ("actor", "code"),
    [
        (Actor.visitor(), "unauthenticated"),
        (Actor.user(uuid.uuid4()), "not_found"),
        (ADMIN, "not_found"),
    ],
)
def test_only_system_creates_and_verifies(db, actor, code):
    assert codes(services.create_account, actor, EMAIL, PASSWORD, "individual") == code
    assert codes(services.mark_email_verified, actor, uuid.uuid4()) == code
    assert not Account.objects.exists() and not AuditRecord.objects.exists()


# --- verify ---------------------------------------------------------------------------------


def test_verify_is_idempotent_with_one_audit_record(db):
    view = create()
    assert services.mark_email_verified(SYSTEM, view.id).state is S.ACTIVE
    first = Account.objects.get(id=view.id).email_verified_at
    assert services.mark_email_verified(SYSTEM, view.id).state is S.ACTIVE
    assert Account.objects.get(id=view.id).email_verified_at == first
    assert AuditRecord.objects.filter(action="account.email_verified").count() == 1


def test_verify_unknown_account_is_not_found(db):
    assert codes(services.mark_email_verified, SYSTEM, uuid.uuid4()) == "not_found"
    assert codes(services.mark_email_verified, SYSTEM, "nope") == "not_found"


# --- suspend and reactivate -----------------------------------------------------------------


def test_suspend_stores_reason_audits_without_it_and_emits_nothing(db, received):
    view = create()
    result = services.suspend(ADMIN, view.id, "  Repeated policy breaches  ")
    assert (result.state, result.suspension_reason) == (S.SUSPENDED, "Repeated policy breaches")
    audit = AuditRecord.objects.get(action="account.suspended")
    assert audit.metadata == {} and audit.reason == ""
    assert audit.actor_id == ADMIN.account_id
    assert received == []


@pytest.mark.parametrize("reason", ["", "   ", "x" * 301, "mail me@x.com", "call 0300123456", None])
def test_bad_reason_is_refused_without_writes(db, reason):
    view = create()
    assert codes(services.suspend, ADMIN, view.id, reason) == "validation_error"
    assert Account.objects.get(id=view.id).suspended_at is None
    assert not AuditRecord.objects.filter(action="account.suspended").exists()


def test_reason_limits_are_inclusive(db):
    view = create()
    assert services.suspend(ADMIN, view.id, "x" * 300).state is S.SUSPENDED


def test_digit_runs_below_seven_are_fine(db):
    view = create()
    assert services.suspend(ADMIN, view.id, "Case 123456 breach").state is S.SUSPENDED


def test_suspending_twice_is_refused(db):
    view = create()
    services.suspend(ADMIN, view.id, "one")
    assert codes(services.suspend, ADMIN, view.id, "two") == "validation_error"
    assert selectors.get_account(view.id).suspension_reason == "one"


def test_reactivate_verified_returns_to_active(db):
    view = create()
    services.mark_email_verified(SYSTEM, view.id)
    services.suspend(ADMIN, view.id, "r")
    result = services.reactivate(ADMIN, view.id)
    assert (result.state, result.suspension_reason) == (S.ACTIVE, "")
    assert AuditRecord.objects.filter(action="account.reactivated").count() == 1


def test_reactivate_unverified_returns_to_email_unverified(db):
    view = create()
    services.suspend(ADMIN, view.id, "r")
    assert services.reactivate(ADMIN, view.id).state is S.EMAIL_UNVERIFIED


def test_reactivating_a_non_suspended_account_is_refused(db):
    view = create()
    assert codes(services.reactivate, ADMIN, view.id) == "validation_error"
    assert not AuditRecord.objects.filter(action="account.reactivated").exists()


def test_verification_while_suspended_is_kept(db):
    view = create()
    services.suspend(ADMIN, view.id, "r")
    assert services.mark_email_verified(SYSTEM, view.id).state is S.SUSPENDED
    assert services.reactivate(ADMIN, view.id).state is S.ACTIVE


@pytest.mark.parametrize("call_args", [("suspend", "r"), ("reactivate",), ("deactivate",)])
@pytest.mark.parametrize(
    ("actor", "code"),
    [
        (Actor.visitor(), "unauthenticated"),
        (Actor.user(uuid.uuid4()), "not_found"),
        (Actor.system(), "not_found"),
    ],
)
def test_non_admins_cannot_change_state(db, actor, code, call_args):
    view = create()
    name, *rest = call_args
    assert codes(getattr(services, name), actor, view.id, *rest) == code
    assert AuditRecord.objects.count() == 1  # only account.created
    row = Account.objects.get(id=view.id)
    assert (row.suspended_at, row.deactivated_at) == (None, None)


@pytest.mark.parametrize("call_args", [("suspend", "r"), ("reactivate",), ("deactivate",)])
def test_unknown_account_is_not_found(db, call_args):
    name, *rest = call_args
    assert codes(getattr(services, name), ADMIN, uuid.uuid4(), *rest) == "not_found"
    assert codes(getattr(services, name), ADMIN, "nope", *rest) == "not_found"


# --- deactivate -----------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_deactivate_audits_and_emits_after_commit(received):
    view = create()
    result = services.deactivate(ADMIN, view.id)
    assert result.state is S.DEACTIVATED
    assert AuditRecord.objects.filter(action="account.deactivated", target_id=view.id).exists()
    assert received == [AccountSessionEnded(account_id=view.id, reason="deactivated")]


def test_deactivate_event_waits_for_commit(db, received):
    view = create()
    with transaction.atomic():
        services.deactivate(ADMIN, view.id)
        assert received == []


@pytest.mark.django_db(transaction=True)
def test_rolled_back_deactivation_emits_nothing(received):
    view = create()
    with pytest.raises(RuntimeError), transaction.atomic():
        services.deactivate(ADMIN, view.id)
        raise RuntimeError
    assert received == []
    assert selectors.get_account(view.id).state is S.EMAIL_UNVERIFIED
    assert not AuditRecord.objects.filter(action="account.deactivated").exists()


@pytest.mark.parametrize("call_args", [("suspend", "r"), ("reactivate",), ("deactivate",)])
def test_deactivated_account_cannot_be_changed_again(db, call_args):
    view = create()
    services.deactivate(ADMIN, view.id)
    name, *rest = call_args
    assert codes(getattr(services, name), ADMIN, view.id, *rest) == "not_found"
    assert codes(services.mark_email_verified, SYSTEM, view.id) == "not_found"
    assert AuditRecord.objects.filter(action="account.deactivated").count() == 1


def test_deactivating_a_suspended_account_wins(db):
    view = create()
    services.suspend(ADMIN, view.id, "r")
    assert services.deactivate(ADMIN, view.id).state is S.DEACTIVATED


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("name", ["create", "verify", "suspend", "reactivate", "deactivate"])
def test_failure_after_audit_write_rolls_back_account_and_audit(monkeypatch, name):
    view = create()
    if name != "verify":
        services.mark_email_verified(SYSTEM, view.id)
    if name == "reactivate":
        services.suspend(ADMIN, view.id, "r")
    before_rows = list(Account.objects.values())
    before_audit = AuditRecord.objects.count()
    original = services.audit.record

    def record_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError

    monkeypatch.setattr(services.audit, "record", record_then_fail)
    calls = {
        "create": lambda: services.create_account(SYSTEM, "new@example.com", PASSWORD, "agency"),
        "verify": lambda: services.mark_email_verified(SYSTEM, view.id),
        "suspend": lambda: services.suspend(ADMIN, view.id, "r"),
        "reactivate": lambda: services.reactivate(ADMIN, view.id),
        "deactivate": lambda: services.deactivate(ADMIN, view.id),
    }
    with pytest.raises(RuntimeError):
        calls[name]()
    assert list(Account.objects.values()) == before_rows
    assert AuditRecord.objects.count() == before_audit


# --- privacy --------------------------------------------------------------------------------


def test_no_email_hash_or_reason_in_audit_or_logs(db, caplog):
    caplog.set_level(logging.DEBUG)
    view = create("canary.person@example.com")
    services.mark_email_verified(SYSTEM, view.id)
    services.suspend(ADMIN, view.id, "canary-reason-text")
    services.reactivate(ADMIN, view.id)
    services.deactivate(ADMIN, view.id)
    stored = Account.objects.get(id=view.id)
    dump = " ".join(
        f"{r.actor_kind}{r.action}{r.target_type}{r.reason}{r.request_id}{r.metadata}"
        for r in AuditRecord.objects.all()
    )
    dump += " " + caplog.text
    for secret in ("canary.person", "example.com", "canary-reason", stored.password, PASSWORD):
        assert secret not in dump
    assert AuditRecord.objects.count() == 5


# --- selectors and primitives ---------------------------------------------------------------


def test_views_are_immutable_and_hide_email_and_password(db):
    view = create()
    assert set(view.__dataclass_fields__) == {"id", "type", "state", "suspension_reason"}
    with pytest.raises(AttributeError):
        view.state = S.ACTIVE
    assert not isinstance(view, Account)


def test_get_account_handles_unknown_and_malformed_ids(db):
    for bad in (uuid.uuid4(), "nope", None, 5, ""):
        assert selectors.get_account(bad) is None
        assert selectors.get_signed_in_account(bad) is None


def test_suspended_shows_reason_and_may_be_signed_in(db):
    view = create()
    services.suspend(ADMIN, view.id, "Policy breach")
    got = selectors.get_signed_in_account(str(view.id))
    assert (got.state, got.suspension_reason) == (S.SUSPENDED, "Policy breach")


def test_deactivated_is_never_signed_in_but_admin_view_still_sees_it(db):
    view = create()
    services.deactivate(ADMIN, view.id)
    assert selectors.get_signed_in_account(view.id) is None
    assert selectors.get_account(view.id).state is S.DEACTIVATED


@pytest.mark.parametrize(
    ("kind", "verify", "suspend", "deactivate", "expected"),
    [
        ("individual", True, False, False, True),
        ("agency", True, False, False, True),
        ("admin", True, False, False, False),
        ("individual", False, False, False, False),
        ("individual", True, True, False, False),
        ("individual", True, False, True, False),
    ],
)
def test_is_active_owner(db, kind, verify, suspend, deactivate, expected):
    view = create(kind=kind)
    if verify:
        services.mark_email_verified(SYSTEM, view.id)
    if suspend:
        services.suspend(ADMIN, view.id, "r")
    if deactivate:
        services.deactivate(ADMIN, view.id)
    assert selectors.is_active_owner(view.id) is expected


def test_is_active_owner_unknown_account(db):
    assert selectors.is_active_owner(uuid.uuid4()) is False
    assert selectors.is_active_owner("nope") is False


@pytest.mark.django_db(transaction=True)
def test_lock_account_outside_a_transaction_is_refused():
    view = create()
    assert not connection.in_atomic_block
    with pytest.raises(RuntimeError):
        selectors.lock_account(view.id)


def test_lock_account_returns_a_snapshot(db):
    view = create(kind="agency")
    with transaction.atomic():
        snap = selectors.lock_account(view.id)
        assert (snap.id, snap.type, snap.state) == (view.id, "agency", S.EMAIL_UNVERIFIED)
        with pytest.raises(AttributeError):
            snap.state = S.ACTIVE
        with pytest.raises(DomainError):
            selectors.lock_account(uuid.uuid4())


@pytest.mark.django_db(transaction=True)
def test_second_transaction_waits_for_the_first_lock():
    view = create()
    holding, release, second_done = threading.Event(), threading.Event(), threading.Event()
    errors = []

    def first():
        try:
            with transaction.atomic():
                selectors.lock_account(view.id)
                holding.set()
                release.wait(10)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            holding.set()
            connection.close()

    def second():
        try:
            with transaction.atomic():
                selectors.lock_account(view.id)
            second_done.set()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            connection.close()

    t1 = threading.Thread(target=first)
    t1.start()
    assert holding.wait(10)
    t2 = threading.Thread(target=second)
    t2.start()
    assert not second_done.wait(1.0)  # still blocked while the first holds the lock
    release.set()
    t1.join(10)
    assert second_done.wait(10)
    t2.join(10)
    assert errors == []


# --- events ---------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("reason", ["sign_out", "password_changed", "deactivated"])
def test_session_ended_helper_emits_for_each_reason(received, reason):
    account_id = uuid.uuid4()
    with transaction.atomic():
        emit_session_ended(account_id, reason)
    assert received == [AccountSessionEnded(account_id=account_id, reason=reason)]


def test_session_ended_event_shape_and_validation():
    assert AccountSessionEnded.name == "account.session_ended"
    with pytest.raises(ValueError):
        emit_session_ended(uuid.uuid4(), "expired")
    with pytest.raises(ValueError):
        emit_session_ended("not-a-uuid", "sign_out")
    assert identity_events.SESSION_END_REASONS == {"sign_out", "password_changed", "deactivated"}


# --- constraints and registration -----------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"suspended_at": timezone.now(), "suspension_reason": ""},
        {"suspended_at": None, "suspension_reason": "left over"},
    ],
)
def test_database_ties_reason_to_suspension(db, fields):
    with pytest.raises(IntegrityError), transaction.atomic():
        Account.objects.create(email="a@b.co", password="x", account_type="individual", **fields)


def test_audit_actions_are_registered_with_enumerated_metadata():
    from apps.kernel import audit

    registered = audit.registered_actions()
    for action in (
        "account.created",
        "account.email_verified",
        "account.suspended",
        "account.reactivated",
        "account.deactivated",
    ):
        assert action in registered
    assert registered["account.created"] == {
        "account_type": frozenset({"individual", "agency", "admin"})
    }
    assert all(
        not registered[a] for a in registered if a.startswith("account.") and a != "account.created"
    )


def test_password_of_exactly_1024_characters_is_accepted(db):
    assert services.create_account(SYSTEM, EMAIL, "x" * 1024, "individual")


def test_other_integrity_errors_are_not_reported_as_duplicate_email(db, monkeypatch):
    def boom(**kwargs):
        raise IntegrityError("some other constraint")

    monkeypatch.setattr(services.Account.objects, "create", boom)
    with pytest.raises(IntegrityError):
        create()


@pytest.mark.parametrize(
    "reason", ["1 2 3 4 5 6 7", "12-34-567", "\u0661\u0662\u0663\u0664\u0665\u0666\u0667"]
)
def test_reason_with_seven_digits_in_total_is_refused(db, reason):
    view = create()
    assert codes(services.suspend, ADMIN, view.id, reason) == "validation_error"


@pytest.mark.parametrize(
    "reason",
    [
        "ping @bob",  # bare handle
        "call me at x@y",  # dotless address
        "jane@gmail",  # dotless address
        "1/2/3/4/5/6/7",  # slash-separated digits
        "123 abc 4567 890",  # "4567 890" is seven digits grouped by a space (phone rule)
    ],
)
def test_reason_rules_kept_from_the_interim_check_are_refused(db, reason):
    view = create()
    assert codes(services.suspend, ADMIN, view.id, reason) == "validation_error"


@pytest.mark.parametrize("email", ["user@localhost", "a,b@c.de", 'a"b@c.de'])
def test_shared_email_shape_refuses_dotless_domains_and_special_characters(db, email):
    assert codes(create, email) == "validation_error"
    assert not Account.objects.exists()


def test_view_raises_runtime_error_when_row_is_missing(db):
    with pytest.raises(RuntimeError):
        services._view(uuid.uuid4())


# --- administrators are not managed here ---------------------------------------------------


@pytest.mark.parametrize("call_args", [("suspend", "r"), ("reactivate",), ("deactivate",)])
def test_admin_accounts_and_self_cannot_be_managed(db, call_args):
    name, *rest = call_args
    other_admin = create("admin2@example.com", "admin")
    own = create("admin1@example.com", "admin")
    individual = create()
    for target, actor in (
        (other_admin.id, ADMIN),
        (own.id, Actor.admin(own.id)),
        (individual.id, Actor.admin(individual.id)),
    ):
        assert codes(getattr(services, name), actor, target, *rest) == "validation_error"
    assert not AuditRecord.objects.exclude(action="account.created").exists()
    assert not Account.objects.exclude(suspended_at=None, deactivated_at=None).exists()


# --- ids and failures after the event -------------------------------------------------------


@pytest.mark.parametrize("form", ["hex", "braces", "urn", "upper"])
def test_non_canonical_uuid_forms_are_not_found(db, form):
    view = create()
    forms = {
        "hex": view.id.hex,
        "braces": f"{{{view.id}}}",
        "urn": view.id.urn,
        "upper": str(view.id).upper(),
    }
    assert selectors.get_account(forms[form]) is None
    assert selectors.is_active_owner(forms[form]) is False
    assert codes(services.mark_email_verified, SYSTEM, forms[form]) == "not_found"
    assert selectors.get_account(str(view.id)) is not None


@pytest.mark.django_db(transaction=True)
def test_failure_after_event_emission_rolls_back_and_dispatches_nothing(received, monkeypatch):
    view = create()
    original = services.emit_session_ended

    def emit_then_fail(*args):
        original(*args)
        raise RuntimeError

    monkeypatch.setattr(services, "emit_session_ended", emit_then_fail)
    with pytest.raises(RuntimeError):
        services.deactivate(ADMIN, view.id)
    assert received == []
    assert selectors.get_account(view.id).state is S.EMAIL_UNVERIFIED
    assert not AuditRecord.objects.filter(action="account.deactivated").exists()
