"""House and Apartment details (Story 2.4): validation, lazy storage, wire, events and drift."""

import dataclasses
from decimal import Decimal
from pathlib import Path

import pytest
from django.db import DataError, IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader

from apps.catalog import registry
from apps.kernel import events
from apps.listings import fields, selectors, services, validation
from apps.listings.events import ListingModelInputsChanged
from apps.listings.models import (
    ListingResidential,
    ListingRevision,
    PropertyListing,
    residential_constraints,
)
from tests.listings.support import (
    APARTMENT,
    APARTMENT_FIELDS,
    BOTH_FIELDS,
    HOUSE,
    HOUSE_FIELDS,
    RESIDENTIAL_IDS,
    make_account,
    random_email,
    seed_cap,
    user,
)

D = Decimal


# Story 2.5 widened these (same ids and columns) to Shops and Offices.
SHOP_AND_OFFICE = ("shop", "office")
SHARED_WITH_COMMERCIAL = {
    "condition": ("house", "apartment", *SHOP_AND_OFFICE),
    "furnishing": ("house", "apartment", *SHOP_AND_OFFICE),
    "parking_spaces": ("house", "apartment", *SHOP_AND_OFFICE),
    "occupancy_status": ("house", "apartment", *SHOP_AND_OFFICE),
    "floor_number": ("apartment", *SHOP_AND_OFFICE),
    "building_floors": ("apartment", *SHOP_AND_OFFICE),
    "lift": ("apartment", "office"),
    "building_security": ("apartment", *SHOP_AND_OFFICE),
    "building_lobby": ("apartment", *SHOP_AND_OFFICE),
    "maintenance_charges": ("apartment", *SHOP_AND_OFFICE),
}
SHOP_FIELDS = {n for n, subtypes in SHARED_WITH_COMMERCIAL.items() if "shop" in subtypes}
OFFICE_FIELDS = {n for n, subtypes in SHARED_WITH_COMMERCIAL.items() if "office" in subtypes}


@pytest.fixture(autouse=True)
def cap(db):
    seed_cap()


def draft(kind=HOUSE, account=None):
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


def save(updates, kind=HOUSE):
    account, view = draft(kind)
    return account, update(account, view, updates)


def stored():
    return ListingResidential.objects.get()


# --- the registry definitions -------------------------------------------------------------------


def test_the_registry_defines_the_catalogue_with_the_specified_rules():
    both = ("house", "apartment")
    for name, low, high, requirement in [
        ("bedrooms", 0, 50, "required_for_publication"),
        ("bathrooms", 0, 50, "required_for_publication"),
        ("construction_year", 1900, 2100, "recommended"),
        ("parking_spaces", 0, 50, "recommended"),
        ("total_floors", 1, 200, "recommended"),
        ("kitchens", 0, 50, "recommended"),
        ("floor_number", 0, 200, "recommended"),
        ("building_floors", 1, 200, "recommended"),
        *((n, 0, 50, "optional") for n in HOUSE_FIELDS[3:12]),
    ]:
        definition = registry.field(name)
        assert (definition.type, definition.minimum, definition.maximum) == (
            "integer",
            D(low),
            D(high),
        ), name
        assert definition.requirement == requirement, name
    for name in BOTH_FIELDS:
        wide = SHARED_WITH_COMMERCIAL.get(name)
        assert registry.field(name).applicability.subtypes == (wide or both), name
    for name in HOUSE_FIELDS:
        assert registry.field(name).applicability.subtypes == ("house",), name
    for name in APARTMENT_FIELDS:
        wide = SHARED_WITH_COMMERCIAL.get(name)
        assert registry.field(name).applicability.subtypes == (wide or ("apartment",)), name
    assert registry.field("condition").allowed_values == (
        "new",
        "excellent",
        "good",
        "needs_renovation",
    )
    assert registry.field("furnishing").allowed_values == (
        "unfurnished",
        "semi_furnished",
        "furnished",
    )
    assert registry.field("occupancy_status").allowed_values == (
        "owner_occupied",
        "tenant_occupied",
        "vacant",
    )
    assert registry.field("construction_status").allowed_values == (
        "completed",
        "under_construction",
        "grey_structure",
    )
    covered = registry.field("covered_area_sqft")
    assert (covered.type, covered.scale, covered.minimum, covered.maximum) == (
        "decimal",
        4,
        D("0.0001"),
        D("9999999999.9999"),
    )
    charges = registry.field("maintenance_charges")
    assert (charges.type, charges.currency, charges.minimum) == ("money", "PKR", D(0))
    assert charges.maximum == D("999999999999.99") and charges.requirement == "optional"
    for name in ("basement", "lift", "backup_electricity", "building_security"):
        assert registry.field(name).type == "boolean"
    assert {n for n in RESIDENTIAL_IDS if registry.field(n).searchable} == {
        "bedrooms",
        "bathrooms",
        "furnishing",
        "parking_spaces",
    }
    for name in RESIDENTIAL_IDS:
        definition = registry.field(name)
        assert definition.field_class == "public" and definition.ai_extractable, name
        assert not definition.derived and not definition.sortable, name


