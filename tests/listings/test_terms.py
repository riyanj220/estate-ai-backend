"""Sale and rent terms (Story 2.6): the registry `date` type, typed nullable columns on
`ListingTerms`, applicability by purpose, validation, wire, events and drift. Values are
low-entropy fake words and exact decimals; a float appears only to prove it is refused."""

import dataclasses
import datetime
from decimal import Decimal

import pytest
from django.db import DataError, IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from apps.catalog import registry
from apps.kernel import events
from apps.kernel.errors import field_message
from apps.listings import fields, selectors, services, validation
from apps.listings.events import ListingModelInputsChanged
from apps.listings.models import (
    ListingCommercial,
    ListingPlot,
    ListingResidential,
    ListingRevision,
    ListingTerms,
    PropertyListing,
    terms_constraints,
)
from tests.listings.support import (
    BOTH_TERMS,
    HOUSE,
    LOCATION_IDS,
    RENT_TERMS,
    SALE_TERMS,
    TERMS_IDS,
    make_account,
    random_email,
    seed_cap,
    user,
)

D = Decimal
SALE = ("sale", "apartment", "apartment")
RENT = ("rent", "apartment", "apartment")
RENT_HOUSE = ("rent", "house", "house")
EVERY_PURPOSE_KIND = {"sale": SALE, "rent": RENT}

RENT_SET = {
    "asking_price": "25000",
    "price_basis": "rental_period",
    "rental_period": "month",
    "security_deposit": "50000",
    "availability_date": "2027-03-01",
    "advance_rent": "25000.50",
    "minimum_lease_months": 12,
    "notice_period_days": 30,
    "utility_electricity_included": False,
    "utility_gas_included": True,
    "utility_water_included": True,
    "utility_internet_included": False,
    "pets_allowed": False,
    "occupant_restrictions": "families_only",
    "price_negotiable": True,
}
SALE_SET = {
    "asking_price": "9500000",
    "price_basis": "total",
    "price_negotiable": False,
    "possession_availability": "within_one_month",
}
DEFERRED = (
    "payment_type",
    "down_payment",
    "total_installment_price",
    "monthly_installment",
    "remaining_installments",
    "ownership_document_type",
    "title_document_type",
    "approval_information",
    "installment_available",
)
DATE_FIELD = registry.field("availability_date")


@pytest.fixture(autouse=True)
def cap(db):
    seed_cap()


def draft(kind=RENT, account=None):
    account = account or make_account(random_email())
    return account, services.create_draft(user(account), *kind)


def update(account, view, updates, base=None, origin="manual"):
    return services.apply_updates(
        user(account), view.id, view.revision if base is None else base, updates, origin
    )


def codes(account, view, updates):
    with pytest.raises(services.FieldsInvalid) as caught:
        update(account, view, updates)
    return caught.value.fields


def save(updates, kind=RENT):
    account, view = draft(kind)
    return account, update(account, view, updates)


def terms():
    return ListingTerms.objects.get()


def other_rows():
    return (
        ListingPlot.objects.exists()
        or ListingCommercial.objects.exists()
        or ListingResidential.objects.exists()
    )


# --- saving and the wire ------------------------------------------------------------------------


def test_sale_terms_are_stored_as_typed_columns_on_the_terms_row():
    account, saved = save(SALE_SET, SALE)
    row = terms()
    assert row.price_negotiable is False and row.possession_availability == "within_one_month"
    assert row.asking_price == D("9500000.00")
    assert saved.fields["price_negotiable"] is False
    assert saved.fields["possession_availability"] == "within_one_month"
    assert saved.revision == 2 and ListingTerms.objects.count() == 1
    assert not other_rows()
    assert all(saved.fields[name] is None for name in RENT_TERMS)


def test_rent_terms_are_stored_false_is_kept_and_dates_are_calendar_dates():
    account, saved = save(RENT_SET)
    row = terms()
    assert row.security_deposit == D("50000.00") and row.advance_rent == D("25000.50")
    assert row.availability_date == datetime.date(2027, 3, 1)
    assert type(row.availability_date) is datetime.date  # never a timestamp
    assert (row.minimum_lease_months, row.notice_period_days) == (12, 30)
    assert row.utility_electricity_included is False and row.utility_gas_included is True
    assert row.pets_allowed is False and row.occupant_restrictions == "families_only"
    wire = saved.fields
    assert wire["availability_date"] == "2027-03-01"
    assert wire["security_deposit"] == "50000.00" and wire["advance_rent"] == "25000.50"
    assert wire["minimum_lease_months"] == 12 and wire["pets_allowed"] is False
    assert wire["utility_internet_included"] is False
    assert wire["possession_availability"] is None
    assert set(saved.provenance) == set(RENT_SET)
    assert all(
        dict(entry) == {"origin": "manual", "confirmed": True}
        for entry in saved.provenance.values()
    )
    revision = ListingRevision.objects.get(revision=2)
    assert revision.changes["availability_date"] == "2027-03-01"
    assert revision.changes["pets_allowed"] is False
    assert revision.changes["security_deposit"] == "50000.00"
    assert selectors.get_owned_listing(user(account), saved.id) == saved


