"""Agency business profile at the service level (Story 1.21)."""

import threading
import uuid

import pytest
from django.db import connection
from django.utils import timezone

from apps.catalog.models import City
from apps.identity import selectors, services, subscribers, validation
from apps.identity.models import Account, AgencyProfile
from apps.identity.services import profile as profile_service
from apps.kernel import events, runtime
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError, field_message
from apps.kernel.models import AuditRecord

SYSTEM = Actor.system("req")
EMAIL = "Office@Skyline-Estates.example"
PASSWORD = "correct horse battery staple"
NAME = "Skyline Estates"
PHONE = "+923215550123"
ADDRESS = "Suite 4, Harbour Tower, Chundrigar Road"
ABOUT = "We help families buy and rent homes."


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    runtime.shutdown()
    subscribers.register()
    yield
    runtime.shutdown()


@pytest.fixture
def city(db):
    City.objects.all().delete()
    return City.objects.create(name="Karachi")


def make(city, email=EMAIL, account_type="agency", verified=True, **overrides):
    view = services.create_account(SYSTEM, email, PASSWORD, account_type)
    if verified:
        services.mark_email_verified(SYSTEM, view.id)
    if account_type == "agency":
        values = {
            "agency_name": NAME,
            "business_phone": PHONE,
            "office_address": ADDRESS,
            "city_id": city.id,
        }
        values.update(overrides)
        AgencyProfile.objects.create(account_id=view.id, **values)
    return view


def user(view):
    return Actor.user(view.id, "req")


def update(view, changes):
    return services.update_agency_profile(user(view), changes)


def errors_of(view, changes):
    with pytest.raises(services.FieldsInvalid) as exc:
        update(view, changes)
    return exc.value.fields


def audits():
    return AuditRecord.objects.filter(action="account.profile_updated").count()


# --- read ----------------------------------------------------------------------------------------


def test_read_returns_all_fields(city):
    view = make(city, description=ABOUT)
    profile = services.get_agency_profile(user(view))
    assert (profile.agency_name, profile.description, profile.city_id) == (NAME, ABOUT, city.id)
    assert (profile.business_phone, profile.office_address) == (PHONE, ADDRESS)
    assert (profile.email, profile.pending_email, profile.verification_status) == (
        EMAIL,
        None,
        "unverified",
    )


def test_read_actor_rules(city):
    view = make(city)
    with pytest.raises(DomainError) as exc:
        services.get_agency_profile(Actor.visitor())
    assert exc.value.code == "unauthenticated"
    for actor in (Actor.system(), Actor.admin(uuid.uuid4())):
        with pytest.raises(DomainError) as exc:
            services.get_agency_profile(actor)
        assert exc.value.code == "forbidden"
    individual = make(city, "ind@example.com", "individual")
    with pytest.raises(DomainError) as exc:
        services.get_agency_profile(user(individual))
    assert exc.value.code == "forbidden"
    assert services.get_agency_profile(user(view)).agency_name == NAME


def test_deactivated_account_has_no_agency_profile(city):
    view = make(city)
    Account.objects.update(deactivated_at=timezone.now())
    for call in (
        lambda: services.get_agency_profile(user(view)),
        lambda: update(view, {"agency_name": "Other Name"}),
    ):
        with pytest.raises(DomainError) as exc:
            call()
        assert exc.value.code == "unauthenticated"


def test_the_view_repr_hides_private_fields(city):
    view = make(city)
    text = repr(services.get_agency_profile(user(view)))
    for secret in (PHONE, ADDRESS, EMAIL):
        assert secret not in text


def test_suspended_agency_may_read_and_update(city):
    view = make(city)
    Account.objects.update(suspended_at=timezone.now(), suspension_reason="x")
    assert update(view, {"agency_name": "Skyline Group"}).agency_name == "Skyline Group"
    assert services.get_agency_profile(user(view)).agency_name == "Skyline Group"


# --- update --------------------------------------------------------------------------------------


def test_update_each_field_and_audit_once(city):
    view = make(city)
    other = City.objects.create(name="Lahore")
    result = update(
        view,
        {
            "agency_name": "  Skyline   Group ",
            "description": ABOUT + "\n\n  Second   paragraph.",
            "city_id": str(other.id),
            "business_phone": "0300 7654321",
            "office_address": "  Plot 9,   Main Road  ",
        },
    )
    assert result.agency_name == "Skyline Group"
    assert result.description == ABOUT + " Second paragraph."
    assert result.city_id == other.id
    assert result.business_phone == "+923007654321"
    assert result.office_address == "Plot 9, Main Road"
    assert audits() == 1
    assert AuditRecord.objects.get(action="account.profile_updated").metadata == {}