# --- accept ------------------------------------------------------------------------------------

HOUSE_SET = {
    "bedrooms": 4,
    "bathrooms": 3,
    "construction_year": 2015,
    "condition": "good",
    "furnishing": "semi_furnished",
    "parking_spaces": 2,
    "occupancy_status": "vacant",
    "covered_area_sqft": "2400.5",
    "total_floors": 2,
    "kitchens": 1,
    "construction_status": "completed",
    "drawing_rooms": 1,
    "dining_rooms": 1,
    "tv_lounges": 1,
    "study_rooms": 0,
    "prayer_rooms": 1,
    "store_rooms": 2,
    "laundry_rooms": 1,
    "powder_rooms": 1,
    "servant_quarters": 1,
    "basement": False,
}
APARTMENT_SET = {
    "bedrooms": 2,
    "bathrooms": 2,
    "floor_number": 5,
    "building_floors": 12,
    "lift": True,
    "backup_electricity": False,
    "building_security": True,
    "balcony_or_terrace": True,
    "building_lobby": False,
    "intercom": True,
    "maintenance_charges": "5000",
}


def test_a_house_saves_every_field_and_the_wire_shows_native_types():
    account, saved = save(HOUSE_SET)
    assert saved.revision == 2
    assert saved.fields["bedrooms"] == 4 and type(saved.fields["bedrooms"]) is int
    assert saved.fields["basement"] is False
    assert saved.fields["study_rooms"] == 0 and type(saved.fields["study_rooms"]) is int
    assert saved.fields["covered_area_sqft"] == "2400.5000"
    assert saved.fields["condition"] == "good"
    for name in APARTMENT_FIELDS:
        assert saved.fields[name] is None, name
    assert {k: dict(v) for k, v in saved.provenance.items()} == {
        name: {"origin": "manual", "confirmed": True} for name in HOUSE_SET
    }
    row = stored()
    assert row.bedrooms == 4 and row.basement is False and row.covered_area_sqft == D("2400.5")
    assert selectors.get_owned_listing(user(account), saved.id) == saved
    changes = ListingRevision.objects.get(revision=2).changes
    assert changes["bedrooms"] == 4 and changes["basement"] is False
    assert changes["covered_area_sqft"] == "2400.5000"


def test_an_apartment_saves_every_field():
    _, saved = save(APARTMENT_SET, APARTMENT)
    assert saved.fields["floor_number"] == 5 and saved.fields["lift"] is True
    assert saved.fields["backup_electricity"] is False
    assert saved.fields["maintenance_charges"] == "5000.00"
    for name in HOUSE_FIELDS:
        assert saved.fields[name] is None, name
    assert stored().maintenance_charges == D("5000.00")


def test_matrix_example_values():
    _, saved = save({"bedrooms": 4, "bathrooms": 3, "total_floors": 2, "kitchens": 1})
    assert [saved.fields[n] for n in ("bedrooms", "bathrooms", "total_floors", "kitchens")] == [
        4,
        3,
        2,
        1,
    ]
    _, saved = save({"floor_number": 5, "building_floors": 12, "lift": True}, APARTMENT)
    assert (saved.fields["floor_number"], saved.fields["lift"]) == (5, True)


def test_zero_is_valid_when_given_and_the_bounds_are_inclusive():
    _, saved = save({"bedrooms": 0, "bathrooms": 50, "construction_year": 1900})
    assert (saved.fields["bedrooms"], saved.fields["bathrooms"]) == (0, 50)
    _, saved = save({"construction_year": 2100, "total_floors": 200, "kitchens": 0})
    assert saved.fields["construction_year"] == 2100 and saved.fields["total_floors"] == 200
    _, saved = save({"floor_number": 0, "building_floors": 1}, APARTMENT)
    assert saved.fields["floor_number"] == 0 and saved.fields["building_floors"] == 1


# --- reject ------------------------------------------------------------------------------------

INTEGERS = [n for n in RESIDENTIAL_IDS if registry.field(n).type == "integer"]
BOOLEANS = [n for n in RESIDENTIAL_IDS if registry.field(n).type == "boolean"]
ENUMS = [n for n in RESIDENTIAL_IDS if registry.field(n).type == "enum"]
NOT_INTEGERS = ["3", "", "3.0", 3.0, 3.5, True, False, [], {}, [3], "three", 1e3]
NOT_BOOLEANS = ["yes", "true", "false", "", 1, 0, 1.0, [], {}, [True]]


