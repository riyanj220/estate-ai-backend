import logging
import re
import uuid

import pytest
from django.contrib.auth.hashers import check_password
from django.core import mail as django_mail
from django.db import IntegrityError, connection, transaction

from apps.catalog.models import City
from apps.identity import selectors, services, subscribers, validation
from apps.identity.events import AccountRegistered, AccountVerificationRequested
from apps.identity.models import Account, AgencyProfile, IndividualProfile
from apps.identity.state import AccountState
from apps.kernel import events, runtime
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError, field_message
from apps.kernel.models import AuditRecord

VISITOR = Actor.visitor("req-1")
NAME = "Skyline Estates (Pvt) Ltd."
EMAIL = "Office@Skyline-Estates.example"
PASSWORD = "mango-river-lantern-42"
PHONE = "0321-5550123"
E164 = "+923215550123"
ADDRESS = "Suite 4, 12th Floor, Harbour Tower, I.I. Chundrigar Road"


@pytest.fixture(autouse=True)
def fresh_events(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    runtime.shutdown()
    yield
    runtime.shutdown()


@pytest.fixture
def city(db):
    City.objects.all().delete()  # a transactional test elsewhere may have flushed the seed
    return City.objects.create(name="Karachi")


@pytest.fixture
def registered():
    seen = []
    events.subscribe(AccountRegistered, seen.append)
    return seen


def register(city, **overrides):
    values = {
        "agency_name": NAME,
        "email": EMAIL,
        "password": PASSWORD,
        "phone": PHONE,
        "office_address": ADDRESS,
        "city_id": city.id,
        "actor": VISITOR,
    }
    values.update(overrides)
    actor = values.pop("actor")
    services.register_agency(actor, **values)


def invalid(city, **kwargs):
    with pytest.raises(services.RegistrationInvalid) as exc:
        register(city, **kwargs)
    return exc.value.fields


def verify(email=EMAIL):
    services.mark_email_verified(Actor.system(), Account.objects.get(email=email).id)


# --- success ---------------------------------------------------------------------------------


def test_new_registration_creates_unverified_agency_profile_audit_and_event(
    city, registered, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        assert register(city) is None
    account = Account.objects.get()
    assert account.email == EMAIL
    assert account.account_type == "agency"
    assert account.state is AccountState.EMAIL_UNVERIFIED
    assert check_password(PASSWORD, account.password) and account.password != PASSWORD
    profile = AgencyProfile.objects.get()
    assert (
        profile.account_id,
        profile.agency_name,
        profile.business_phone,
        profile.office_address,
        profile.city_id,
        profile.verification_status,
    ) == (account.id, NAME, E164, ADDRESS, city.id, "unverified")
    assert IndividualProfile.objects.count() == 0
    record = AuditRecord.objects.get(action="account.created")
    assert (record.target_id, record.actor_kind, record.metadata) == (
        account.id,
        "visitor",
        {"account_type": "agency"},
    )
    assert registered == [AccountRegistered(account_id=account.id)]
    assert django_mail.outbox == []  # no subscriber wired in this test


def test_storage_normalises_name_address_and_phone(city):
    register(
        city,
        agency_name="  Skyline   Estates  ",
        office_address="  Plot 5, Block   B ",
        phone="+92 321 5550123",
    )
    profile = AgencyProfile.objects.get()
    assert (profile.agency_name, profile.office_address, profile.business_phone) == (
        "Skyline Estates",
        "Plot 5, Block B",
        E164,
    )


def test_verification_email_is_sent_and_confirming_activates_the_agency(
    city, django_capture_on_commit_callbacks
):
    subscribers.register()
    with django_capture_on_commit_callbacks(execute=True):
        register(city)
    assert len(django_mail.outbox) == 1
    message = django_mail.outbox[0]
    assert message.to == [EMAIL]
    token = re.search(r"token=([A-Za-z0-9_-]+)", message.body).group(1)
    before = AgencyProfile.objects.values().get()
    services.confirm_email(VISITOR, token)
    account = Account.objects.get()
    assert account.state is AccountState.ACTIVE
    assert AgencyProfile.objects.values().get() == before


def test_verified_agency_summary_shows_name_and_nothing_private(city):
    register(city)
    verify()
    summary = selectors.get_actor_summary(Account.objects.get().id)
    assert (summary.account_type, summary.agency_name, summary.full_name) == ("agency", NAME, "")
    assert E164 not in repr(summary) and "Harbour" not in repr(summary)
    assert str(city.id) not in repr(summary)


def test_summary_agency_name_is_empty_for_individuals(db):
    services.register_individual(VISITOR, "Ayesha Khan", "a@example.com", PASSWORD, "0300-1234567")
    verify("a@example.com")
    assert selectors.get_actor_summary(Account.objects.get().id).agency_name == ""


# --- existing email --------------------------------------------------------------------------


def test_existing_email_creates_nothing_and_queues_a_notice(
    city, registered, django_capture_on_commit_callbacks, settings
):
    register(city)
    verify()
    before = (Account.objects.count(), AgencyProfile.objects.count(), AuditRecord.objects.count())
    registered.clear()
    django_mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        register(
            city,
            email=EMAIL.upper(),
            agency_name="Other Agency",
            phone="0333-7654321",
            office_address="9 Another Street, Karachi",
        )
    assert before == (
        Account.objects.count(),
        AgencyProfile.objects.count(),
        AuditRecord.objects.count(),
    )
    assert registered == []
    assert [m.to for m in django_mail.outbox] == [[EMAIL]]
    notice = django_mail.outbox[0]
    for private in (PASSWORD, "Other Agency", "7654321", "Another Street", NAME, E164, ADDRESS):
        assert private not in notice.body and private not in notice.subject


def test_existing_individual_email_is_not_overwritten_by_an_agency_registration(
    city, django_capture_on_commit_callbacks
):
    services.register_individual(VISITOR, "Ayesha Khan", EMAIL, PASSWORD, "0300-1234567")
    with django_capture_on_commit_callbacks(execute=True):
        register(city)
    assert Account.objects.get().account_type == "individual"
    assert AgencyProfile.objects.count() == 0


def test_unverified_existing_account_gets_a_new_verification_request(
    city, django_capture_on_commit_callbacks
):
    seen = []
    events.subscribe(AccountVerificationRequested, seen.append)
    register(city)
    with django_capture_on_commit_callbacks(execute=True):
        register(city)
    assert seen == [AccountVerificationRequested(account_id=Account.objects.get().id)]
    assert django_mail.outbox == []


@pytest.mark.parametrize("known", [True, False])
def test_password_is_hashed_once_on_both_paths(city, monkeypatch, known):
    if known:
        register(city)
    calls = []
    real = services.make_password
    monkeypatch.setattr(services, "make_password", lambda p: calls.append(p) or real(p))
    register(city, email=EMAIL if known else "new@example.com")
    assert calls == [PASSWORD]


def test_existing_email_still_validates_every_field(city):
    register(city)
    assert invalid(city, phone="123") == {"phone": "invalid_phone"}


# --- refusals --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "actor", [Actor.user(uuid.uuid4()), Actor.admin(uuid.uuid4()), Actor.system()]
)
def test_only_a_visitor_may_register(city, actor):
    with pytest.raises(DomainError) as exc:
        register(city, actor=actor)
    assert exc.value.code == "forbidden"
    assert Account.objects.count() == 0 and AuditRecord.objects.count() == 0


def test_several_errors_are_reported_together(city):
    assert services_invalid(
        city_id=uuid.uuid4(),
        agency_name="1234",
        email="nope",
        password="short",
        phone="12",
        office_address="ab",
    ) == {
        "agency_name": "invalid_name",
        "email": "invalid_email",
        "password": "password_too_short",
        "phone": "invalid_phone",
        "office_address": "invalid_address",
    }  # the city lookup is skipped while another field fails


def services_invalid(**kwargs):
    with pytest.raises(services.RegistrationInvalid) as exc:
        services.register_agency(VISITOR, **kwargs)
    return exc.value.fields


def test_missing_values_are_required(db):
    assert services_invalid(
        agency_name="",
        email=None,
        password="",
        phone="   ",
        office_address=None,
        city_id="",
    ) == dict.fromkeys(
        ["agency_name", "email", "password", "phone", "office_address", "city_id"], "required"
    )


@pytest.mark.parametrize(
    "city_id", [5, ["x"], {"a": 1}, "not-a-uuid", True, "12345678123456781234567812345678"]
)
def test_wrong_typed_or_malformed_city_is_invalid(city, city_id):
    assert invalid(city, city_id=city_id) == {"city_id": "invalid"}


def test_unknown_and_inactive_cities_are_refused(city):
    assert invalid(city, city_id=uuid.uuid4()) == {"city_id": "invalid_city"}
    inactive = City.objects.create(name="Quetta", is_active=False)
    assert invalid(city, city_id=inactive.id) == {"city_id": "invalid_city"}
    assert Account.objects.count() == 0


def test_uppercase_city_uuid_is_accepted(city):
    register(city, city_id=str(city.id).upper())
    assert AgencyProfile.objects.get().city_id == city.id


def test_refused_registration_writes_nothing(city):
    invalid(city, agency_name="1234")
    assert (Account.objects.count(), AgencyProfile.objects.count()) == (0, 0)
    assert AuditRecord.objects.count() == 0


def test_failure_after_profile_rolls_back_account_profile_audit_and_event(
    city, registered, monkeypatch, django_capture_on_commit_callbacks
):
    real = services.emit_registered

    def boom(account_id):
        real(account_id)
        raise RuntimeError("late")

    monkeypatch.setattr(services, "emit_registered", boom)
    with django_capture_on_commit_callbacks(execute=True), pytest.raises(RuntimeError):
        register(city)
    assert (Account.objects.count(), AgencyProfile.objects.count()) == (0, 0)
    assert AuditRecord.objects.count() == 0
    assert registered == []


def test_failure_creating_the_profile_rolls_back_the_account(city, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("after the account row")

    monkeypatch.setattr(services.AgencyProfile.objects, "create", boom)
    with pytest.raises(RuntimeError):
        register(city)
    assert (Account.objects.count(), AuditRecord.objects.count()) == (0, 0)


# --- storage constraint ----------------------------------------------------------------------


def test_database_rejects_any_other_verification_status(city):
    register(city)
    with pytest.raises(IntegrityError), transaction.atomic():
        AgencyProfile.objects.update(verification_status="verified")
    assert AgencyProfile.objects.get().verification_status == "unverified"


def test_deleting_the_account_removes_the_profile(city):
    register(city)
    Account.objects.all().delete()
    assert AgencyProfile.objects.count() == 0


def test_table_has_no_foreign_key_into_catalog(db):
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, "identity_agencyprofile")
    targets = {c["foreign_key"][0] for c in constraints.values() if c["foreign_key"]}
    assert targets == {"identity_account"}


# --- privacy ---------------------------------------------------------------------------------


def test_nothing_private_reaches_logs_audit_events_errors_or_mail(
    city, registered, caplog, django_capture_on_commit_callbacks
):
    subscribers.register()
    canaries = ("9998877", "Zephyr Quay", "canary-passphrase-77", "canary.agency")
    body = {
        "agency_name": "Canary Agency",
        "email": "canary.agency@example.com",
        "password": "canary-passphrase-77",
        "phone": "0300 9998877",
        "office_address": "Zephyr Quay 77, Block 9",
    }
    caplog.set_level(logging.DEBUG)
    with django_capture_on_commit_callbacks(execute=True):
        register(city, **body)
        register(city, **body)  # existing, unverified: a new verification request
        verify("canary.agency@example.com")
        register(city, **body)  # existing, verified: the notice
        errors = invalid(city, **{**body, "phone": "9998877", "office_address": "Zephyr\x00"})
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    audited = str(list(AuditRecord.objects.values()))
    emitted = " ".join(repr(e) for e in registered)
    mailed = " ".join(f"{m.subject} {m.body} {m.to} {m.alternatives}" for m in django_mail.outbox)
    assert django_mail.outbox
    for canary in ("9998877", "Zephyr Quay"):
        for where in (logged, audited, emitted, mailed, str(errors)):
            assert canary not in where
    for canary in canaries[2:]:
        for where in (logged, audited, emitted, str(errors)):
            assert canary not in where


def test_error_codes_have_static_messages():
    for code in ("invalid_address", "invalid_city", "invalid_name"):
        assert field_message(code) != field_message("no_such_code")
        assert "{" not in field_message(code)


# --- validators ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["A", "x" * 101, "12345", "A@B Realty", "Agency_One", "Call 0300 1234567", "Agency\u0000",
     "Agency\u00b2", "Agency \u0301", "-\u0301Agency", "\u0301Agency", "2\u0301 Homes",
     "info@agency.com", "agency at example dot com", "Agency​", "!!!", "Agency #1", "&-/",
     "‌Agency"],
)  # fmt: skip
def test_bad_agency_names(name):
    assert validation.check_agency_name(name) == ("", "invalid_name")