def test_the_wire_order_follows_the_registry_and_the_terms_come_after_the_details():
    names = list(fields.WIRE_FIELDS)
    assert names.index("price_negotiable") > names.index("business_centre_facilities")
    assert tuple(n for n in names if n in TERMS_IDS) == TERMS_IDS
    assert names[-1] == "area_sqft"
    ids = [f.id for f in registry.fields()]
    after = len(TERMS_IDS) + len(LOCATION_IDS)  # the location (Story 2.7) is appended after them
    assert ids[-after : -len(LOCATION_IDS)] == list(TERMS_IDS)


def test_a_date_is_the_same_text_whatever_the_server_time_zone():
    account, view = draft()
    with timezone.override("Pacific/Kiritimati"):  # UTC+14: a timestamp conversion would shift it
        saved = update(account, view, {"availability_date": "2027-03-01"})
    assert saved.fields["availability_date"] == "2027-03-01"
    with timezone.override("Pacific/Pago_Pago"):  # UTC-11
        assert (
            selectors.get_owned_listing(user(account), saved.id).fields["availability_date"]
            == "2027-03-01"
        )
    assert terms().availability_date == datetime.date(2027, 3, 1)


def test_unanswered_is_null_never_zero_false_or_empty():
    account, view = draft()
    assert all(view.fields[name] is None for name in TERMS_IDS)
    assert terms().price_negotiable is None
    saved = update(account, view, {"asking_price": "25000"})
    for name in TERMS_IDS:
        assert saved.fields[name] is None, name
        assert getattr(terms(), name) is None, name
    assert not any(name in saved.provenance for name in TERMS_IDS)


def test_a_clear_sets_null_removes_provenance_and_bumps_the_revision():
    account, saved = save(RENT_SET)
    cleared = update(account, saved, {"security_deposit": None, "availability_date": None})
    assert cleared.revision == saved.revision + 1
    assert (
        cleared.fields["security_deposit"] is None and cleared.fields["availability_date"] is None
    )
    assert terms().security_deposit is None and terms().availability_date is None
    assert "security_deposit" not in cleared.provenance
    assert "availability_date" not in cleared.provenance
    assert ListingRevision.objects.get(revision=cleared.revision).changes == {
        "security_deposit": None,
        "availability_date": None,
    }
    assert terms().advance_rent == D("25000.50")  # the rest is untouched
    assert ListingTerms.objects.count() == 1


def test_zero_is_a_real_money_answer_and_false_is_a_real_boolean_answer():
    _, saved = save({"security_deposit": "0", "advance_rent": 0, "pets_allowed": False})
    assert saved.fields["security_deposit"] == "0.00" and saved.fields["advance_rent"] == "0.00"
    assert saved.fields["pets_allowed"] is False
    assert terms().security_deposit == D("0.00")


def test_repeating_a_value_is_a_no_op_and_a_date_compares_by_value():
    account, saved = save(RENT_SET)
    same = update(
        account, saved, {"availability_date": "2027-03-01", "security_deposit": "50000.00"}
    )
    assert same.revision == saved.revision and ListingRevision.objects.count() == 2
    changed = update(account, saved, {"availability_date": "2027-03-02"})
    assert changed.revision == saved.revision + 1
    assert changed.fields["availability_date"] == "2027-03-02"


def test_resaving_an_ai_value_manually_confirms_it():
    account, view = draft()
    ai = update(account, view, {"availability_date": "2027-03-01"}, origin="ai")
    assert dict(ai.provenance["availability_date"]) == {"origin": "ai", "confirmed": False}
    confirmed = update(account, ai, {"availability_date": "2027-03-01"})
    assert confirmed.revision == ai.revision + 1
    assert dict(confirmed.provenance["availability_date"]) == {
        "origin": "manual",
        "confirmed": True,
    }


# --- purpose rules ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", RENT_TERMS)
def test_a_rent_term_is_not_applicable_to_a_sale_draft(name):
    account, view = draft(SALE)
    value = RENT_SET[name]
    assert codes(account, view, {name: value}) == {name: "not_applicable"}
    assert getattr(terms(), name) is None
    assert PropertyListing.objects.get().revision == 1


@pytest.mark.parametrize("name", SALE_TERMS)
def test_a_sale_term_is_not_applicable_to_a_rent_draft(name):
    account, view = draft(RENT)
    assert codes(account, view, {name: "available_now"}) == {name: "not_applicable"}
    assert getattr(terms(), name) is None
    assert PropertyListing.objects.get().revision == 1