@pytest.mark.parametrize("name", INTEGERS)
def test_every_integer_field_refuses_wrong_types_and_out_of_range(name):
    definition = registry.field(name)
    kind = HOUSE if "house" in definition.applicability.subtypes else APARTMENT
    account, view = draft(kind)
    for bad in NOT_INTEGERS:
        assert codes(account, view, {name: bad}) == {name: "invalid"}, repr(bad)
    low, high = int(definition.minimum), int(definition.maximum)
    for bad in (low - 1, high + 1, -1, 10**30):
        assert codes(account, view, {name: bad}) == {name: "invalid_range"}, bad
    saved = update(account, view, {name: low})
    assert saved.fields[name] == low
    assert update(account, saved, {name: high}).fields[name] == high


@pytest.mark.parametrize("name", BOOLEANS)
def test_every_boolean_field_refuses_everything_but_true_and_false(name):
    kind = HOUSE if "house" in registry.field(name).applicability.subtypes else APARTMENT
    account, view = draft(kind)
    for bad in NOT_BOOLEANS:
        assert codes(account, view, {name: bad}) == {name: "invalid"}, repr(bad)
    for good in (True, False):
        saved = update(account, view, {name: good})
        assert saved.fields[name] is good
        view = saved


@pytest.mark.parametrize("name", ENUMS)
def test_every_enum_field_accepts_its_values_and_refuses_others(name):
    definition = registry.field(name)
    kind = HOUSE if "house" in definition.applicability.subtypes else APARTMENT
    account, view = draft(kind)
    for bad in ("mint", "", "GOOD", " good", 3, True, ["good"]):
        expected = "invalid_choice" if isinstance(bad, str) else "invalid"
        assert codes(account, view, {name: bad}) == {name: expected}, repr(bad)
    for value in definition.allowed_values:
        view = update(account, view, {name: value})
        assert view.fields[name] == value


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("2400", None),
        ("0.0001", None),
        ("9999999999.9999", None),
        ("0", "invalid_area"),
        ("-5", "invalid_area"),
        ("0.00001", "invalid_area"),
        ("10000000000", "invalid_area"),
        ("0010", "invalid"),
        (" 5", "invalid"),
        ("1,5", "invalid"),
        ("abc", "invalid"),
        ("", "invalid"),
        ("1e3", "invalid"),
        (5, "invalid"),
        (5.5, "invalid"),
        (True, "invalid"),
    ],
)
def test_covered_area_follows_the_decimal_rules(value, code):
    account, view = draft()
    if code is None:
        assert (
            update(account, view, {"covered_area_sqft": value}).fields["covered_area_sqft"]
            == f"{D(value):.4f}"
        )
    else:
        assert codes(account, view, {"covered_area_sqft": value}) == {"covered_area_sqft": code}


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("0", None),
        ("0.00", None),
        ("1500.5", "1500.50"),
        ("999999999999.99", None),
        ("-1", "invalid_price"),
        ("0.001", "invalid_price"),
        ("1000000000000", "invalid_price"),
        ("abc", "invalid"),
        (True, "invalid"),
        (1.5, "invalid"),
    ],
)
def test_maintenance_charges_follow_the_money_rules(value, code):
    account, view = draft(APARTMENT)
    if code in (None, "1500.50"):
        saved = update(account, view, {"maintenance_charges": value})
        assert saved.fields["maintenance_charges"] == (code or f"{D(value):.2f}")
    else:
        assert codes(account, view, {"maintenance_charges": value}) == {"maintenance_charges": code}


def test_zero_maintenance_charges_are_a_stored_answer_not_unknown():
    _, saved = save({"maintenance_charges": "0"}, APARTMENT)
    assert saved.fields["maintenance_charges"] == "0.00" and stored().maintenance_charges == D(0)


def test_covered_area_may_exceed_the_listing_area():
    _, saved = save({"area_value": "100", "area_unit": "sqft", "covered_area_sqft": "5000"})
    assert saved.fields["covered_area_sqft"] == "5000.0000"
    assert saved.fields["area_sqft"] == "100.0000"


def test_covered_area_never_depends_on_the_area_unit():
    _, saved = save({"area_value": "5", "area_unit": "marla", "covered_area_sqft": "1000"})
    assert saved.fields["covered_area_sqft"] == "1000.0000"
    assert saved.fields["area_sqft"] is None  # marla without a basis: still unknown


def test_all_failures_are_reported_at_once_and_nothing_is_written():
    account, view = draft()
    assert codes(
        account, view, {"bedrooms": 99, "kitchens": "2", "basement": "yes", "title": "ok title one"}
    ) == {"bedrooms": "invalid_range", "kitchens": "invalid", "basement": "invalid"}
    assert ListingResidential.objects.count() == 0
    assert PropertyListing.objects.get().revision == 1