@pytest.mark.parametrize(
    "name",
    ["Al", "A & B Estates", "Smith, Jones & Co.", "O'Neil-Smith (Pvt) Ltd", "Sun/Moon Realty",
     "Agency 21", "21st Century Homes", "Bahria’s Best", "عائشہ ریئل اسٹیٹ", "x" * 100],
)  # fmt: skip
def test_good_agency_names(name):
    assert validation.check_agency_name(name) == (name, None)


def test_agency_name_type_and_blank():
    assert validation.check_agency_name(None)[1] == "required"
    assert validation.check_agency_name("   ")[1] == "required"
    assert validation.check_agency_name(5)[1] == "invalid"
    assert validation.check_agency_name(["x"])[1] == "invalid"


@pytest.mark.parametrize(
    "address",
    ["abcd", "1234", "x" * 301, "Plot 5\nBlock B", "Plot 5\tBlock", "Plot 5\x00", "Plot\x07 55",
     "!!!!!!", "-----", "Plot 5​B", "‌Plot 55"],
)  # fmt: skip
def test_bad_addresses(address):
    assert validation.check_office_address(address) == ("", "invalid_address")


@pytest.mark.parametrize(
    ("address", "cleaned"),
    [
        ("12345", "12345"),  # a digit is enough
        ("Call me 0300 1234567 here", "Call me 0300 1234567 here"),  # private: no contact check
        ("  Shop  7,   Saddar ", "Shop 7, Saddar"),
        ("x" * 300, "x" * 300),
        ("دفتر نمبر ۵، صدر", "دفتر نمبر ۵، صدر"),
    ],
)
def test_good_addresses(address, cleaned):
    assert validation.check_office_address(address) == (cleaned, None)