def test_other_fields_are_untouched(city):
    view = make(city, description=ABOUT)
    update(view, {"agency_name": "Skyline Group"})
    row = AgencyProfile.objects.get()
    assert (row.description, row.business_phone, row.office_address) == (ABOUT, PHONE, ADDRESS)
    assert row.city_id == city.id


@pytest.mark.parametrize("blank", ["", "   \n ", None])
def test_blank_or_null_description_clears_it(city, blank):
    view = make(city, description=ABOUT)
    assert update(view, {"description": blank}).description == ""
    assert AgencyProfile.objects.get().description == ""
    assert audits() == 1


def test_clearing_an_empty_description_is_unchanged(city):
    view = make(city)
    update(view, {"description": None})
    assert audits() == 0


def test_unchanged_submission_writes_nothing(city):
    view = make(city, description=ABOUT)
    before = AgencyProfile.objects.get().updated_at
    update(
        view,
        {
            "agency_name": NAME,
            "description": ABOUT,
            "city_id": city.id,
            "business_phone": "0321-5550123",
            "office_address": ADDRESS,
        },
    )
    assert audits() == 0
    assert AgencyProfile.objects.get().updated_at == before


@pytest.mark.parametrize("field", ["agency_name", "business_phone", "office_address", "city_id"])
@pytest.mark.parametrize("blank", ["", "  ", None])
def test_mandatory_fields_cannot_be_cleared(city, field, blank):
    view = make(city)
    assert errors_of(view, {field: blank}) == {field: "required"}
    row = AgencyProfile.objects.get()
    assert (row.agency_name, row.business_phone, row.office_address) == (NAME, PHONE, ADDRESS)
    assert row.city_id == city.id
    assert audits() == 0


def test_invalid_values_are_all_reported_at_once(city):
    view = make(city)
    found = errors_of(
        view,
        {
            "agency_name": "Call 03001234567",
            "description": "Mail me at sales@example.com",
            "business_phone": "12ab",
            "office_address": "a",
            "city_id": "not-a-uuid",
        },
    )
    assert found == {
        "agency_name": "invalid_name",
        "description": "contains_contact_details",
        "business_phone": "invalid_phone",
        "office_address": "invalid_address",
        "city_id": "invalid",
    }
    assert AgencyProfile.objects.get().agency_name == NAME


def test_wrong_types_are_invalid(city):
    view = make(city)
    found = errors_of(
        view,
        {
            "agency_name": 5,
            "description": 5,
            "business_phone": 5,
            "office_address": ["x"],
            "city_id": 5,
        },
    )
    assert found == {
        "agency_name": "invalid",
        "description": "invalid_description",
        "business_phone": "invalid",
        "office_address": "invalid",
        "city_id": "invalid",
    }


@pytest.mark.parametrize("bad", [str(uuid.uuid4()), uuid.uuid4()])
def test_unknown_city_is_refused(city, bad):
    view = make(city)
    assert errors_of(view, {"city_id": bad}) == {"city_id": "invalid_city"}


def test_inactive_city_is_refused(city):
    view = make(city)
    dormant = City.objects.create(name="Quetta", is_active=False)
    assert errors_of(view, {"city_id": dormant.id}) == {"city_id": "invalid_city"}
    assert AgencyProfile.objects.get().city_id == city.id


def test_city_is_looked_up_only_after_the_other_fields_pass(city, monkeypatch):
    view = make(city)
    other = City.objects.create(name="Lahore")
    calls = []
    monkeypatch.setattr(profile_service, "city_is_active", lambda c: calls.append(c) or True)
    assert errors_of(view, {"city_id": other.id, "business_phone": "1"}) == {
        "business_phone": "invalid_phone"
    }
    assert calls == []


def test_city_deactivated_mid_request_changes_nothing(city, monkeypatch):
    view = make(city)
    other = City.objects.create(name="Lahore")

    def lookup(city_id):
        City.objects.filter(id=city_id).update(is_active=False)  # deactivated before the write
        return selectors.city_is_active(city_id)

    monkeypatch.setattr(profile_service, "city_is_active", lookup)
    assert errors_of(view, {"agency_name": "Skyline Group", "city_id": other.id}) == {
        "city_id": "invalid_city"
    }
    row = AgencyProfile.objects.get()
    assert (row.agency_name, row.city_id) == (NAME, city.id)
    assert audits() == 0


@pytest.mark.parametrize(
    "changes",
    [
        {},
        None,
        [],
        {"email": "x@example.com"},
        {"verification_status": "verified"},
        {"account_type": "admin"},
        {"agency_name": "Skyline Group", "x": 1},
    ],
)
def test_unknown_or_empty_changes_are_refused(city, changes):
    view = make(city)
    with pytest.raises(DomainError) as exc:
        update(view, changes)
    assert exc.value.code == "validation_error"
    assert audits() == 0