# --- applicability -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "allowed"),
    [
        (HOUSE, {*BOTH_FIELDS, *HOUSE_FIELDS}),
        (APARTMENT, {*BOTH_FIELDS, *APARTMENT_FIELDS}),
        (("sale", "plot", "residential_plot"), set()),
        (("sale", "plot", "commercial_plot"), set()),
        (("rent", "plot", "plot_file"), set()),
        (("rent", "commercial", "shop"), SHOP_FIELDS),
        (("rent", "commercial", "office"), OFFICE_FIELDS),
    ],
)
def test_the_not_applicable_matrix(kind, allowed):
    account, view = draft(kind)
    good = {
        "integer": 1,
        "boolean": True,
        "enum": None,
        "decimal": "10",
        "money": "10",
    }
    for name in RESIDENTIAL_IDS:
        definition = registry.field(name)
        value = good[definition.type]
        if definition.type == "enum":
            value = definition.allowed_values[0]
        if name == "construction_year":
            value = 2000
        if name in allowed:
            assert codes_or_none(account, view, {name: value}) is None, name
            view = selectors.get_owned_listing(user(account), view.id)
        else:
            assert codes(account, view, {name: value}) == {name: "not_applicable"}, name
    assert PropertyListing.objects.get().revision == view.revision


def codes_or_none(account, view, updates):
    try:
        update(account, view, updates)
    except services.FieldsInvalid as caught:
        return caught.fields
    return None


def test_a_wrong_subtype_judges_applicability_before_the_value():
    account, view = draft(APARTMENT)
    assert codes(account, view, {"kitchens": "garbage"}) == {"kitchens": "not_applicable"}
    account, view = draft(HOUSE, make_account("john.roe@example.com"))
    assert codes(account, view, {"lift": "garbage"}) == {"lift": "not_applicable"}


def test_clearing_a_field_that_does_not_apply_is_a_harmless_no_op():
    account, view = draft(APARTMENT)
    saved = update(account, view, {"kitchens": None})
    assert saved.revision == 1 and ListingResidential.objects.count() == 0


def test_the_right_set_beside_the_wrong_one_reports_only_the_wrong_field():
    account, view = draft(APARTMENT)
    assert codes(account, view, {"bedrooms": 2, "kitchens": 1, "lift": True}) == {
        "kitchens": "not_applicable"
    }
    assert ListingResidential.objects.count() == 0


# --- lazy creation, null and clears -------------------------------------------------------------


def test_a_new_draft_has_no_residential_row_and_every_detail_is_null():
    _, view = draft()
    assert ListingResidential.objects.count() == 0
    assert all(view.fields[name] is None for name in RESIDENTIAL_IDS)
    assert all(name not in view.provenance for name in RESIDENTIAL_IDS)


def test_other_edits_and_reads_never_create_the_row():
    account, view = draft()
    saved = update(account, view, {"title": "Family house near the park"})
    selectors.get_owned_listing(user(account), saved.id)
    selectors.list_owned(user(account))
    assert ListingResidential.objects.count() == 0
    assert all(saved.fields[name] is None for name in RESIDENTIAL_IDS)


def test_clearing_a_never_set_field_does_not_create_the_row_or_a_revision():
    account, view = draft()
    saved = update(account, view, {"bedrooms": None, "lift": None, "covered_area_sqft": None})
    assert saved.revision == 1 and ListingResidential.objects.count() == 0
    assert ListingRevision.objects.count() == 1


def test_clears_together_with_other_changes_never_create_the_row():
    account, view = draft()
    saved = update(account, view, {"bedrooms": None, "title": "Family house near the park"})
    assert saved.revision == 2 and ListingResidential.objects.count() == 0


def test_the_first_non_null_value_creates_the_row_once():
    account, view = draft()
    saved = update(account, view, {"bedrooms": 3})
    assert ListingResidential.objects.count() == 1
    first = stored()
    saved = update(account, saved, {"bathrooms": 2})
    assert ListingResidential.objects.count() == 1 and stored().pk == first.pk
    assert (stored().bedrooms, stored().bathrooms) == (3, 2)
    assert saved.revision == 3


def test_a_null_together_with_a_value_creates_the_row_with_only_the_value():
    _, saved = save({"bedrooms": 3, "bathrooms": None, "lift": None})
    row = stored()
    assert row.bedrooms == 3 and row.bathrooms is None and row.basement is None
    assert saved.fields["bathrooms"] is None


def test_unanswered_is_null_never_zero_or_false():
    _, saved = save({"bedrooms": 3})
    row = stored()
    for name in RESIDENTIAL_IDS:
        if name != "bedrooms":
            assert getattr(row, name) is None, name
            assert saved.fields[name] is None, name
    assert saved.fields["basement"] is None and saved.fields["kitchens"] is None