def test_address_type_and_blank():
    assert validation.check_office_address(None)[1] == "required"
    assert validation.check_office_address("  ")[1] == "required"
    assert validation.check_office_address(5)[1] == "invalid"


def test_city_id_shapes():
    key = uuid.uuid4()
    assert validation.check_city_id(str(key)) == (key, None)
    assert validation.check_city_id(key) == (key, None)
    assert validation.check_city_id(None)[1] == "required"
    assert validation.check_city_id(" ")[1] == "required"
    assert validation.check_city_id(1)[1] == "invalid"
    assert validation.check_city_id("{" + str(key) + "}")[1] == "invalid"


def test_validate_agency_is_pure_and_uses_the_injected_lookup():
    key = uuid.uuid4()
    details, errors = validation.validate_agency(
        NAME, EMAIL, PASSWORD, PHONE, ADDRESS, str(key), lambda c: c == key
    )
    assert errors == {} and details.city_id == key and details.phone == E164
    details, errors = validation.validate_agency(
        NAME, EMAIL, PASSWORD, PHONE, ADDRESS, str(key), lambda c: False
    )
    assert details is None and errors == {"city_id": "invalid_city"}


def test_password_equal_to_email_is_refused(city):
    assert invalid(city, password=EMAIL) == {"password": "password_matches_email"}
    assert invalid(city, password="weak") == {"password": "password_too_short"}
    assert invalid(city, password="password123") == {"password": "password_too_common"}


