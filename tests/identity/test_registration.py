import logging
import uuid

import pytest
from django.contrib.auth.hashers import check_password
from django.core import mail as django_mail
from django.db import transaction

from apps.identity import passwords, services, validation
from apps.identity.events import AccountRegistered
from apps.identity.models import Account, IndividualProfile
from apps.identity.state import AccountState
from apps.kernel import events, runtime
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError, field_message
from apps.kernel.models import AuditRecord

VISITOR = Actor.visitor("req-1")
NAME = "Ayesha Khan"
EMAIL = "Ayesha.Khan@Example.com"
PASSWORD = "mango-river-lantern-42"
PHONE = "0300-1234567"
E164 = "+923001234567"


@pytest.fixture(autouse=True)
def fresh_events(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    runtime.shutdown()
    yield
    runtime.shutdown()


@pytest.fixture
def registered():
    seen = []
    events.subscribe(AccountRegistered, seen.append)
    return seen


def register(email=EMAIL, name=NAME, password=PASSWORD, phone=PHONE, actor=VISITOR):
    services.register_individual(actor, name, email, password, phone)


def invalid(**kwargs):
    with pytest.raises(services.RegistrationInvalid) as exc:
        register(**kwargs)
    return exc.value.fields


# --- success ---------------------------------------------------------------------------------


def test_new_registration_creates_unverified_account_profile_and_audit(
    db, registered, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        assert register() is None
    account = Account.objects.get()
    assert account.email == EMAIL  # case kept as entered
    assert account.account_type == "individual"
    assert account.state is AccountState.EMAIL_UNVERIFIED
    assert check_password(PASSWORD, account.password) and account.password != PASSWORD
    profile = IndividualProfile.objects.get()
    assert (profile.account_id, profile.full_name, profile.phone) == (account.id, NAME, E164)
    record = AuditRecord.objects.get(action="account.created")
    assert (record.target_id, record.actor_kind, record.metadata) == (
        account.id,
        "visitor",
        {"account_type": "individual"},
    )
    assert registered == [AccountRegistered(account_id=account.id)]
    assert django_mail.outbox == []  # the verification message is Story 1.16's


def test_name_is_normalised(db):
    register(name="  Zoë   O’Neil-Smith  Jr.  ")
    assert IndividualProfile.objects.get().full_name == "Zoë O’Neil-Smith Jr."


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("0300-1234567", E164),
        ("0300 1234567", E164),
        ("03001234567", E164),
        ("+92 300 1234567", E164),
        ("0092 300 1234567", E164),
        ("+44 20 7946 0958", "+442079460958"),
    ],
)
def test_phone_shapes_are_stored_as_e164(db, raw, stored):
    register(phone=raw)
    assert IndividualProfile.objects.get().phone == stored


def test_two_accounts_may_share_a_phone_and_nothing_is_reported(db):
    register(email="one@example.com")
    register(email="two@example.com")
    assert IndividualProfile.objects.filter(phone=E164).count() == 2


def verify(email=EMAIL):
    services.mark_email_verified(Actor.system(), Account.objects.get(email=email).id)


# --- existing email --------------------------------------------------------------------------


def test_existing_email_creates_nothing_and_queues_a_notice(
    db, registered, django_capture_on_commit_callbacks, settings
):
    register()
    verify()
    before = (
        Account.objects.count(),
        IndividualProfile.objects.count(),
        AuditRecord.objects.count(),
    )
    registered.clear()
    django_mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        assert register(email=EMAIL.upper(), name="Someone Else", phone="0333-7654321") is None
    after = (
        Account.objects.count(),
        IndividualProfile.objects.count(),
        AuditRecord.objects.count(),
    )
    assert before == after
    assert registered == []
    assert [m.to for m in django_mail.outbox] == [[EMAIL]]  # the stored address
    notice = django_mail.outbox[0]
    assert settings.FRONTEND_BASE_URL.rstrip("/") + "/sign-in" in notice.body
    assert notice.alternatives == []
    for private in (PASSWORD, "Someone Else", "7654321", NAME, E164):
        assert private not in notice.body and private not in notice.subject