@pytest.mark.parametrize("purpose", ["sale", "rent"])
@pytest.mark.parametrize("value", [True, False])
def test_price_negotiable_applies_to_both_purposes(purpose, value):
    _, saved = save({"price_negotiable": value}, EVERY_PURPOSE_KIND[purpose])
    assert saved.fields["price_negotiable"] is value


@pytest.mark.parametrize("kind", [SALE, RENT, HOUSE, RENT_HOUSE])
def test_the_terms_follow_the_purpose_for_every_subtype_alike(kind):
    account, view = draft(kind)
    rent = kind[0] == "rent"
    wrong = {"possession_availability": "available_now"} if rent else {"pets_allowed": True}
    right = {"security_deposit": "10"} if rent else {"possession_availability": "available_now"}
    assert codes(account, view, wrong) == {next(iter(wrong)): "not_applicable"}
    assert update(account, view, right).revision == 2


def test_the_right_set_beside_the_wrong_one_reports_only_the_wrong_field():
    account, view = draft(SALE)
    assert codes(
        account, view, {"asking_price": "10", "security_deposit": "5", "price_negotiable": True}
    ) == {"security_deposit": "not_applicable"}
    assert PropertyListing.objects.get().revision == 1
    assert terms().asking_price is None and terms().price_negotiable is None


def test_a_wrong_purpose_is_judged_before_the_value():
    account, view = draft(SALE)
    assert codes(account, view, {"availability_date": "not a date", "advance_rent": "-1"}) == {
        "availability_date": "not_applicable",
        "advance_rent": "not_applicable",
    }


def test_null_on_a_not_applicable_term_is_a_documented_no_op():
    account, view = draft(SALE)
    result = update(account, view, {"security_deposit": None, "availability_date": None})
    assert result.revision == 1 and ListingRevision.objects.count() == 1
    assert result.fields["security_deposit"] is None
    rent_account, rent_view = draft(RENT)
    result = update(rent_account, rent_view, {"possession_availability": None})
    assert (
        result.revision == 1
        and ListingRevision.objects.filter(listing_id=rent_view.id).count() == 1
    )


def test_a_stored_term_of_the_other_purpose_can_still_be_cleared():
    # data stored under an older registry that let the other purpose through is clearable by null
    account, view = draft(SALE)
    ListingTerms.objects.update(security_deposit=D("5.00"))
    cleared = update(account, view, {"security_deposit": None})
    assert cleared.revision == 2 and terms().security_deposit is None


# --- dates --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "2027-02-30",
        "2027-02-29",
        "2027-13-01",
        "2027-00-10",
        "2027-03-00",
        "2027-3-1",
        "2027-03-1",
        "27-03-01",
        "2027/03/01",
        "01-03-2027",
        "20270301",
        "2027-W09-1",
        "2027-03-01T00:00:00",
        "2027-03-01T00:00:00Z",
        "2027-03-01 00:00:00",
        "2027-03-01 ",
        " 2027-03-01",
        "2027-03-01\n",
        "٢٠٢٧-٠٣-٠١",
        "２０２７-０３-０１",
        "0000-01-01",
        "",
        "tomorrow",
        "2027-03-01" + "0" * 50,
        20270301,
        2027.0,
        True,
        ["2027-03-01"],
        {"date": "2027-03-01"},
    ],
)
def test_a_date_must_be_a_real_iso_calendar_date(value):
    account, view = draft()
    assert codes(account, view, {"availability_date": value}) == {"availability_date": "invalid"}
    assert terms().availability_date is None and PropertyListing.objects.get().revision == 1


@pytest.mark.parametrize("value", ["1999-12-31", "2101-01-01", "1900-01-01", "9999-12-31"])
def test_a_real_date_outside_the_window_is_invalid_date(value):
    account, view = draft()
    assert codes(account, view, {"availability_date": value}) == {
        "availability_date": "invalid_date"
    }
    assert terms().availability_date is None


@pytest.mark.parametrize("value", ["2000-01-01", "2100-12-31", "2028-02-29", "2027-03-01"])
def test_the_window_is_inclusive_and_a_leap_day_is_real(value):
    _, saved = save({"availability_date": value})
    assert saved.fields["availability_date"] == value


def test_the_date_codes_are_catalogue_texts_that_name_no_value():
    account, view = draft()
    window = codes(account, view, {"availability_date": "2101-01-01"})
    assert field_message(window["availability_date"]) == "Enter a date within the allowed range."
    assert "2101" not in field_message("invalid_date")
    assert field_message("invalid") == "Enter a valid value."