def test_inner_joiners_are_accepted_in_name_and_address_but_not_at_the_edges():
    assert validation.check_agency_name("Ab\u200cc Homes")[1] is None
    assert validation.check_agency_name("A\u200db Homes")[1] is None
    assert validation.check_office_address("Shop 7 \u0645\u06cc\u200c\u062e\u0627\u0646")[1] is None
    assert validation.check_office_address("Shop 7 A\u200db Road")[1] is None
    for edge in ("\u200cAgency", "Agency\u200d"):
        assert validation.check_agency_name(edge)[1] == "invalid_name"
    for edge in ("\u200cShop 77", "Shop 77\u200d"):
        assert validation.check_office_address(edge)[1] == "invalid_address"


def test_inner_joiner_registration_succeeds(city):
    register(city, agency_name="Ab\u200cc Homes", office_address="Shop 7 A\u200db Road")
    profile = AgencyProfile.objects.get()
    assert "\u200c" in profile.agency_name and "\u200d" in profile.office_address


def test_name_digits_must_be_decimal_and_marks_must_follow_a_letter():
    assert validation.check_agency_name("Agency\u00b2") == ("", "invalid_name")  # superscript
    assert validation.check_agency_name("Agency \u0301") == ("", "invalid_name")
    assert validation.check_agency_name("Agency, \u0301x") == ("", "invalid_name")
    assert validation.check_agency_name("Zoe\u0308 Homes")[1] is None  # letter then mark
    assert validation.check_agency_name("Agency \u0663")[1] is None  # Arabic-Indic digit (Nd)