def test_clearing_sets_the_column_null_removes_provenance_and_advances_the_revision():
    account, view = draft()
    saved = update(account, view, {"kitchens": 2, "basement": True})
    cleared = update(account, saved, {"kitchens": None})
    assert cleared.revision == 3 and cleared.fields["kitchens"] is None
    assert "kitchens" not in cleared.provenance and "basement" in cleared.provenance
    assert stored().kitchens is None and stored().basement is True
    assert ListingRevision.objects.get(revision=3).changes == {"kitchens": None}
    cleared = update(account, cleared, {"basement": None})
    assert stored().basement is None and cleared.fields["basement"] is None
    assert ListingResidential.objects.count() == 1  # the row stays once it exists


def test_false_and_zero_are_changes_from_unknown():
    account, view = draft()
    saved = update(account, view, {"basement": False, "kitchens": 0})
    assert saved.revision == 2
    assert saved.fields["basement"] is False and saved.fields["kitchens"] == 0
    assert update(account, saved, {"basement": False, "kitchens": 0}).revision == 2  # no-op


def test_an_unchanged_save_writes_nothing():
    account, view = draft()
    saved = update(account, view, {"bedrooms": 3})
    stamp = stored().updated_at
    again = update(account, saved, {"bedrooms": 3})
    assert again.revision == saved.revision and stored().updated_at == stamp
    assert ListingRevision.objects.count() == 2


def test_resaving_an_ai_value_manually_confirms_it():
    account, view = draft()
    saved = update(account, view, {"bedrooms": 3}, origin="ai")
    assert dict(saved.provenance["bedrooms"]) == {"origin": "ai", "confirmed": False}
    confirmed = update(account, saved, {"bedrooms": 3})
    assert confirmed.revision == 3
    assert dict(confirmed.provenance["bedrooms"]) == {"origin": "manual", "confirmed": True}


def test_booleans_and_integers_never_collide_in_change_detection():
    account, view = draft()
    saved = update(account, view, {"basement": True, "kitchens": 1})
    again = update(account, saved, {"basement": True, "kitchens": 1})
    assert again.revision == saved.revision
    flipped = update(account, saved, {"basement": False})
    assert flipped.revision == saved.revision + 1 and flipped.fields["basement"] is False


# --- the floor rule ----------------------------------------------------------------------------


def test_floor_above_the_building_is_refused():
    account, view = draft(APARTMENT)
    assert codes(account, view, {"floor_number": 10, "building_floors": 5}) == {
        "floor_number": "invalid_floor",
        "building_floors": "invalid_floor",
    }
    assert ListingResidential.objects.count() == 0


def test_the_floor_rule_uses_the_resulting_state():
    account, view = draft(APARTMENT)
    saved = update(account, view, {"floor_number": 4, "building_floors": 8})
    assert codes(account, saved, {"building_floors": 3}) == {
        "floor_number": "invalid_floor",
        "building_floors": "invalid_floor",
    }
    assert codes(account, saved, {"floor_number": 9}) == {"floor_number": "invalid_floor"}
    both = update(account, saved, {"floor_number": 2, "building_floors": 2})  # equal is fine
    assert (both.fields["floor_number"], both.fields["building_floors"]) == (2, 2)
    moved = update(account, both, {"floor_number": 1, "building_floors": 1})
    assert moved.fields["building_floors"] == 1
    cleared = update(account, moved, {"building_floors": None})  # unknown building: no rule
    assert cleared.fields["building_floors"] is None
    assert update(account, cleared, {"floor_number": 99}).fields["floor_number"] == 99


def test_ground_floor_zero_is_never_above_a_building():
    _, saved = save({"floor_number": 0, "building_floors": 1}, APARTMENT)
    assert saved.fields["floor_number"] == 0


def test_a_floor_failure_alongside_a_range_failure_reports_the_range_code_only():
    account, view = draft(APARTMENT)
    assert codes(account, view, {"floor_number": 300, "building_floors": 5}) == {
        "floor_number": "invalid_range"
    }