def test_unverified_existing_account_gets_a_verification_request_not_a_notice(
    db, django_capture_on_commit_callbacks
):
    from apps.identity.events import AccountVerificationRequested

    seen = []
    events.subscribe(AccountVerificationRequested, seen.append)
    register()
    with django_capture_on_commit_callbacks(execute=True):
        register()
    assert seen == [AccountVerificationRequested(account_id=Account.objects.get().id)]
    assert django_mail.outbox == []


def test_verified_account_gets_the_notice_and_no_verification_request(
    db, django_capture_on_commit_callbacks
):
    from apps.identity.events import AccountVerificationRequested

    seen = []
    events.subscribe(AccountVerificationRequested, seen.append)
    register()
    verify()
    with django_capture_on_commit_callbacks(execute=True):
        register()
    assert seen == [] and len(django_mail.outbox) == 1


def test_notices_are_capped_per_account_and_stay_silent(db, django_capture_on_commit_callbacks):
    register()
    verify()
    with django_capture_on_commit_callbacks(execute=True):
        for _ in range(8):
            assert register() is None  # same outcome whether or not a notice goes out
    assert len(django_mail.outbox) == 5
    with django_capture_on_commit_callbacks(execute=True):
        register(email="other@example.com")
        verify("other@example.com")
        register(email="other@example.com")
    assert len(django_mail.outbox) == 6  # another account has its own cap


def test_existing_email_still_validates_every_field(db):
    register()
    assert invalid(phone="123") == {"phone": "invalid_phone"}


@pytest.mark.parametrize("known", [True, False])
def test_password_is_hashed_once_on_both_paths(db, monkeypatch, known):
    if known:
        register()
    calls = []
    real = services.make_password
    monkeypatch.setattr(services, "make_password", lambda p: calls.append(p) or real(p))
    register(email=EMAIL if known else "new@example.com")
    assert calls == [PASSWORD]


# --- refusals --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "actor",
    [
        Actor.user(uuid.uuid4()),
        Actor.admin(uuid.uuid4()),
        Actor.system(),
    ],
)
def test_only_a_visitor_may_register(db, actor):
    with pytest.raises(DomainError) as exc:
        register(actor=actor)
    assert exc.value.code == "forbidden"
    assert Account.objects.count() == 0 and AuditRecord.objects.count() == 0


def test_several_errors_are_reported_together(db):
    assert invalid(name="X", email="nope", password="short", phone="12") == {
        "full_name": "invalid_name",
        "email": "invalid_email",
        "password": "password_too_short",
        "phone": "invalid_phone",
    }


def test_missing_values_are_required(db):
    assert invalid(name="", email=None, password="", phone="   ") == {
        "full_name": "required",
        "email": "required",
        "password": "required",
        "phone": "required",
    }


def test_refused_registration_writes_nothing(db):
    invalid(name="1234")
    assert (Account.objects.count(), IndividualProfile.objects.count()) == (0, 0)
    assert AuditRecord.objects.count() == 0