def test_address_needs_three_letters_or_digits():
    assert validation.check_office_address("1----") == ("", "invalid_address")
    assert validation.check_office_address("12-- --") == ("", "invalid_address")
    assert validation.check_office_address("123--")[1] is None


def test_city_lookup_runs_only_after_the_cheap_checks_pass():
    calls = []
    details, errors = validation.validate_agency(
        "1", EMAIL, PASSWORD, PHONE, ADDRESS, str(uuid.uuid4()), lambda c: calls.append(c) or True
    )
    assert details is None and errors == {"agency_name": "invalid_name"} and calls == []
    validation.validate_agency(
        NAME, EMAIL, PASSWORD, PHONE, ADDRESS, str(uuid.uuid4()), lambda c: calls.append(c) or True
    )
    assert len(calls) == 1


def test_city_deactivated_between_validation_and_insert_creates_nothing(
    city, registered, monkeypatch, django_capture_on_commit_callbacks
):
    monkeypatch.setattr(
        services,
        "validate_agency",
        lambda *a, **k: validation.validate_agency(*a[:6], lambda c: True),
    )
    City.objects.filter(id=city.id).update(is_active=False)
    with django_capture_on_commit_callbacks(execute=True):
        assert invalid(city) == {"city_id": "invalid_city"}
    assert (Account.objects.count(), AgencyProfile.objects.count()) == (0, 0)
    assert AuditRecord.objects.count() == 0 and registered == []


def test_summary_queries_only_the_matching_profile(city, django_assert_num_queries):
    register(city)
    verify()
    services.register_individual(VISITOR, "Ayesha Khan", "a@example.com", PASSWORD, "0300-1234567")
    verify("a@example.com")
    agency_id = Account.objects.get(email=EMAIL).id
    individual_id = Account.objects.get(email="a@example.com").id
    with django_assert_num_queries(2):  # account + its one profile
        assert selectors.get_actor_summary(agency_id).agency_name == NAME
    with django_assert_num_queries(2):
        summary = selectors.get_actor_summary(individual_id)
    assert (summary.agency_name, summary.full_name) == ("", "Ayesha Khan")


def test_event_canary_over_every_identity_event_type(city, django_capture_on_commit_callbacks):
    from apps.identity import events as identity_events

    seen = []
    for name in dir(identity_events):
        obj = getattr(identity_events, name)
        if (
            isinstance(obj, type)
            and issubclass(obj, events.DomainEvent)
            and obj is not events.DomainEvent
            and obj.__module__ == identity_events.__name__
        ):
            events.subscribe(obj, seen.append)
    with django_capture_on_commit_callbacks(execute=True):
        register(city)
        register(city)  # a verification request
        verify()
        register(city)
    assert any(isinstance(e, AccountRegistered) for e in seen)
    assert any(isinstance(e, AccountVerificationRequested) for e in seen)
    text = " ".join(repr(e) for e in seen)
    assert "5550123" not in text and "Harbour" not in text and E164 not in text


def test_database_error_on_the_profile_insert_leaks_nothing(
    city, monkeypatch, caplog, client, settings
):
    import json

    from django.db import DataError

    def boom(*args, **kwargs):
        raise DataError(f"value too long: {PHONE} {ADDRESS} {NAME}")

    monkeypatch.setattr(services.AgencyProfile.objects, "create", boom)
    caplog.set_level(logging.DEBUG)
    client.raise_request_exception = False
    response = client.post(
        "/api/v1/registrations/agencies",
        json.dumps(
            {
                "agency_name": NAME,
                "email": EMAIL,
                "phone": PHONE,
                "office_address": ADDRESS,
                "city_id": str(city.id),
                "password": PASSWORD,
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "server_error"
    logged = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    for private in ("5550123", "Harbour", "Chundrigar", E164, PASSWORD):
        assert private not in response.content.decode()
        assert private not in logged
    assert Account.objects.count() == 0