def drop_constraint(name):
    """Stored data from an older, wider registry: the test drops the CHECK (rolled back with it)."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")  # settle the deferred foreign key checks
        cursor.execute(f"ALTER TABLE listings_listingresidential DROP CONSTRAINT {name}")


def test_an_unrelated_edit_does_not_rejudge_stored_floors():
    account, view = draft(APARTMENT)
    saved = update(account, view, {"floor_number": 3, "building_floors": 9})
    drop_constraint("listings_res_floor_within_building")
    ListingResidential.objects.update(floor_number=5, building_floors=3)  # violates the rule
    after = update(account, saved, {"title": "Family house near the park", "lift": True})
    assert after.revision == 3
    assert (after.fields["floor_number"], after.fields["building_floors"]) == (5, 3)
    values, errors = validation.evaluate(
        "sale",
        "apartment",
        "apartment",
        {"floor_number": 5, "building_floors": 3},
        {"title": "Family house near the park", "bedrooms": 2},
    )
    assert errors == {}
    # touching one of the two judges the resulting state again
    assert codes(account, after, {"floor_number": 5}) == {"floor_number": "invalid_floor"}


def test_a_null_on_a_not_applicable_field_is_a_200_no_op():
    account, view = draft(APARTMENT)
    assert update(account, view, {"kitchens": None, "basement": None}).revision == 1
    saved = update(account, view, {"bedrooms": 2})
    again = update(account, saved, {"kitchens": None})
    assert again.revision == saved.revision == 2
    assert ListingRevision.objects.count() == 2


def test_a_null_clears_a_value_stored_under_an_older_registry():
    account, view = draft(APARTMENT)
    saved = update(account, view, {"bedrooms": 2})
    ListingResidential.objects.update(kitchens=2)  # a house field stored on an apartment
    cleared = update(account, saved, {"kitchens": None})
    assert cleared.revision == 3 and stored().kitchens is None
    assert ListingRevision.objects.get(revision=3).changes == {"kitchens": None}


def test_a_stored_out_of_bounds_value_resent_unchanged_is_invalid_range():
    account, view = draft()
    saved = update(account, view, {"bedrooms": 3})
    drop_constraint("listings_res_bedrooms_range")
    ListingResidential.objects.update(bedrooms=60)  # allowed by an older, wider registry
    assert codes(account, saved, {"bedrooms": 60}) == {"bedrooms": "invalid_range"}
    assert stored().bedrooms == 60 and PropertyListing.objects.get().revision == 2
    assert update(account, saved, {"bedrooms": 5}).fields["bedrooms"] == 5


def test_the_rule_is_for_apartments_only():
    judged = validation.evaluate(
        "sale", "house", "house", {"floor_number": 9, "building_floors": 2}, {"kitchens": 1}
    )
    assert judged[1] == {}
    values, errors = validation.evaluate(
        "sale", "house", "house", {}, {"floor_number": 10, "building_floors": 5}
    )
    assert errors == {"floor_number": "not_applicable", "building_floors": "not_applicable"}


# --- the event ---------------------------------------------------------------------------------


@pytest.fixture
def bus(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    seen = []
    events.subscribe(ListingModelInputsChanged, seen.append)
    return seen


@pytest.mark.parametrize(
    "updates",
    [
        {"bedrooms": 3},
        {"bathrooms": 2},
        {"construction_year": 2010},
        {"condition": "new"},
        {"covered_area_sqft": "1000"},
        {"bedrooms": 3, "bathrooms": 2, "condition": "good"},  # one event per save
    ],
)
def test_a_valuation_input_emits_one_event_after_commit(
    bus, django_capture_on_commit_callbacks, updates
):
    account, view = draft()
    with django_capture_on_commit_callbacks(execute=True):
        update(account, view, updates)
        assert bus == []
    assert len(bus) == 1 and bus[0].listing_id == view.id


@pytest.mark.parametrize(
    "updates",
    [
        {"furnishing": "furnished"},
        {"parking_spaces": 2},
        {"occupancy_status": "vacant"},
        {"kitchens": 2},
        {"basement": True},
        {"total_floors": 3, "construction_status": "grey_structure"},
    ],
)
def test_other_details_emit_nothing(bus, django_capture_on_commit_callbacks, updates):
    account, view = draft()
    with django_capture_on_commit_callbacks(execute=True):
        update(account, view, updates)
    assert bus == []


def test_changing_clearing_and_repeating_a_model_input(bus, django_capture_on_commit_callbacks):
    account, view = draft()
    with django_capture_on_commit_callbacks(execute=True):
        saved = update(account, view, {"bedrooms": 3})
    assert len(bus) == 1
    bus.clear()
    with django_capture_on_commit_callbacks(execute=True):
        update(account, saved, {"bedrooms": 3})  # no change
        saved = update(account, saved, {"furnishing": "furnished"})
    assert bus == []
    with django_capture_on_commit_callbacks(execute=True):
        update(account, saved, {"bedrooms": None})  # a clear changes a model input
    assert len(bus) == 1


def test_a_failure_after_the_write_rolls_the_row_back_and_emits_nothing(
    bus, monkeypatch, django_capture_on_commit_callbacks
):
    account, view = draft()

    def boom(*args, **kwargs):
        raise RuntimeError("after the write")

    monkeypatch.setattr(ListingRevision.objects, "create", boom)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(RuntimeError):
            update(account, view, {"bedrooms": 3})
    assert ListingResidential.objects.count() == 0
    assert PropertyListing.objects.get().revision == 1 and callbacks == [] and bus == []


# --- concurrency, owners, older registry ---------------------------------------------------------


def test_a_stale_base_revision_is_refused_with_the_current_view_and_writes_nothing():
    account, view = draft()
    saved = update(account, view, {"bedrooms": 3})
    with pytest.raises(services.StaleRevision) as caught:
        update(account, view, {"bedrooms": 9}, base=1)
    assert caught.value.current == saved and caught.value.current.fields["bedrooms"] == 3
    assert stored().bedrooms == 3


def test_a_stale_first_save_does_not_create_the_row():
    account, view = draft()
    update(account, view, {"title": "Family house near the park"})
    with pytest.raises(services.StaleRevision):
        update(account, view, {"bedrooms": 3}, base=1)
    assert ListingResidential.objects.count() == 0


def test_another_owner_gets_not_found_and_no_row():
    _, view = draft()
    other = make_account("john.roe@example.com")
    with pytest.raises(services.DomainError) as caught:
        services.apply_updates(user(other), view.id, 1, {"bedrooms": 3})
    assert caught.value.code == "not_found" and ListingResidential.objects.count() == 0


def test_a_draft_from_an_older_registry_is_revalidated_under_version_5():
    account, view = draft()
    PropertyListing.objects.update(registry_version=3)
    assert codes(account, view, {"bedrooms": 51}) == {"bedrooms": "invalid_range"}
    assert PropertyListing.objects.get().registry_version == 3
    saved = update(account, view, {"bedrooms": 4})
    assert saved.registry_version == registry.registry_version() == 5
    assert PropertyListing.objects.get().registry_version == 5


def test_a_changed_registry_bound_is_judged_on_the_next_save(monkeypatch):
    account, view = draft()
    real = registry.field
    monkeypatch.setattr(
        registry,
        "field",
        lambda name: (
            dataclasses.replace(real(name), maximum=D(10)) if name == "bedrooms" else real(name)
        ),
    )
    assert codes(account, view, {"bedrooms": 11}) == {"bedrooms": "invalid_range"}
    assert update(account, view, {"bedrooms": 10}).fields["bedrooms"] == 10


def test_the_view_is_unchanged_by_a_later_unrelated_read_path():
    account, saved = save({"bedrooms": 3, "lift": None})
    listed = selectors.list_owned(user(account)).items[0]
    assert listed == saved


# --- the pure validators ------------------------------------------------------------------------


def test_the_validators_do_not_copy_or_coerce_values():
    definition = registry.field("bedrooms")
    assert validation.check_integer(definition, 3) == (3, None)
    assert validation.check_integer(definition, True) == (None, "invalid")
    assert validation.check_integer(definition, 3.0) == (None, "invalid")
    assert validation.check_integer(definition, "3") == (None, "invalid")
    assert validation.check_integer(definition, 51) == (None, "invalid_range")
    assert validation.check_boolean(True) == (True, None)
    assert validation.check_boolean(0) == (None, "invalid")
    assert validation.check_boolean("true") == (None, "invalid")


def test_no_float_arithmetic_in_the_listing_modules():
    for module in (validation, selectors, services):
        assert "float(" not in Path(module.__file__).read_text(), module.__name__


# --- constraints and drift -----------------------------------------------------------------------


def listing_row():
    return PropertyListing.objects.create(
        owner_account_id=make_account(random_email()).id,
        status="draft",
        revision=1,
        registry_version=5,
        purpose="sale",
        category="apartment",
        subtype="apartment",
    )


def residential(listing=None, **values):
    return ListingResidential.objects.create(listing=listing or listing_row(), **values)


def _migrated():
    state = MigrationLoader(connection).project_state()
    options = state.models[("listings", "listingresidential")].options
    return {c.name: c.condition for c in options["constraints"]}


def test_every_non_boolean_field_has_a_named_constraint():
    names = {c.name for c in residential_constraints()}
    for name in RESIDENTIAL_IDS:
        suffix = {"enum": "valid", "boolean": None}.get(registry.field(name).type, "range")
        if suffix:
            assert f"listings_res_{name}_{suffix}" in names, name
    assert "listings_res_floor_within_building" in names
    booleans = [n for n in RESIDENTIAL_IDS if registry.field(n).type == "boolean"]
    assert len(names) == len(RESIDENTIAL_IDS) - len(booleans) + 1


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"bedrooms": -1}, "listings_res_bedrooms_range"),
        ({"bedrooms": 51}, "listings_res_bedrooms_range"),
        ({"bathrooms": 51}, "listings_res_bathrooms_range"),
        ({"construction_year": 1899}, "listings_res_construction_year_range"),
        ({"construction_year": 2101}, "listings_res_construction_year_range"),
        ({"condition": "mint"}, "listings_res_condition_valid"),
        ({"furnishing": "full"}, "listings_res_furnishing_valid"),
        ({"occupancy_status": "empty"}, "listings_res_occupancy_status_valid"),
        ({"construction_status": "done"}, "listings_res_construction_status_valid"),
        ({"parking_spaces": 51}, "listings_res_parking_spaces_range"),
        ({"total_floors": 0}, "listings_res_total_floors_range"),
        ({"total_floors": 201}, "listings_res_total_floors_range"),
        ({"kitchens": 51}, "listings_res_kitchens_range"),
        ({"servant_quarters": -1}, "listings_res_servant_quarters_range"),
        ({"floor_number": 201, "building_floors": 200}, "listings_res_floor_number_range"),
        ({"floor_number": -1}, "listings_res_floor_number_range"),
        ({"building_floors": 0}, "listings_res_building_floors_range"),
        ({"covered_area_sqft": "0"}, "listings_res_covered_area_sqft_range"),
        ({"covered_area_sqft": "-1"}, "listings_res_covered_area_sqft_range"),
        ({"maintenance_charges": "-0.01"}, "listings_res_maintenance_charges_range"),
        ({"floor_number": 5, "building_floors": 4}, "listings_res_floor_within_building"),
    ],
)
def test_constraints_name_themselves(db, overrides, constraint):
    with pytest.raises(IntegrityError, match=constraint), transaction.atomic():
        residential(**overrides)
    assert ListingResidential.objects.count() == 0


def test_over_the_column_width_is_a_data_error(db):
    with pytest.raises(DataError), transaction.atomic():
        residential(covered_area_sqft="10000000000")


def test_the_bounds_and_nulls_are_accepted(db):
    residential(
        bedrooms=0,
        bathrooms=50,
        construction_year=1900,
        covered_area_sqft="9999999999.9999",
        maintenance_charges="0",
        floor_number=0,
        building_floors=1,
        lift=False,
        basement=None,
    )
    ListingResidential.objects.all().delete()
    assert residential(floor_number=7).pk  # unknown building: no rule
    assert residential(listing=listing_row(), construction_year=2100, total_floors=200).pk


def test_one_row_per_listing_and_nulls_are_the_default(db):
    row = listing_row()
    created = residential(listing=row)
    assert all(getattr(created, name) is None for name in RESIDENTIAL_IDS)
    with pytest.raises(IntegrityError), transaction.atomic():
        residential(listing=row)


def test_the_column_types_in_the_database(db):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, data_type, numeric_precision, numeric_scale "
            "FROM information_schema.columns WHERE table_name = 'listings_listingresidential' "
            "AND column_name = ANY(%s)",
            [list(RESIDENTIAL_IDS)],
        )
        found = {row[0]: row[1:] for row in cursor.fetchall()}
    assert set(found) == set(RESIDENTIAL_IDS)
    assert found["covered_area_sqft"] == ("numeric", 14, 4)
    assert found["maintenance_charges"] == ("numeric", 14, 2)
    for name in RESIDENTIAL_IDS:
        kind = registry.field(name).type
        if kind == "boolean":
            assert found[name][0] == "boolean", name
        elif kind == "integer":
            assert found[name][0] == "integer", name


def test_the_migration_matches_the_models_and_the_registry(db):
    migrated = _migrated()
    assert migrated == {c.name: c.condition for c in ListingResidential._meta.constraints}
    assert migrated == {c.name: c.condition for c in residential_constraints()}
    assert "50" in repr(migrated["listings_res_bedrooms_range"])
    assert "1900" in repr(migrated["listings_res_construction_year_range"])
    assert "2100" in repr(migrated["listings_res_construction_year_range"])
    assert "['new', 'excellent', 'good', 'needs_renovation']" in repr(
        migrated["listings_res_condition_valid"]
    )
    assert "9999999999.9999" in repr(migrated["listings_res_covered_area_sqft_range"])
    assert "999999999999.99" in repr(migrated["listings_res_maintenance_charges_range"])


def test_a_changed_bound_or_enum_would_differ_from_the_migration(db, monkeypatch):
    migrated = _migrated()
    real = registry.field
    changes = {
        "bedrooms": {"maximum": D(40)},
        "total_floors": {"minimum": D(2)},
        "construction_year": {"minimum": D(1800)},
        "condition": {"allowed_values": ("new", "good")},
        "construction_status": {"allowed_values": ("completed",)},
        "covered_area_sqft": {"maximum": D(5)},
        "maintenance_charges": {"maximum": D(5)},
    }
    for field_id, change in changes.items():
        monkeypatch.setattr(
            registry,
            "field",
            lambda name, fid=field_id, ch=change: (
                dataclasses.replace(real(name), **ch) if name == fid else real(name)
            ),
        )
        drifted = {c.name: c.condition for c in residential_constraints()}
        assert any(migrated[name] != drifted[name] for name in drifted), field_id


def test_storage_follows_the_single_map():
    assert fields.RESIDENTIAL_FIELDS == (*RESIDENTIAL_IDS, "electricity", "water", "gas")
    assert all(fields.STORAGE[name] == fields.RESIDENTIAL for name in fields.RESIDENTIAL_FIELDS)