def test_check_date_returns_a_date_and_never_copies_or_coerces():
    assert validation.check_date(DATE_FIELD, "2027-03-01") == (datetime.date(2027, 3, 1), None)
    assert validation.check_date(DATE_FIELD, datetime.date(2027, 3, 1)) == (None, "invalid")
    assert validation.check_date(DATE_FIELD, datetime.datetime(2027, 3, 1)) == (None, "invalid")
    assert validation.check_date(DATE_FIELD, "2000-01-01")[1] is None
    assert validation.check_date(DATE_FIELD, "1999-12-31") == (None, "invalid_date")
    assert validation.check_date(DATE_FIELD, None) == (None, "invalid")


def test_a_changed_registry_window_is_judged_on_the_next_save(monkeypatch):
    account, view = draft()
    real = registry.field
    monkeypatch.setattr(
        registry,
        "field",
        lambda name: (
            dataclasses.replace(real(name), maximum=datetime.date(2030, 12, 31))
            if name == "availability_date"
            else real(name)
        ),
    )
    assert codes(account, view, {"availability_date": "2031-01-01"}) == {
        "availability_date": "invalid_date"
    }
    assert update(account, view, {"availability_date": "2030-12-31"}).revision == 2


# --- money, integers, enums, booleans -----------------------------------------------------------


@pytest.mark.parametrize("name", ["security_deposit", "advance_rent"])
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("-1", "invalid_price"),
        ("-0.01", "invalid_price"),
        ("1.234", "invalid_price"),
        ("1000000000000", "invalid_price"),
        ("999999999999.995", "invalid_price"),
        ("abc", "invalid"),
        ("1,000", "invalid"),
        ("1e3", "invalid"),
        ("", "invalid"),
        (1.5, "invalid"),
        (True, "invalid"),
        (["1"], "invalid"),
    ],
)
def test_money_terms_follow_the_asking_price_rules(name, value, expected):
    account, view = draft()
    assert codes(account, view, {name: value}) == {name: expected}
    assert getattr(terms(), name) is None


@pytest.mark.parametrize("name", ["security_deposit", "advance_rent"])
@pytest.mark.parametrize(
    ("value", "stored"),
    [("0", "0.00"), ("0.01", "0.01"), (5, "5.00"), ("999999999999.99", "999999999999.99")],
)
def test_money_terms_accept_their_bounds_with_two_decimals(name, value, stored):
    _, saved = save({name: value})
    assert saved.fields[name] == stored
    assert getattr(terms(), name) == D(stored)


@pytest.mark.parametrize(
    ("name", "bounds"),
    [("minimum_lease_months", (1, 120)), ("notice_period_days", (0, 365))],
)
def test_integer_terms_have_inclusive_ranges(name, bounds):
    low, high = bounds
    account, view = draft()
    for bad in (low - 1, high + 1, 10**12, -(10**12)):
        assert codes(account, view, {name: bad}) == {name: "invalid_range"}
    saved = update(account, view, {name: low})
    assert saved.fields[name] == low
    assert update(account, saved, {name: high}).fields[name] == high


@pytest.mark.parametrize("name", ["minimum_lease_months", "notice_period_days"])
@pytest.mark.parametrize("value", ["12", 12.0, 12.5, True, False, "", [12], {"n": 12}])
def test_integer_terms_are_json_integers_only(name, value):
    account, view = draft()
    assert codes(account, view, {name: value}) == {name: "invalid"}


@pytest.mark.parametrize("name", [n for n in RENT_TERMS if registry.field(n).type == "boolean"])
@pytest.mark.parametrize("value", ["yes", "true", 1, 0, "", [True], 1.0])
def test_boolean_terms_are_json_booleans_only(name, value):
    account, view = draft()
    assert codes(account, view, {name: value}) == {name: "invalid"}
    assert update(account, view, {name: False}).fields[name] is False


def test_price_negotiable_is_a_json_boolean_only():
    account, view = draft(SALE)
    assert codes(account, view, {"price_negotiable": "yes"}) == {"price_negotiable": "invalid"}


def test_the_enum_terms_take_their_values_and_refuse_others():
    for name, kind, values, bad in [
        (
            "occupant_restrictions",
            RENT,
            ("no_restriction", "families_only", "bachelors_only"),
            "any",
        ),
        (
            "possession_availability",
            SALE,
            ("available_now", "within_one_month", "within_three_months", "after_three_months"),
            "tomorrow",
        ),
    ]:
        assert registry.field(name).allowed_values == values
        for value in values:
            assert save({name: value}, kind)[1].fields[name] == value
            ListingRevision.objects.all().delete()
            PropertyListing.objects.all().delete()
        account, view = draft(kind)
        assert codes(account, view, {name: bad}) == {name: "invalid_choice"}
        assert codes(account, view, {name: "Available_Now"}) == {name: "invalid_choice"}
        assert codes(account, view, {name: 3}) == {name: "invalid"}
        assert codes(account, view, {name: True}) == {name: "invalid"}