def test_update_actor_rules(city):
    individual = make(city, "ind@example.com", "individual")
    for actor in (user(individual), Actor.admin(uuid.uuid4()), Actor.system()):
        for body in ({"agency_name": "Skyline Group"}, {}, {"email": "x"}, None, {"city_id": 5}):
            with pytest.raises(DomainError) as exc:  # forbidden comes before any body check
                services.update_agency_profile(actor, body)
            assert exc.value.code == "forbidden"
    with pytest.raises(DomainError) as exc:
        services.update_agency_profile(Actor.visitor(), {"agency_name": "Skyline Group"})
    assert exc.value.code == "unauthenticated"


def test_updates_lock_the_account_then_the_profile(city, monkeypatch):
    view = make(city)
    order = []
    for model in (services.Account, services.AgencyProfile):
        real = model.objects.select_for_update

        def spy(*args, _real=real, _name=model.__name__, **kwargs):
            order.append(_name)
            return _real(*args, **kwargs)

        monkeypatch.setattr(model.objects, "select_for_update", spy)
    update(view, {"agency_name": "Skyline Group"})
    assert order == ["Account", "AgencyProfile"]


@pytest.mark.django_db(transaction=True)
def test_parallel_updates_audit_once_per_actual_change():
    runtime.shutdown()
    City.objects.all().delete()
    view = make(City.objects.create(name="Karachi"))
    barrier, errors = threading.Barrier(8), []

    def work():
        try:
            barrier.wait()
            update(view, {"agency_name": "Skyline Group"})
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
    assert audits() == 1
    assert AgencyProfile.objects.get().agency_name == "Skyline Group"


def test_the_actor_summary_is_unchanged(city):
    view = make(city, description=ABOUT)
    summary = selectors.get_actor_summary(view.id)
    assert summary.agency_name == NAME
    text = repr(summary)
    for secret in (PHONE, ADDRESS, ABOUT):
        assert secret not in text


# --- description validator -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Homes in Lahore since 1999", "Homes in Lahore since 1999"),
        ("  Line one.\n\nLine two.\t Three.  ", "Line one. Line two. Three."),
        ("école", "école"),
        ("ہم گھر فروخت کرتے ہیں", "ہم گھر فروخت کرتے ہیں"),
        ("2024", "2024"),
        ("a" * 1000, "a" * 1000),
        ("one\x0btwo\x0cthree\x85four\u2028five", "one two three four five"),
    ],
)
def test_description_accepts_and_collapses(text, expected):
    assert validation.check_description(text) == (expected, None)


@pytest.mark.parametrize("blank", [None, "", "   ", "\n\n"])
def test_description_blank_clears(blank):
    assert validation.check_description(blank) == ("", None)


@pytest.mark.parametrize(
    "text, code",
    [
        (5, "invalid_description"),
        (0, "invalid_description"),
        (False, "invalid_description"),
        ([], "invalid_description"),
        ({}, "invalid_description"),
        (["x"], "invalid_description"),
        ("\u0301", "invalid_description"),
        ("\u0301abc", "invalid_description"),
        ("... \u0301 abc", "invalid_description"),
        ("abc \u0301", "invalid_description"),
        ("a" * 1001, "invalid_description"),
        ("null\x00byte", "invalid_description"),
        ("bell\x07char", "invalid_description"),
        ("hidden‮char", "invalid_description"),
        ("‍word", "invalid_description"),
        ("!!! ... ???", "invalid_description"),
        ("\x00", "invalid_description"),
        ("Call 0300 1234567 now", "contains_contact_details"),
        ("Write to sales@example.com", "contains_contact_details"),
        ("Visit us at House 12, Street 5, Gulberg", "contains_contact_details"),
    ],
)
def test_description_rejects(text, code):
    assert validation.check_description(text) == ("", code)


def test_description_messages_are_static():
    assert field_message("contains_contact_details") == (
        "This text cannot contain phone numbers, email addresses or street addresses."
    )
    assert "1000" in field_message("invalid_description")


def test_oversized_description_is_refused_before_the_contact_scan(monkeypatch):
    def boom(text):
        raise AssertionError("scanned")

    monkeypatch.setattr(validation, "reject_contact_details", boom)
    assert validation.check_description("a" * 5000) == ("", "invalid_description")


def test_an_unchanged_deactivated_city_does_not_block_other_edits(city):
    view = make(city)
    City.objects.filter(id=city.id).update(is_active=False)
    result = update(view, {"agency_name": "Skyline Group", "city_id": str(city.id)})
    assert result.agency_name == "Skyline Group" and result.city_id == city.id
    assert audits() == 1


def test_a_changed_city_must_be_active_even_if_the_stored_one_is_not(city):
    view = make(city)
    dormant = City.objects.create(name="Quetta", is_active=False)
    City.objects.filter(id=city.id).update(is_active=False)
    assert errors_of(view, {"city_id": dormant.id}) == {"city_id": "invalid_city"}