def test_failure_after_account_creation_rolls_everything_back(db, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("after the account row")

    monkeypatch.setattr(services.IndividualProfile.objects, "create", boom)
    with pytest.raises(RuntimeError):
        register()
    assert (Account.objects.count(), AuditRecord.objects.count()) == (0, 0)


def test_failure_after_profile_rolls_back_account_profile_and_audit(db, monkeypatch):
    real = services.emit_registered

    def boom(account_id):
        real(account_id)
        raise RuntimeError("late")

    monkeypatch.setattr(services, "emit_registered", boom)
    with pytest.raises(RuntimeError):
        register()
    assert (Account.objects.count(), IndividualProfile.objects.count()) == (0, 0)
    assert AuditRecord.objects.count() == 0


def test_event_is_not_delivered_when_the_transaction_rolls_back(
    db, registered, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError), transaction.atomic():
            register()
            raise RuntimeError("outer failure")
    assert registered == []
    assert django_mail.outbox == []


# --- privacy ---------------------------------------------------------------------------------


def test_nothing_private_reaches_logs_audit_or_events(
    db, registered, caplog, django_capture_on_commit_callbacks
):
    canaries = ("canary.person", "canary-passphrase-77", "3009998877", "Canary Person")
    caplog.set_level(logging.DEBUG)
    with django_capture_on_commit_callbacks(execute=True):
        register(
            name="Canary Person",
            email="canary.person@example.com",
            password="canary-passphrase-77",
            phone="0300 9998877",
        )
        register(
            name="Canary Person",
            email="canary.person@example.com",
            password="canary-passphrase-77",
            phone="0300 9998877",
        )
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    audited = str(list(AuditRecord.objects.values()))
    emitted = " ".join(repr(e) for e in registered)
    for canary in canaries:
        assert canary not in logged
        assert canary not in audited
        assert canary not in emitted


# --- validators ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["A", "x" * 81, "John 3rd", "J@ne Doe", "Jane_Doe", "!!", "---", "'.'", "Call 0300 1234567",
     "jane at example dot com", "jane@example.com", "House 12", "Jane\u0000"],
)  # fmt: skip
def test_bad_names(name):
    assert validation.check_full_name(name) == ("", "invalid_name")


@pytest.mark.parametrize("name", ["Al", "Jo-Ann", "O'Brien", "Dr. Who", "عائشہ خان", "Zoë Müller"])
def test_good_names(name):
    assert validation.check_full_name(name) == (name, None)


def test_names_may_contain_zwnj_and_zwj_but_not_at_the_edges():
    inside = "\u0645\u06cc\u200c\u062e\u0627\u0646 \u0627\u0644\u0644\u0647\u200d"
    assert validation.check_full_name("Ab\u200cc")[1] is None
    assert validation.check_full_name("A\u200db")[1] is None
    assert inside and validation.check_full_name("\u200cAb")[1] == "invalid_name"
    assert validation.check_full_name("Ab\u200d")[1] == "invalid_name"
    assert validation.check_full_name("Ab\u200bc")[1] == "invalid_name"  # other invisibles


def test_name_type_and_blank():
    assert validation.check_full_name(None)[1] == "required"
    assert validation.check_full_name(5)[1] == "invalid"
    assert validation.check_full_name("ée")[0] == "ée"  # NFC


@pytest.mark.parametrize("email", ["nope", "a@b", "a@b.", "a b@c.de", "a@@b.co", "x@münchen.de"])
def test_bad_emails(email):
    assert validation.check_email(email) == ("", "invalid_email")


def test_email_length_limit():
    ok = "a" * 242 + "@example.com"  # 254
    assert validation.check_email(ok) == (ok, None)
    assert validation.check_email("a" + ok)[1] == "invalid_email"


@pytest.mark.parametrize(
    ("password", "email", "code"),
    [
        ("short", "", "password_too_short"),
        ("1234567", "", "password_too_short"),
        ("x" * 1025, "", "password_too_long"),
        ("password123", "", "password_too_common"),
        ("12345678", "", "password_too_common"),
        ("PASSWORD123", "", "password_too_common"),
        ("Jane.Doe@Example.com", "jane.doe@example.com", "password_matches_email"),
        ("JANE.DOE12", "jane.doe12@example.com", "password_matches_email"),
        ("        ", "", "required"),
        ("", "", "required"),
        (None, "", "required"),
        (12345678, "", "invalid"),
    ],
)
def test_password_rules(password, email, code):
    assert passwords.password_error(password, email) == code


def test_password_boundaries_accepted():
    assert passwords.password_error("q7!Zm9#k", "") is None
    assert passwords.password_error("q7!Zm9#k" + "x" * (1024 - 8), "") is None


def test_every_error_code_has_its_own_static_message():
    codes = [
        "required", "invalid_name", "invalid_email", "password_too_short", "password_too_long",
        "password_too_common", "password_matches_email", "invalid_phone", "unknown_field",
    ]  # fmt: skip
    for code in codes:
        message = field_message(code)
        assert message != field_message("no_such_code") or code == "required"
        assert "{" not in message