def test_all_failures_are_reported_at_once_and_nothing_is_written():
    account, view = draft()
    result = codes(
        account,
        view,
        {
            "security_deposit": "-1",
            "availability_date": "2027-02-30",
            "advance_rent": "x",
            "minimum_lease_months": 0,
            "notice_period_days": "5",
            "pets_allowed": "no",
            "occupant_restrictions": "any",
            "possession_availability": "available_now",
            "utility_gas_included": True,
        },
    )
    assert result == {
        "security_deposit": "invalid_price",
        "availability_date": "invalid",
        "advance_rent": "invalid",
        "minimum_lease_months": "invalid_range",
        "notice_period_days": "invalid",
        "pets_allowed": "invalid",
        "occupant_restrictions": "invalid_choice",
        "possession_availability": "not_applicable",
    }
    assert PropertyListing.objects.get().revision == 1
    assert all(getattr(terms(), name) is None for name in TERMS_IDS)


def test_no_relation_is_checked_between_the_deposit_advance_and_the_rent():
    _, saved = save({"asking_price": "10", "security_deposit": "999", "advance_rent": "5000"})
    assert (
        saved.fields["security_deposit"] == "999.00" and saved.fields["advance_rent"] == "5000.00"
    )


# --- deferred terms -----------------------------------------------------------------------------


@pytest.mark.parametrize("name", DEFERRED)
@pytest.mark.parametrize("kind", [SALE, RENT])
def test_deferred_terms_are_unknown_fields_and_nothing_is_written(name, kind):
    account, view = draft(kind)
    assert codes(account, view, {name: "anything", "price_negotiable": True}) == {
        validation.NON_FIELD: "unknown_field"
    }
    assert name not in {f.id for f in registry.fields()}
    assert name not in fields.EDITABLE_FIELDS and name not in view.fields
    assert terms().price_negotiable is None and PropertyListing.objects.get().revision == 1


def test_every_deferred_term_together_is_one_unknown_field_error():
    account, view = draft(SALE)
    assert codes(account, view, dict.fromkeys(DEFERRED, 1)) == {
        validation.NON_FIELD: "unknown_field"
    }


def test_an_unknown_id_is_never_echoed_in_the_codes():
    account, view = draft(SALE)
    result = codes(account, view, {"installment_plan_xyz": 1})
    assert "installment_plan_xyz" not in repr(result)


# --- rent without a period ----------------------------------------------------------------------


def test_a_rent_draft_can_save_without_a_rental_period_and_none_is_assumed():
    account, view = draft(RENT)
    saved = update(account, view, {"price_basis": "rental_period"})
    assert saved.fields["price_basis"] == "rental_period" and saved.fields["rental_period"] is None
    assert terms().rental_period is None
    again = selectors.get_owned_listing(user(account), saved.id)
    assert again.fields["rental_period"] is None and "rental_period" not in again.provenance
    more = update(account, saved, {"security_deposit": "1", "asking_price": "5"})
    assert more.fields["rental_period"] is None
    definition = registry.field("rental_period")
    assert definition.requirement == "required_for_publication"
    assert definition.applicability.purposes == ("rent",)
    assert more.fields["price_basis"] == "rental_period"  # a basis, never a period


# --- seller-declared and AI ------------------------------------------------------------------


def test_possession_availability_is_seller_declared_and_not_ai_extractable():
    definition = registry.field("possession_availability")
    assert definition.seller_declared is True and definition.ai_extractable is False
    assert definition.requirement == "recommended"
    assert definition.applicability.purposes == ("sale",)
    assert registry.field("occupant_restrictions").seller_declared is False


def test_the_terms_definitions_match_the_specification():
    expected = {
        "price_negotiable": ("boolean", "optional", (), True),
        "possession_availability": ("enum", "recommended", ("sale",), False),
        "security_deposit": ("money", "recommended", ("rent",), True),
        "availability_date": ("date", "recommended", ("rent",), True),
        "advance_rent": ("money", "optional", ("rent",), True),
        "minimum_lease_months": ("integer", "optional", ("rent",), True),
        "notice_period_days": ("integer", "optional", ("rent",), True),
        "utility_electricity_included": ("boolean", "optional", ("rent",), True),
        "utility_gas_included": ("boolean", "optional", ("rent",), True),
        "utility_water_included": ("boolean", "optional", ("rent",), True),
        "utility_internet_included": ("boolean", "optional", ("rent",), True),
        "pets_allowed": ("boolean", "optional", ("rent",), True),
        "occupant_restrictions": ("enum", "optional", ("rent",), True),
    }
    assert tuple(expected) == TERMS_IDS == (*BOTH_TERMS, *SALE_TERMS, *RENT_TERMS)
    for name, (kind, requirement, purposes, extractable) in expected.items():
        definition = registry.field(name)
        assert (definition.type, definition.requirement) == (kind, requirement), name
        assert definition.applicability.purposes == purposes, name
        assert definition.applicability.categories == definition.applicability.subtypes == ()
        assert definition.ai_extractable is extractable, name
        assert definition.field_class == "public" and not definition.valuation_input, name
        assert fields.STORAGE[name] == fields.TERMS, name
    for name in ("security_deposit", "advance_rent"):
        assert registry.field(name).minimum == D(0)
        assert registry.field(name).maximum == D("999999999999.99")
    assert registry.field("minimum_lease_months").minimum == 1
    assert registry.field("minimum_lease_months").maximum == 120
    assert (
        registry.field("notice_period_days").minimum,
        registry.field("notice_period_days").maximum,
    ) == (0, 365)
    assert (DATE_FIELD.minimum, DATE_FIELD.maximum) == (
        datetime.date(2000, 1, 1),
        datetime.date(2100, 12, 31),
    )


# --- events -------------------------------------------------------------------------------------


@pytest.fixture
def bus(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    seen = []
    events.subscribe(ListingModelInputsChanged, seen.append)
    return seen


@pytest.mark.parametrize(
    ("kind", "updates"),
    [
        (SALE, SALE_SET),
        (RENT, RENT_SET),
        (RENT, {"security_deposit": "5", "availability_date": "2027-03-01"}),
        (SALE, {"possession_availability": "available_now", "price_negotiable": True}),
    ],
)
def test_terms_never_emit_the_valuation_event(
    bus, django_capture_on_commit_callbacks, kind, updates
):
    account, view = draft(kind)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        update(account, view, updates)
    assert all(e.listing_id == view.id for e in bus)
    changed_inputs = {n for n in updates if registry.field(n).valuation_input}
    assert len(bus) == (1 if changed_inputs else 0)
    assert len(callbacks) == len(bus)


def test_a_model_input_beside_terms_emits_exactly_one_event_after_commit(
    bus, django_capture_on_commit_callbacks
):
    account, view = draft(RENT)
    with django_capture_on_commit_callbacks(execute=True):
        update(account, view, {"bedrooms": 3, "security_deposit": "5", "pets_allowed": True})
        assert bus == []  # nothing before the commit
    assert len(bus) == 1 and bus[0].listing_id == view.id


def test_a_failure_after_the_event_is_queued_rolls_everything_back_and_delivers_nothing(
    bus, monkeypatch, django_capture_on_commit_callbacks
):
    account, view = draft(RENT_HOUSE)
    real_emit = services.emit
    queued = []

    def emit_then_fail(event):
        real_emit(event)  # the after-commit delivery is registered ...
        queued.append(event)
        raise RuntimeError("after the event was queued")  # ... and the transaction then fails

    monkeypatch.setattr(services, "emit", emit_then_fail)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(RuntimeError):
            update(account, view, {"bedrooms": 3, "availability_date": "2027-03-01"})
    assert len(queued) == 1  # the failure really came after the model input was queued
    assert callbacks == [] and bus == []  # a rolled-back transaction delivers nothing
    assert terms().availability_date is None
    assert not ListingResidential.objects.exists()
    assert PropertyListing.objects.get().revision == 1
    # control: the same edit without the failure delivers exactly one event after commit
    monkeypatch.setattr(services, "emit", real_emit)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        update(account, view, {"bedrooms": 3, "availability_date": "2027-03-01"})
    assert len(callbacks) == 1 and len(bus) == 1


def test_a_failure_while_writing_the_revision_rolls_the_terms_back(monkeypatch):
    account, view = draft()

    def boom(*args, **kwargs):
        raise RuntimeError("after the write")

    monkeypatch.setattr(ListingRevision.objects, "create", boom)
    with pytest.raises(RuntimeError):
        update(account, view, {"availability_date": "2027-03-01", "pets_allowed": True})
    assert terms().availability_date is None and terms().pets_allowed is None
    assert PropertyListing.objects.get().revision == 1


# --- stale, replay, other owners, older registry ------------------------------------------------


def test_a_stale_base_revision_is_refused_with_the_current_view_and_writes_nothing():
    account, view = draft()
    saved = update(account, view, {"security_deposit": "5"})
    with pytest.raises(services.StaleRevision) as caught:
        update(account, view, {"security_deposit": "99", "availability_date": "2027-03-01"}, base=1)
    assert (
        caught.value.current == saved and caught.value.current.fields["security_deposit"] == "5.00"
    )
    assert terms().security_deposit == D("5.00") and terms().availability_date is None


def test_a_replay_of_a_saved_edit_is_stale_and_writes_nothing():
    account, view = draft()
    update(account, view, {"availability_date": "2027-03-01"})
    with pytest.raises(services.StaleRevision):
        update(account, view, {"availability_date": "2027-03-01"}, base=1)
    assert PropertyListing.objects.get().revision == 2 and ListingRevision.objects.count() == 2


def test_another_owner_gets_not_found_and_nothing_changes():
    _, view = draft()
    other = make_account(random_email())
    with pytest.raises(services.DomainError) as caught:
        services.apply_updates(user(other), view.id, 1, {"security_deposit": "5"})
    assert caught.value.code == "not_found" and terms().security_deposit is None


def test_a_draft_from_an_older_registry_is_revalidated_under_the_current_version():
    account, view = draft()
    PropertyListing.objects.update(registry_version=5)  # before the terms existed
    assert codes(account, view, {"availability_date": "2101-01-01"}) == {
        "availability_date": "invalid_date"
    }
    assert PropertyListing.objects.get().registry_version == 5
    saved = update(account, view, {"availability_date": "2027-03-01"})
    assert saved.registry_version == registry.registry_version() == 7
    assert PropertyListing.objects.get().registry_version == 7


# --- stored values outside today's rules --------------------------------------------------------


def drop_constraint(name):
    """Stored data from an older, wider registry: the test drops the CHECK (rolled back with it)."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute(f"ALTER TABLE listings_listingterms DROP CONSTRAINT {name}")


def test_a_stored_out_of_window_date_resent_unchanged_is_invalid_date():
    account, view = draft()
    saved = update(account, view, {"availability_date": "2027-03-01"})
    drop_constraint("listings_terms_availability_date_range")
    ListingTerms.objects.update(availability_date=datetime.date(1999, 12, 31))
    assert codes(account, saved, {"availability_date": "1999-12-31"}) == {
        "availability_date": "invalid_date"
    }
    assert terms().availability_date == datetime.date(1999, 12, 31)
    assert PropertyListing.objects.get().revision == 2
    fixed = update(account, saved, {"availability_date": "2027-03-02"})
    assert fixed.fields["availability_date"] == "2027-03-02"


def test_a_stored_out_of_range_integer_and_money_are_revalidated_when_resent():
    account, view = draft()
    saved = update(account, view, {"minimum_lease_months": 12, "advance_rent": "5"})
    drop_constraint("listings_terms_minimum_lease_months_range")
    drop_constraint("listings_terms_advance_rent_range")
    ListingTerms.objects.update(minimum_lease_months=0, advance_rent=D("-1.00"))
    assert codes(account, saved, {"minimum_lease_months": 0, "advance_rent": "-1"}) == {
        "minimum_lease_months": "invalid_range",
        "advance_rent": "invalid_price",
    }
    assert terms().minimum_lease_months == 0 and terms().advance_rent == D("-1.00")


def test_a_stored_out_of_window_value_is_untouched_by_an_unrelated_edit():
    account, view = draft()
    saved = update(account, view, {"availability_date": "2027-03-01"})
    drop_constraint("listings_terms_availability_date_range")
    ListingTerms.objects.update(availability_date=datetime.date(2101, 1, 1))
    after = update(account, saved, {"pets_allowed": True})
    assert terms().availability_date == datetime.date(2101, 1, 1)
    assert after.fields["availability_date"] == "2101-01-01"  # served as stored, never altered


def test_the_view_is_unchanged_by_a_later_read_path():
    account, saved = save(RENT_SET)
    assert selectors.list_owned(user(account)).items[0] == saved


# --- constraints and drift ----------------------------------------------------------------------


def listing_row(kind=RENT):
    return PropertyListing.objects.create(
        owner_account_id=make_account(random_email()).id,
        status="draft",
        revision=1,
        registry_version=6,
        purpose=kind[0],
        category=kind[1],
        subtype=kind[2],
    )


def terms_row(**values):
    return ListingTerms.objects.create(listing=listing_row(), **values)


def _migrated():
    state = MigrationLoader(connection).project_state()
    options = state.models[("listings", "listingterms")].options
    return {c.name: c.condition for c in options["constraints"]}


def test_every_new_term_has_a_named_constraint_except_the_booleans():
    names = {c.name for c in terms_constraints()}
    assert {"listings_terms_price_range", "listings_terms_basis_valid"} <= names
    assert "listings_terms_period_valid" in names
    booleans = 0
    for name in fields.TERMS_FIELDS:
        if name in ("asking_price", "price_basis", "rental_period"):
            continue
        kind = registry.field(name).type
        if kind == "boolean":
            booleans += 1
            continue
        suffix = "valid" if kind == "enum" else "range"
        assert f"listings_terms_{name}_{suffix}" in names, name
    assert len(names) == 3 + len(TERMS_IDS) - booleans


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"possession_availability": "soon"}, "listings_terms_possession_availability_valid"),
        ({"occupant_restrictions": "any"}, "listings_terms_occupant_restrictions_valid"),
        ({"security_deposit": "-0.01"}, "listings_terms_security_deposit_range"),
        ({"advance_rent": "-1"}, "listings_terms_advance_rent_range"),
        (
            {"availability_date": datetime.date(1999, 12, 31)},
            "listings_terms_availability_date_range",
        ),
        (
            {"availability_date": datetime.date(2101, 1, 1)},
            "listings_terms_availability_date_range",
        ),
        ({"minimum_lease_months": 0}, "listings_terms_minimum_lease_months_range"),
        ({"minimum_lease_months": 121}, "listings_terms_minimum_lease_months_range"),
        ({"notice_period_days": -1}, "listings_terms_notice_period_days_range"),
        ({"notice_period_days": 366}, "listings_terms_notice_period_days_range"),
    ],
)
def test_terms_constraints_name_themselves(db, overrides, constraint):
    with pytest.raises(IntegrityError, match=constraint), transaction.atomic():
        terms_row(**overrides)
    assert ListingTerms.objects.count() == 0 and PropertyListing.objects.count() == 0


def test_the_bounds_and_nulls_are_accepted(db):
    terms_row(
        security_deposit="0",
        advance_rent="999999999999.99",
        availability_date=datetime.date(2000, 1, 1),
        minimum_lease_months=1,
        notice_period_days=0,
        pets_allowed=False,
        occupant_restrictions="bachelors_only",
    )
    terms_row(
        availability_date=datetime.date(2100, 12, 31),
        minimum_lease_months=120,
        notice_period_days=365,
        possession_availability="after_three_months",
    )
    row = terms_row()  # every new column is nullable
    assert all(getattr(row, name) is None for name in TERMS_IDS)


def test_over_the_column_width_is_a_data_error(db):
    with pytest.raises(DataError), transaction.atomic():
        terms_row(advance_rent="100000000000000")


def test_the_column_types_in_the_database(db):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, data_type, numeric_precision, numeric_scale, is_nullable "
            "FROM information_schema.columns WHERE table_name = 'listings_listingterms' "
            "AND column_name = ANY(%s)",
            [list(TERMS_IDS)],
        )
        found = {row[0]: row[1:] for row in cursor.fetchall()}
    assert set(found) == set(TERMS_IDS)
    for name, (data_type, precision, scale, nullable) in found.items():
        assert nullable == "YES", name
        kind = registry.field(name).type
        if kind == "boolean":
            assert data_type == "boolean", name
        elif kind == "integer":
            assert data_type == "smallint", name
        elif kind == "date":
            assert data_type == "date", name  # not a timestamp
        elif kind == "money":
            assert (data_type, precision, scale) == ("numeric", 14, 2), name
        else:
            assert data_type == "character varying", name


def test_one_terms_row_per_listing(db):
    row = listing_row()
    ListingTerms.objects.create(listing=row)
    with pytest.raises(IntegrityError), transaction.atomic():
        ListingTerms.objects.create(listing=row)


def test_the_migration_matches_the_models_and_the_registry(db):
    migrated = _migrated()
    assert migrated == {c.name: c.condition for c in terms_constraints()}
    assert "999999999999.99" in repr(migrated["listings_terms_security_deposit_range"])
    assert "2000, 1, 1" in repr(migrated["listings_terms_availability_date_range"])
    assert "2100, 12, 31" in repr(migrated["listings_terms_availability_date_range"])
    assert "120" in repr(migrated["listings_terms_minimum_lease_months_range"])
    assert "365" in repr(migrated["listings_terms_notice_period_days_range"])
    assert "after_three_months" in repr(migrated["listings_terms_possession_availability_valid"])
    assert "bachelors_only" in repr(migrated["listings_terms_occupant_restrictions_valid"])
    assert "listings_terms_price_range" in migrated  # the earlier constraints are kept


def test_a_changed_bound_or_enum_would_differ_from_the_migration(db, monkeypatch):
    migrated = _migrated()
    real = registry.field
    changes = [
        ("security_deposit", {"maximum": D(5)}),
        ("advance_rent", {"minimum": D(1)}),
        ("minimum_lease_months", {"maximum": D(5)}),
        ("notice_period_days", {"minimum": D(1)}),
        ("availability_date", {"maximum": datetime.date(2050, 1, 1)}),
        ("availability_date", {"minimum": datetime.date(2010, 1, 1)}),
        ("possession_availability", {"allowed_values": ("available_now",)}),
        ("occupant_restrictions", {"allowed_values": ("families_only",)}),
    ]
    for field_id, change in changes:
        monkeypatch.setattr(
            registry,
            "field",
            lambda name, fid=field_id, ch=change: (
                dataclasses.replace(real(name), **ch) if name == fid else real(name)
            ),
        )
        drifted = {c.name: c.condition for c in terms_constraints()}
        assert any(migrated[name] != drifted[name] for name in drifted), (field_id, change)


def test_storage_follows_the_single_map():
    assert all(fields.STORAGE[name] == fields.TERMS for name in TERMS_IDS)
    assert fields.TERMS_FIELDS == ("asking_price", "price_basis", "rental_period", *TERMS_IDS)
    assert selectors.TERMS_FIELDS == fields.TERMS_FIELDS
