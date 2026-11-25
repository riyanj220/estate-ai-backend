"""Plot, Plot File, Shop and Office details (Story 2.5): validation, lazy storage, wire, events and
drift. Values are low-entropy fake words and exact decimals; nothing here uses floats as data
under test except to prove a JSON number is refused."""

import dataclasses
from decimal import Decimal

import pytest
from django.db import DataError, IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader

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
    PropertyListing,
    commercial_constraints,
    plot_constraints,
)
from tests.listings.support import (
    APARTMENT,
    COMMERCIAL_FIELDS,
    COMMERCIAL_PLOT,
    DETAIL_IDS,
    HOUSE,
    OFFICE,
    PLOT_FIELDS,
    PLOT_FILE,
    RESIDENTIAL_IDS,
    RESIDENTIAL_PLOT,
    SHOP,
    UTILITY_FIELDS,
    make_account,
    random_email,
    seed_cap,
    user,
)

D = Decimal

PLOTS = ("residential_plot", "commercial_plot", "plot_file")
COMMERCIAL = ("shop", "office")
EVERY_SUBTYPE = ("house", "apartment", *PLOTS, *COMMERCIAL)
KINDS = {
    "house": HOUSE,
    "apartment": APARTMENT,
    "residential_plot": RESIDENTIAL_PLOT,
    "commercial_plot": COMMERCIAL_PLOT,
    "plot_file": PLOT_FILE,
    "shop": SHOP,
    "office": OFFICE,
}

# Where each new field applies, written out independently of the registry file.
APPLIES = {
    **dict.fromkeys(UTILITY_FIELDS, {*PLOTS, *COMMERCIAL}),
    **dict.fromkeys(PLOT_FIELDS, set(PLOTS)),
    "plot_file_status": {"plot_file"},
    **dict.fromkeys(COMMERCIAL_FIELDS, set(COMMERCIAL)),
    "frontage_ft": {"shop"},
    "conference_rooms": {"office"},
    "business_centre_facilities": {"office"},
}
KINDS_FOR = {name: KINDS[sorted(subtypes)[0]] for name, subtypes in APPLIES.items()}
DECIMALS = {
    # field: (minimum, maximum, scale)
    "plot_width": (D("0.01"), D("99999.99"), 2),
    "plot_depth": (D("0.01"), D("99999.99"), 2),
    "road_width_ft": (D("1.00"), D("999.99"), 2),
    "frontage_ft": (D("0.01"), D("9999.99"), 2),
    "ceiling_height_ft": (D("1.00"), D("99.99"), 2),
    "power_load_kw": (D("0.01"), D("99999.99"), 2),
}
BOOLEANS = [
    n
    for n in (*UTILITY_FIELDS, *PLOT_FIELDS, *COMMERCIAL_FIELDS)
    if registry.field(n).type == "boolean"
]
ENUMS = [
    n
    for n in (*PLOT_FIELDS, *COMMERCIAL_FIELDS)
    if registry.field(n).type == "enum" and n != "possession_status"
]
SELLER_DECLARED = {
    "possession_status",
    "balloting_status",
    "development_status",
    "approval_authority",
    "plot_file_status",
}


@pytest.fixture(autouse=True)
def cap(db):
    seed_cap()


def draft(kind=RESIDENTIAL_PLOT, account=None):
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


def save(updates, kind=RESIDENTIAL_PLOT):
    account, view = draft(kind)
    return account, update(account, view, updates)


def no_rows():
    return not (
        ListingPlot.objects.exists()
        or ListingCommercial.objects.exists()
        or ListingResidential.objects.exists()
    )


PLOT_SET = {
    "plot_width": "35",
    "plot_depth": "70.5",
    "possession_status": "possession_ready",
    "corner_plot": True,
    "road_width_ft": "40",
    "balloting_status": "balloted",
    "sewerage": False,
    "development_status": "developed",
    "approval_authority": "lda",
    "park_facing": True,
    "main_boulevard": False,
    "boundary_wall": True,
    "orientation": "north_east",
    "electricity": True,
    "water": True,
    "gas": False,
}
SHOP_SET = {
    "business_use": "retail",
    "frontage_ft": "18.25",
    "road_access": "main_road",
    "ceiling_height_ft": "12",
    "power_load_kw": "10.5",
    "three_phase_electricity": True,
    "condition": "good",
    "furnishing": "semi_furnished",
    "parking_spaces": 2,
    "occupancy_status": "vacant",
    "floor_number": 0,
    "building_floors": 3,
    "building_security": True,
    "building_lobby": False,
    "maintenance_charges": "2500",
    "electricity": True,
}
OFFICE_SET = {
    "business_use": "office_work",
    "road_access": "secondary_road",
    "conference_rooms": 3,
    "business_centre_facilities": False,
    "lift": True,
    "floor_number": 4,
    "building_floors": 9,
    "water": True,
    "gas": False,
}


# --- accept ------------------------------------------------------------------------------------


def test_a_residential_plot_saves_its_details_and_the_wire_shows_native_types():
    account, saved = save(PLOT_SET)
    assert saved.revision == 2
    assert saved.fields["plot_width"] == "35.00" and saved.fields["plot_depth"] == "70.50"
    assert saved.fields["road_width_ft"] == "40.00"
    assert saved.fields["corner_plot"] is True and saved.fields["sewerage"] is False
    assert saved.fields["possession_status"] == "possession_ready"
    assert saved.fields["approval_authority"] == "lda"
    assert saved.fields["plot_file_status"] is None
    for name in COMMERCIAL_FIELDS:
        assert saved.fields[name] is None, name
    assert {k: dict(v) for k, v in saved.provenance.items()} == {
        name: {"origin": "manual", "confirmed": True} for name in PLOT_SET
    }
    plot = ListingPlot.objects.get()
    assert plot.plot_width == D("35.00") and plot.corner_plot is True and plot.sewerage is False
    assert plot.orientation == "north_east"
    assert ListingResidential.objects.get().water is True  # the shared utilities
    assert not ListingCommercial.objects.exists()
    assert selectors.get_owned_listing(user(account), saved.id) == saved
    changes = ListingRevision.objects.get(revision=2).changes
    assert changes["plot_width"] == "35.00" and changes["corner_plot"] is True


def test_a_commercial_plot_takes_the_same_details():
    _, saved = save(
        {"plot_width": "10", "possession_status": "possession_pending"}, COMMERCIAL_PLOT
    )
    assert saved.fields["possession_status"] == "possession_pending"


def test_a_plot_file_saves_file_only_and_its_status():
    _, saved = save(
        {
            "possession_status": "file_only",
            "plot_file_status": "allocation_letter",
            "plot_width": "5",
        },
        PLOT_FILE,
    )
    assert saved.fields["possession_status"] == "file_only"
    assert saved.fields["plot_file_status"] == "allocation_letter"
    assert ListingPlot.objects.get().plot_file_status == "allocation_letter"


def test_a_shop_saves_commercial_and_shared_details():
    _, saved = save(SHOP_SET, SHOP)
    assert saved.fields["frontage_ft"] == "18.25" and saved.fields["ceiling_height_ft"] == "12.00"
    assert saved.fields["power_load_kw"] == "10.50"
    assert saved.fields["floor_number"] == 0 and saved.fields["building_floors"] == 3
    assert saved.fields["maintenance_charges"] == "2500.00" and saved.fields["parking_spaces"] == 2
    assert saved.fields["conference_rooms"] is None and saved.fields["lift"] is None
    commercial, unit = ListingCommercial.objects.get(), ListingResidential.objects.get()
    assert commercial.frontage_ft == D("18.25") and commercial.three_phase_electricity is True
    assert unit.floor_number == 0 and unit.condition == "good" and unit.electricity is True
    assert not ListingPlot.objects.exists()


def test_an_office_saves_its_details_and_the_lift():
    _, saved = save(OFFICE_SET, OFFICE)
    assert saved.fields["conference_rooms"] == 3 and type(saved.fields["conference_rooms"]) is int
    assert saved.fields["business_centre_facilities"] is False and saved.fields["lift"] is True
    assert saved.fields["frontage_ft"] is None
    assert ListingCommercial.objects.get().conference_rooms == 3


def test_bounds_are_inclusive_and_zero_is_a_real_answer():
    for name, (low, high, _) in DECIMALS.items():
        account, view = draft(KINDS_FOR[name])
        saved = update(account, view, {name: format(low, "f")})
        assert saved.fields[name] == f"{low:.2f}", name
        saved = update(account, saved, {name: format(high, "f")})
        assert saved.fields[name] == f"{high:.2f}", name
    _, saved = save({"conference_rooms": 0}, OFFICE)
    assert saved.fields["conference_rooms"] == 0
    _, saved = save({"conference_rooms": 50}, OFFICE)
    assert saved.fields["conference_rooms"] == 50


def test_decimals_are_served_at_their_scale_and_never_in_exponent_form():
    account, view = draft()
    saved = update(
        account, view, {"plot_width": "100", "plot_depth": "0.5", "road_width_ft": "1000"[:3]}
    )
    assert saved.fields["plot_width"] == "100.00" and saved.fields["plot_depth"] == "0.50"
    assert saved.fields["road_width_ft"] == "100.00"
    saved = update(account, saved, {"plot_width": "99999.99", "plot_depth": "0.01"})
    for name in ("plot_width", "plot_depth", "road_width_ft"):
        assert "E" not in saved.fields[name].upper(), name
    assert ListingRevision.objects.get(revision=3).changes == {
        "plot_width": "99999.99",
        "plot_depth": "0.01",
    }


# --- reject: shapes and ranges -----------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(DECIMALS))
def test_every_measure_follows_the_strict_decimal_rules(name):
    low, high, scale = DECIMALS[name]
    account, view = draft(KINDS_FOR[name])
    for bad in ("12,5", "abc", "", " 5", "5 ", "0010", "1e3", "1E3", "+5", ".5", "5.", "٥", "5\n"):
        assert codes(account, view, {name: bad}) == {name: "invalid"}, repr(bad)
    for bad in (5, 5.5, True, False, [], {}, ["5"], 10**30):
        assert codes(account, view, {name: bad}) == {name: "invalid"}, repr(bad)
    over = format(high + D("0.01"), "f")
    below = format(low - D("0.01"), "f") if low > D("0.01") else "0"
    for bad in ("0", "-1", "-0.5", "0.001", "1.234", over, below, "1" * 11, "9" * 25):
        if D(bad) >= low and D(bad) <= high and len(bad.partition(".")[2]) <= scale:
            continue  # a valid value for this field's bounds
        assert codes(account, view, {name: bad}) == {name: "invalid_measure"}, (name, bad)
    assert no_rows()


def test_measure_boundaries_for_each_field():
    account, view = draft()
    assert codes(account, view, {"road_width_ft": "0.99"}) == {"road_width_ft": "invalid_measure"}
    assert update(account, view, {"road_width_ft": "1"}).fields["road_width_ft"] == "1.00"
    account, view = draft(OFFICE)
    assert codes(account, view, {"ceiling_height_ft": "0.99"}) == {
        "ceiling_height_ft": "invalid_measure"
    }
    assert codes(account, view, {"ceiling_height_ft": "100"}) == {
        "ceiling_height_ft": "invalid_measure"
    }
    assert codes(account, view, {"power_load_kw": "100000"}) == {"power_load_kw": "invalid_measure"}
    account, view = draft(SHOP)
    assert codes(account, view, {"frontage_ft": "10000"}) == {"frontage_ft": "invalid_measure"}


def test_the_area_fields_keep_their_own_code():
    account, view = draft(HOUSE)
    assert codes(account, view, {"covered_area_sqft": "0"}) == {"covered_area_sqft": "invalid_area"}
    assert validation.decimal_code(registry.field("area_value")) == "invalid_area"
    assert validation.decimal_code(registry.field("plot_width")) == "invalid_measure"
    message = field_message("invalid_measure")
    assert message == (
        "Enter a number above zero within the allowed range, "
        "using digits and an optional decimal point."
    )
    assert "feet" not in message and "kilowatt" not in message
    assert registry.field("power_load_kw").error_code == "invalid_measure"
    assert registry.field("area_value").error_code == "invalid_area"


@pytest.mark.parametrize("name", BOOLEANS)
def test_every_boolean_field_refuses_everything_but_true_and_false(name):
    account, view = draft(KINDS_FOR[name])
    for bad in ("yes", "true", "false", "", 1, 0, 1.0, [], {}, [True]):
        assert codes(account, view, {name: bad}) == {name: "invalid"}, repr(bad)
    for good in (True, False):
        view = update(account, view, {name: good})
        assert view.fields[name] is good


@pytest.mark.parametrize("name", ENUMS)
def test_every_enum_field_accepts_its_values_and_refuses_others(name):
    definition = registry.field(name)
    account, view = draft(KINDS_FOR[name])
    for bad in ("mint", "", "LDA", " lda", ["lda"], 3, True):
        expected = "invalid_choice" if isinstance(bad, str) else "invalid"
        assert codes(account, view, {name: bad}) == {name: expected}, repr(bad)
    for value in definition.allowed_values:
        view = update(account, view, {name: value})
        assert view.fields[name] == value


def test_the_enum_vocabularies_are_the_specified_ones():
    expected = {
        "balloting_status": ("not_applicable", "balloting_pending", "balloted"),
        "development_status": ("developed", "partially_developed", "undeveloped"),
        "approval_authority": (
            "lda",
            "cda",
            "kda",
            "rda",
            "fda",
            "mda",
            "gda",
            "bda",
            "peshawar_development_authority",
            "private_society",
            "none",
            "other",
        ),
        "plot_file_status": ("open_file", "allocation_letter", "possession_letter"),
        "business_use": (
            "retail",
            "restaurant",
            "office_work",
            "clinic",
            "warehouse",
            "showroom",
            "mixed",
            "other",
        ),
        "road_access": ("main_road", "secondary_road", "inner_street"),
    }
    for name, values in expected.items():
        assert registry.field(name).allowed_values == values, name


def test_conference_rooms_follow_the_integer_rules():
    account, view = draft(OFFICE)
    for bad in ("3", "", "3.0", 3.0, 3.5, True, False, [], {}, [3], 1e1):
        assert codes(account, view, {"conference_rooms": bad}) == {"conference_rooms": "invalid"}
    for bad in (-1, 51, 10**30):
        assert codes(account, view, {"conference_rooms": bad}) == {
            "conference_rooms": "invalid_range"
        }


def test_all_failures_are_reported_at_once_and_nothing_is_written():
    account, view = draft()
    assert codes(
        account,
        view,
        {
            "plot_width": "0",
            "plot_depth": 5,
            "corner_plot": "yes",
            "approval_authority": "xyz",
            "frontage_ft": "5",
            "title": "ok title one",
        },
    ) == {
        "plot_width": "invalid_measure",
        "plot_depth": "invalid",
        "corner_plot": "invalid",
        "approval_authority": "invalid_choice",
        "frontage_ft": "not_applicable",
    }
    assert no_rows() and PropertyListing.objects.get().revision == 1


# --- the Plot File vocabulary ------------------------------------------------------------------


@pytest.mark.parametrize("value", ["possession_ready", "possession_pending"])
def test_a_plot_file_is_never_possession_ready_or_pending(value):
    account, view = draft(PLOT_FILE)
    assert codes(account, view, {"possession_status": value}) == {
        "possession_status": "invalid_choice"
    }
    assert no_rows() and PropertyListing.objects.get().revision == 1


@pytest.mark.parametrize("kind", [RESIDENTIAL_PLOT, COMMERCIAL_PLOT])
def test_the_other_plots_refuse_file_only_and_take_the_two_possession_values(kind):
    account, view = draft(kind)
    assert codes(account, view, {"possession_status": "file_only"}) == {
        "possession_status": "invalid_choice"
    }
    for value in ("possession_ready", "possession_pending"):
        view = update(account, view, {"possession_status": value})
        assert view.fields["possession_status"] == value


def test_a_plot_file_takes_file_only_and_a_wrong_type_is_invalid_not_a_choice():
    account, view = draft(PLOT_FILE)
    view = update(account, view, {"possession_status": "file_only"})
    assert view.revision == 2
    for bad in ("", "FILE_ONLY", "mint"):
        assert codes(account, view, {"possession_status": bad}) == {
            "possession_status": "invalid_choice"
        }
    assert codes(account, view, {"possession_status": 3}) == {"possession_status": "invalid"}


def test_the_plot_file_rule_is_judged_from_the_identity_not_from_other_fields():
    account, view = draft(PLOT_FILE)
    assert codes(
        account, view, {"possession_status": "possession_ready", "plot_file_status": "open_file"}
    ) == {"possession_status": "invalid_choice"}
    assert no_rows()


def test_a_stored_vocabulary_violation_is_revalidated_when_resent():
    account, view = draft(PLOT_FILE)
    saved = update(account, view, {"possession_status": "file_only"})
    ListingPlot.objects.update(possession_status="possession_ready")  # older, looser data
    assert codes(account, saved, {"possession_status": "possession_ready"}) == {
        "possession_status": "invalid_choice"
    }
    assert ListingPlot.objects.get().possession_status == "possession_ready"
    fixed = update(account, saved, {"possession_status": "file_only"})
    assert fixed.fields["possession_status"] == "file_only" and fixed.revision == 3


# --- applicability -----------------------------------------------------------------------------


def sample_value(name):
    definition = registry.field(name)
    if definition.type == "boolean":
        return True
    if definition.type == "integer":
        return 1
    if definition.type == "decimal":
        return "10"
    if definition.type == "enum":
        return definition.allowed_values[0]
    return None


@pytest.mark.parametrize("subtype", EVERY_SUBTYPE)
def test_the_not_applicable_matrix_for_the_new_fields(subtype):
    account, view = draft(KINDS[subtype])
    for name in (*UTILITY_FIELDS, *PLOT_FIELDS, *COMMERCIAL_FIELDS):
        value = sample_value(name)
        if name == "possession_status":
            value = "file_only" if subtype == "plot_file" else "possession_ready"
        if subtype in APPLIES[name]:
            update(account, view, {name: value})
            view = selectors.get_owned_listing(user(account), view.id)
            definition = registry.field(name)
            expected = f"{D(value):.2f}" if definition.type == "decimal" else value
            assert view.fields[name] == expected, name
        else:
            assert codes(account, view, {name: value}) == {name: "not_applicable"}, name
    assert PropertyListing.objects.get().revision == view.revision


@pytest.mark.parametrize(
    ("kind", "updates"),
    [
        (OFFICE, {"frontage_ft": "10"}),
        (SHOP, {"conference_rooms": 2}),
        (SHOP, {"business_centre_facilities": True}),
        (HOUSE, {"business_use": "retail"}),
        (APARTMENT, {"frontage_ft": "10"}),
        (RESIDENTIAL_PLOT, {"bedrooms": 2}),
        (RESIDENTIAL_PLOT, {"plot_file_status": "open_file"}),
        (COMMERCIAL_PLOT, {"plot_file_status": "open_file"}),
        (SHOP, {"kitchens": 1}),
        (OFFICE, {"kitchens": 1}),
        (SHOP, {"lift": True}),
        (SHOP, {"plot_width": "10"}),
        (OFFICE, {"possession_status": "possession_ready"}),
        (HOUSE, {"electricity": True}),
        (APARTMENT, {"gas": True}),
        (PLOT_FILE, {"condition": "new"}),
        (PLOT_FILE, {"floor_number": 1}),
        (RESIDENTIAL_PLOT, {"parking_spaces": 1}),
    ],
)
def test_subtype_limits_from_the_matrix(kind, updates):
    account, view = draft(kind)
    name = next(iter(updates))
    assert codes(account, view, updates) == {name: "not_applicable"}
    assert no_rows() and PropertyListing.objects.get().revision == 1


def test_a_wrong_subtype_judges_applicability_before_the_value():
    account, view = draft(HOUSE)
    assert codes(account, view, {"plot_width": "garbage", "conference_rooms": "x"}) == {
        "plot_width": "not_applicable",
        "conference_rooms": "not_applicable",
    }


def test_the_right_set_beside_the_wrong_one_reports_only_the_wrong_field():
    account, view = draft(SHOP)
    assert codes(account, view, {"frontage_ft": "10", "condition": "good", "lift": True}) == {
        "lift": "not_applicable"
    }
    assert no_rows()


def test_null_on_a_not_applicable_field_is_a_documented_no_op():
    account, view = draft(HOUSE)
    saved = update(
        account, view, {"plot_width": None, "frontage_ft": None, "conference_rooms": None}
    )
    assert saved.revision == 1 and no_rows()
    assert ListingRevision.objects.count() == 1
    saved = update(account, view, {"bedrooms": 2})
    again = update(account, saved, {"plot_file_status": None})
    assert again.revision == saved.revision == 2 and ListingRevision.objects.count() == 2


def test_a_null_clears_a_value_stored_under_an_older_registry():
    account, view = draft(RESIDENTIAL_PLOT)
    saved = update(account, view, {"plot_width": "10"})
    ListingPlot.objects.update(plot_file_status="open_file")  # a Plot File field on a plain plot
    cleared = update(account, saved, {"plot_file_status": None})
    assert cleared.revision == 3 and ListingPlot.objects.get().plot_file_status is None
    assert ListingRevision.objects.get(revision=3).changes == {"plot_file_status": None}


# --- shared fields and the floor rule ------------------------------------------------------------


def test_shared_fields_use_the_existing_columns_for_shops_and_offices():
    for kind in (SHOP, OFFICE):
        _, saved = save({"floor_number": 2, "building_floors": 3, "condition": "new"}, kind)
        assert (saved.fields["floor_number"], saved.fields["building_floors"]) == (2, 3)
        unit = ListingResidential.objects.get()
        assert (unit.floor_number, unit.building_floors, unit.condition) == (2, 3, "new")
        assert not ListingCommercial.objects.exists()
        PropertyListing.objects.all().delete()


@pytest.mark.parametrize("kind", [SHOP, OFFICE, APARTMENT])
def test_the_floor_rule_applies_wherever_both_fields_apply(kind):
    account, view = draft(kind)
    assert codes(account, view, {"floor_number": 5, "building_floors": 3}) == {
        "floor_number": "invalid_floor",
        "building_floors": "invalid_floor",
    }
    assert no_rows()
    saved = update(account, view, {"floor_number": 3, "building_floors": 3})  # equal is fine
    assert codes(account, saved, {"floor_number": 4}) == {"floor_number": "invalid_floor"}
    assert codes(account, saved, {"building_floors": 2}) == {
        "floor_number": "invalid_floor",
        "building_floors": "invalid_floor",
    }
    cleared = update(account, saved, {"building_floors": None})
    assert update(account, cleared, {"floor_number": 99}).fields["floor_number"] == 99


def test_a_floor_range_failure_reports_the_range_code_only():
    account, view = draft(SHOP)
    assert codes(account, view, {"floor_number": 300, "building_floors": 5}) == {
        "floor_number": "invalid_range"
    }


def test_the_rule_does_not_exist_for_a_house_and_floors_are_not_applicable_there():
    account, view = draft(HOUSE)
    assert codes(account, view, {"floor_number": 5, "building_floors": 3}) == {
        "floor_number": "not_applicable",
        "building_floors": "not_applicable",
    }


def test_the_floor_check_constraint_still_guards_a_shop_row(db):
    listing = PropertyListing.objects.create(
        owner_account_id=make_account(random_email()).id,
        status="draft",
        revision=1,
        registry_version=5,
        purpose="rent",
        category="commercial",
        subtype="shop",
    )
    with pytest.raises(IntegrityError, match="listings_res_floor_within_building"):
        with transaction.atomic():
            ListingResidential.objects.create(listing=listing, floor_number=5, building_floors=4)


# --- unsupported attributes ----------------------------------------------------------------------

UNSUPPORTED = (
    "installment_available",
    "down_payment",
    "title_document_type",
    "mortgage_declaration",
    "approval_reference_number",
)


@pytest.mark.parametrize("name", UNSUPPORTED)
def test_unsupported_attributes_are_unknown_fields_and_nothing_is_written(name):
    account, view = draft(RESIDENTIAL_PLOT)
    for value in (True, "x", 5, None):
        assert codes(account, view, {name: value}) == {"non_field_errors": "unknown_field"}
    assert codes(account, view, {name: True, "plot_width": "10"}) == {
        "non_field_errors": "unknown_field"
    }
    assert no_rows() and PropertyListing.objects.get().revision == 1
    assert registry.field(name) is None and name not in fields.STORAGE


def test_every_unsupported_attribute_together_is_one_unknown_field_error():
    account, view = draft(PLOT_FILE)
    assert codes(account, view, dict.fromkeys(UNSUPPORTED, True)) == {
        "non_field_errors": "unknown_field"
    }
    assert no_rows()


# --- lazy creation, null and clears ---------------------------------------------------------------


def test_a_new_draft_has_no_detail_rows_and_every_detail_is_null():
    for kind in (RESIDENTIAL_PLOT, SHOP):
        _, view = draft(kind)
        assert no_rows()
        assert all(view.fields[name] is None for name in DETAIL_IDS)
        assert all(name not in view.provenance for name in DETAIL_IDS)


def test_other_edits_and_reads_never_create_the_rows():
    account, view = draft()
    saved = update(account, view, {"title": "Corner plot near the park"})
    selectors.get_owned_listing(user(account), saved.id)
    selectors.list_owned(user(account))
    assert no_rows()


def test_clears_never_create_a_row_or_a_revision():
    account, view = draft()
    saved = update(account, view, {"plot_width": None, "corner_plot": None, "water": None})
    assert saved.revision == 1 and no_rows() and ListingRevision.objects.count() == 1
    saved = update(account, view, {"plot_width": None, "title": "Corner plot near the park"})
    assert saved.revision == 2 and no_rows()


def test_the_first_non_null_value_creates_only_its_own_row_once():
    account, view = draft()
    saved = update(account, view, {"plot_width": "30"})
    assert ListingPlot.objects.count() == 1
    assert not ListingResidential.objects.exists() and not ListingCommercial.objects.exists()
    first = ListingPlot.objects.get()
    saved = update(account, saved, {"plot_depth": "60"})
    assert ListingPlot.objects.count() == 1 and ListingPlot.objects.get().pk == first.pk
    update(account, saved, {"water": True})  # a shared utility creates the unit-details row
    assert ListingResidential.objects.count() == 1 and ListingPlot.objects.count() == 1
    account, view = draft(SHOP, make_account(random_email()))
    update(account, view, {"frontage_ft": "10"})
    assert ListingCommercial.objects.count() == 1


def test_a_null_together_with_a_value_creates_the_row_with_only_the_value():
    _, saved = save({"plot_width": "30", "plot_depth": None, "corner_plot": None})
    row = ListingPlot.objects.get()
    assert row.plot_width == D("30.00") and row.plot_depth is None and row.corner_plot is None
    assert saved.fields["plot_depth"] is None


def test_unanswered_is_null_never_zero_or_false():
    _, saved = save({"plot_width": "30"})
    row = ListingPlot.objects.get()
    for name in PLOT_FIELDS:
        if name != "plot_width":
            assert getattr(row, name) is None, name
            assert saved.fields[name] is None, name
    _, saved = save({"business_use": "retail"}, SHOP)
    row = ListingCommercial.objects.get()
    for name in COMMERCIAL_FIELDS:
        if name != "business_use":
            assert getattr(row, name) is None, name


def test_clearing_sets_null_removes_provenance_and_keeps_the_row():
    account, view = draft()
    saved = update(account, view, {"plot_width": "30", "corner_plot": True})
    cleared = update(account, saved, {"corner_plot": None})
    assert cleared.revision == 3 and cleared.fields["corner_plot"] is None
    assert "corner_plot" not in cleared.provenance and "plot_width" in cleared.provenance
    assert ListingRevision.objects.get(revision=3).changes == {"corner_plot": None}
    update(account, cleared, {"plot_width": None})
    assert ListingPlot.objects.count() == 1 and ListingPlot.objects.get().plot_width is None


def test_false_and_zero_are_changes_from_unknown_and_repeats_are_no_ops():
    account, view = draft(OFFICE)
    saved = update(account, view, {"business_centre_facilities": False, "conference_rooms": 0})
    assert saved.revision == 2 and saved.fields["conference_rooms"] == 0
    again = update(account, saved, {"business_centre_facilities": False, "conference_rooms": 0})
    assert again.revision == 2 and ListingRevision.objects.count() == 2


def test_resaving_an_ai_value_manually_confirms_it():
    account, view = draft()
    saved = update(account, view, {"approval_authority": "lda"}, origin="ai")
    assert dict(saved.provenance["approval_authority"]) == {"origin": "ai", "confirmed": False}
    confirmed = update(account, saved, {"approval_authority": "lda"})
    assert confirmed.revision == 3
    assert dict(confirmed.provenance["approval_authority"]) == {
        "origin": "manual",
        "confirmed": True,
    }


def test_decimal_text_compares_by_value_so_a_re_entry_of_the_same_number_is_a_no_op():
    account, view = draft()
    saved = update(account, view, {"plot_width": "35"})
    assert update(account, saved, {"plot_width": "35.00"}).revision == saved.revision
    assert update(account, saved, {"plot_width": "35.5"}).revision == saved.revision + 1


# --- the event -----------------------------------------------------------------------------------


@pytest.fixture
def bus(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    seen = []
    events.subscribe(ListingModelInputsChanged, seen.append)
    return seen


def test_the_valuation_inputs_of_this_story_are_exactly_the_specified_ones():
    flagged = {f.id for f in registry.fields() if f.valuation_input}
    added = {
        "plot_width",
        "plot_depth",
        "road_width_ft",
        "corner_plot",
        "development_status",
        "frontage_ft",
    }
    assert added <= flagged
    for name in (*UTILITY_FIELDS, *PLOT_FIELDS, *COMMERCIAL_FIELDS):
        assert registry.field(name).valuation_input == (name in added), name


@pytest.mark.parametrize(
    ("kind", "updates"),
    [
        (RESIDENTIAL_PLOT, {"plot_width": "30"}),
        (RESIDENTIAL_PLOT, {"plot_depth": "60"}),
        (COMMERCIAL_PLOT, {"road_width_ft": "20"}),
        (RESIDENTIAL_PLOT, {"corner_plot": False}),
        (PLOT_FILE, {"development_status": "undeveloped"}),
        (SHOP, {"frontage_ft": "12.5"}),
        (SHOP, {"condition": "new"}),
        (RESIDENTIAL_PLOT, {"plot_width": "30", "plot_depth": "60", "corner_plot": True}),
    ],
)
def test_a_valuation_input_emits_one_event_after_commit(
    bus, django_capture_on_commit_callbacks, kind, updates
):
    account, view = draft(kind)
    with django_capture_on_commit_callbacks(execute=True):
        update(account, view, updates)
        assert bus == []
    assert len(bus) == 1 and bus[0].listing_id == view.id


@pytest.mark.parametrize(
    ("kind", "updates"),
    [
        (RESIDENTIAL_PLOT, {"possession_status": "possession_ready"}),
        (RESIDENTIAL_PLOT, {"balloting_status": "balloted", "approval_authority": "lda"}),
        (RESIDENTIAL_PLOT, {"sewerage": True, "park_facing": True, "orientation": "north"}),
        (PLOT_FILE, {"plot_file_status": "open_file"}),
        (COMMERCIAL_PLOT, {"electricity": True, "water": False, "gas": True}),
        (SHOP, {"business_use": "retail", "road_access": "main_road"}),
        (SHOP, {"ceiling_height_ft": "10", "power_load_kw": "5"}),
        (OFFICE, {"conference_rooms": 2, "business_centre_facilities": True, "lift": True}),
        (OFFICE, {"three_phase_electricity": True, "furnishing": "furnished"}),
    ],
)
def test_other_details_emit_nothing(bus, django_capture_on_commit_callbacks, kind, updates):
    account, view = draft(kind)
    with django_capture_on_commit_callbacks(execute=True):
        update(account, view, updates)
    assert bus == []


def test_changing_clearing_and_repeating_a_model_input(bus, django_capture_on_commit_callbacks):
    account, view = draft()
    with django_capture_on_commit_callbacks(execute=True):
        saved = update(account, view, {"plot_width": "30"})
    assert len(bus) == 1
    bus.clear()
    with django_capture_on_commit_callbacks(execute=True):
        update(account, saved, {"plot_width": "30.00"})  # the same number: no change
        saved = update(account, saved, {"sewerage": True})
    assert bus == []
    with django_capture_on_commit_callbacks(execute=True):
        update(account, saved, {"plot_width": None})  # a clear changes a model input
    assert len(bus) == 1


def test_a_failure_after_the_write_rolls_the_rows_back_and_emits_nothing(
    bus, monkeypatch, django_capture_on_commit_callbacks
):
    account, view = draft()

    def boom(*args, **kwargs):
        raise RuntimeError("after the write")

    monkeypatch.setattr(ListingRevision.objects, "create", boom)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(RuntimeError):
            update(account, view, {"plot_width": "30", "water": True})
    assert no_rows()
    assert PropertyListing.objects.get().revision == 1 and callbacks == [] and bus == []


# --- concurrency, owners, older registry ---------------------------------------------------------


def test_a_stale_base_revision_is_refused_with_the_current_view_and_writes_nothing():
    account, view = draft()
    saved = update(account, view, {"plot_width": "30"})
    with pytest.raises(services.StaleRevision) as caught:
        update(account, view, {"plot_width": "99"}, base=1)
    assert caught.value.current == saved and caught.value.current.fields["plot_width"] == "30.00"
    assert ListingPlot.objects.get().plot_width == D("30.00")


def test_a_stale_first_save_and_a_replay_do_not_create_rows():
    account, view = draft(SHOP)
    update(account, view, {"title": "Shop on the main road"})
    for _ in range(2):  # a retried PATCH after it succeeded is a conflict too
        with pytest.raises(services.StaleRevision):
            update(account, view, {"frontage_ft": "10"}, base=1)
    assert no_rows()


def test_another_owner_gets_not_found_and_no_row():
    _, view = draft()
    other = make_account("john.roe@example.com")
    with pytest.raises(services.DomainError) as caught:
        services.apply_updates(user(other), view.id, 1, {"plot_width": "30"})
    assert caught.value.code == "not_found" and no_rows()


def test_a_draft_from_an_older_registry_is_revalidated_under_the_current_version():
    account, view = draft(PLOT_FILE)
    PropertyListing.objects.update(registry_version=4)
    assert codes(account, view, {"possession_status": "possession_ready"}) == {
        "possession_status": "invalid_choice"
    }
    assert PropertyListing.objects.get().registry_version == 4
    saved = update(account, view, {"possession_status": "file_only"})
    assert saved.registry_version == registry.registry_version() == 9
    assert PropertyListing.objects.get().registry_version == 9


def drop_constraint(table, name):
    """Stored data from an older, wider registry: the test drops the CHECK (rolled back with it)."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")  # settle the deferred foreign key checks
        cursor.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")


def test_a_stored_out_of_range_measure_resent_unchanged_is_invalid_measure():
    account, view = draft()
    saved = update(account, view, {"plot_width": "30"})
    drop_constraint("listings_listingplot", "listings_plot_plot_width_range")
    ListingPlot.objects.update(plot_width=D("0.00"))  # allowed by an older, wider registry
    assert codes(account, saved, {"plot_width": "0"}) == {"plot_width": "invalid_measure"}
    assert ListingPlot.objects.get().plot_width == D("0.00")
    assert PropertyListing.objects.get().revision == 2
    assert update(account, saved, {"plot_width": "31"}).fields["plot_width"] == "31.00"


def test_a_stored_out_of_range_integer_resent_unchanged_is_invalid_range():
    account, view = draft(OFFICE)
    saved = update(account, view, {"conference_rooms": 3})
    drop_constraint("listings_listingcommercial", "listings_com_conference_rooms_range")
    ListingCommercial.objects.update(conference_rooms=60)
    assert codes(account, saved, {"conference_rooms": 60}) == {"conference_rooms": "invalid_range"}
    assert update(account, saved, {"conference_rooms": 5}).fields["conference_rooms"] == 5


def test_a_stored_floor_violation_on_a_shop_is_not_rejudged_by_an_unrelated_edit():
    account, view = draft(SHOP)
    saved = update(account, view, {"floor_number": 2, "building_floors": 9})
    drop_constraint("listings_listingresidential", "listings_res_floor_within_building")
    ListingResidential.objects.update(floor_number=5, building_floors=3)
    after = update(account, saved, {"title": "Shop on the main road", "frontage_ft": "10"})
    assert (after.fields["floor_number"], after.fields["building_floors"]) == (5, 3)
    assert codes(account, after, {"floor_number": 5}) == {"floor_number": "invalid_floor"}


def test_a_changed_registry_bound_is_judged_on_the_next_save(monkeypatch):
    account, view = draft()
    real = registry.field
    monkeypatch.setattr(
        registry,
        "field",
        lambda name: (
            dataclasses.replace(real(name), maximum=D("50.00"))
            if name == "plot_width"
            else real(name)
        ),
    )
    assert codes(account, view, {"plot_width": "50.01"}) == {"plot_width": "invalid_measure"}
    assert update(account, view, {"plot_width": "50"}).fields["plot_width"] == "50.00"


def test_the_view_is_unchanged_by_a_later_unrelated_read_path():
    account, saved = save({"plot_width": "30", "corner_plot": None, "water": True})
    assert selectors.list_owned(user(account)).items[0] == saved


def test_the_validators_do_not_copy_or_coerce_values():
    width = registry.field("plot_width")
    assert validation.check_decimal(width, "35") == (D("35.00"), None)
    assert validation.check_decimal(width, 35) == (None, "invalid")
    assert validation.check_decimal(width, "0") == (None, "invalid_measure")
    assert validation.check_decimal(width, "1.234") == (None, "invalid_measure")
    assert validation.check_decimal(width, "35.5").__getitem__(0).as_tuple().exponent == -2
    possession = registry.field("possession_status")
    assert validation.check_enum(possession, "file_only", "sale", "plot_file") == (
        "file_only",
        None,
    )
    assert validation.check_enum(possession, "file_only", "sale", "residential_plot") == (
        None,
        "invalid_choice",
    )
    assert validation.check_enum(possession, "possession_ready", "sale", "plot_file") == (
        None,
        "invalid_choice",
    )


# --- constraints and drift -----------------------------------------------------------------------


def listing_row(kind=RESIDENTIAL_PLOT):
    return PropertyListing.objects.create(
        owner_account_id=make_account(random_email()).id,
        status="draft",
        revision=1,
        registry_version=5,
        purpose=kind[0],
        category=kind[1],
        subtype=kind[2],
    )


def plot(listing=None, **values):
    return ListingPlot.objects.create(listing=listing or listing_row(), **values)


def commercial(listing=None, **values):
    return ListingCommercial.objects.create(listing=listing or listing_row(SHOP), **values)


def _migrated(model):
    state = MigrationLoader(connection).project_state()
    options = state.models[("listings", model)].options
    return {c.name: c.condition for c in options["constraints"]}


def test_every_non_boolean_field_has_a_named_constraint():
    names = {c.name for c in plot_constraints()}
    for name in PLOT_FIELDS:
        kind = registry.field(name).type
        suffix = {"enum": "valid", "boolean": None}.get(kind, "range")
        if suffix:
            assert f"listings_plot_{name}_{suffix}" in names, name
    booleans = [n for n in PLOT_FIELDS if registry.field(n).type == "boolean"]
    assert len(names) == len(PLOT_FIELDS) - len(booleans)
    names = {c.name for c in commercial_constraints()}
    for name in COMMERCIAL_FIELDS:
        kind = registry.field(name).type
        suffix = {"enum": "valid", "boolean": None}.get(kind, "range")
        if suffix:
            assert f"listings_com_{name}_{suffix}" in names, name
    booleans = [n for n in COMMERCIAL_FIELDS if registry.field(n).type == "boolean"]
    assert len(names) == len(COMMERCIAL_FIELDS) - len(booleans)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"plot_width": "0"}, "listings_plot_plot_width_range"),
        ({"plot_width": "100000"}, "listings_plot_plot_width_range"),
        ({"plot_depth": "-1"}, "listings_plot_plot_depth_range"),
        ({"road_width_ft": "0.99"}, "listings_plot_road_width_ft_range"),
        ({"road_width_ft": "1000"}, "listings_plot_road_width_ft_range"),
        ({"possession_status": "ready"}, "listings_plot_possession_status_valid"),
        ({"balloting_status": "done"}, "listings_plot_balloting_status_valid"),
        ({"development_status": "built"}, "listings_plot_development_status_valid"),
        ({"approval_authority": "xyz"}, "listings_plot_approval_authority_valid"),
        ({"orientation": "up"}, "listings_plot_orientation_valid"),
        ({"plot_file_status": "bought"}, "listings_plot_plot_file_status_valid"),
    ],
)
def test_plot_constraints_name_themselves(db, overrides, constraint):
    with pytest.raises(IntegrityError, match=constraint), transaction.atomic():
        plot(**overrides)
    assert ListingPlot.objects.count() == 0


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"business_use": "farm"}, "listings_com_business_use_valid"),
        ({"frontage_ft": "0"}, "listings_com_frontage_ft_range"),
        ({"frontage_ft": "10000"}, "listings_com_frontage_ft_range"),
        ({"road_access": "lane"}, "listings_com_road_access_valid"),
        ({"ceiling_height_ft": "0.99"}, "listings_com_ceiling_height_ft_range"),
        ({"ceiling_height_ft": "100"}, "listings_com_ceiling_height_ft_range"),
        ({"power_load_kw": "0"}, "listings_com_power_load_kw_range"),
        ({"conference_rooms": -1}, "listings_com_conference_rooms_range"),
        ({"conference_rooms": 51}, "listings_com_conference_rooms_range"),
    ],
)
def test_commercial_constraints_name_themselves(db, overrides, constraint):
    with pytest.raises(IntegrityError, match=constraint), transaction.atomic():
        commercial(**overrides)
    assert ListingCommercial.objects.count() == 0


def test_the_bounds_and_nulls_are_accepted(db):
    plot(
        plot_width="0.01",
        plot_depth="99999.99",
        road_width_ft="1.00",
        possession_status="file_only",
        corner_plot=False,
    )
    plot(road_width_ft="999.99", approval_authority="peshawar_development_authority")
    commercial(
        frontage_ft="9999.99", ceiling_height_ft="1.00", power_load_kw="0.01", conference_rooms=0
    )
    commercial(ceiling_height_ft="99.99", conference_rooms=50)
    assert plot().pk and commercial().pk  # every column is nullable


def test_the_table_stores_the_union_of_possession_values_and_the_rule_is_in_the_service(db):
    # the by-Subtype vocabulary is a service rule: the column allows all three values
    for value in registry.field("possession_status").allowed_values:
        assert plot(possession_status=value).pk


def test_over_the_column_width_is_a_data_error(db):
    with pytest.raises(DataError), transaction.atomic():
        plot(plot_width="100000000000")


def test_one_row_per_listing_and_nulls_are_the_default(db):
    row = listing_row()
    created = plot(listing=row)
    assert all(getattr(created, name) is None for name in PLOT_FIELDS)
    with pytest.raises(IntegrityError), transaction.atomic():
        plot(listing=row)
    shop = listing_row(SHOP)
    created = commercial(listing=shop)
    assert all(getattr(created, name) is None for name in COMMERCIAL_FIELDS)
    with pytest.raises(IntegrityError), transaction.atomic():
        commercial(listing=shop)


def test_the_column_types_in_the_database(db):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name, column_name, data_type, numeric_scale "
            "FROM information_schema.columns WHERE table_name = ANY(%s) AND column_name = ANY(%s)",
            [
                [
                    "listings_listingplot",
                    "listings_listingcommercial",
                    "listings_listingresidential",
                ],
                [*PLOT_FIELDS, *COMMERCIAL_FIELDS, *UTILITY_FIELDS],
            ],
        )
        found = {row[1]: row[2:] for row in cursor.fetchall()}
    assert set(found) == {*PLOT_FIELDS, *COMMERCIAL_FIELDS, *UTILITY_FIELDS}
    for name in found:
        kind = registry.field(name).type
        expected = {"boolean": "boolean", "integer": "integer", "decimal": "numeric"}.get(kind)
        if expected:
            assert found[name][0] == expected, name
        if kind == "decimal":
            assert found[name][1] == 2, name


def test_the_migration_matches_the_models_and_the_registry(db):
    for model, current in (
        ("listingplot", plot_constraints),
        ("listingcommercial", commercial_constraints),
    ):
        migrated = _migrated(model)
        assert migrated
        assert migrated == {c.name: c.condition for c in current()}
    plot_migrated = _migrated("listingplot")
    assert "99999.99" in repr(plot_migrated["listings_plot_plot_width_range"])
    assert "999.99" in repr(plot_migrated["listings_plot_road_width_ft_range"])
    assert "['possession_ready', 'possession_pending', 'file_only']" in repr(
        plot_migrated["listings_plot_possession_status_valid"]
    )
    assert "peshawar_development_authority" in repr(
        plot_migrated["listings_plot_approval_authority_valid"]
    )
    commercial_migrated = _migrated("listingcommercial")
    assert "9999.99" in repr(commercial_migrated["listings_com_frontage_ft_range"])
    assert "50" in repr(commercial_migrated["listings_com_conference_rooms_range"])
    # the unit-details table kept its constraints, floor rule included
    unit = _migrated("listingresidential")
    assert "listings_res_floor_within_building" in unit


def test_a_changed_bound_or_enum_would_differ_from_the_migration(db, monkeypatch):
    plot_migrated, commercial_migrated = _migrated("listingplot"), _migrated("listingcommercial")
    real = registry.field
    changes = [
        ("plot_width", {"maximum": D(5)}, plot_constraints, plot_migrated),
        ("road_width_ft", {"minimum": D(2)}, plot_constraints, plot_migrated),
        ("possession_status", {"allowed_values": ("file_only",)}, plot_constraints, plot_migrated),
        ("approval_authority", {"allowed_values": ("lda",)}, plot_constraints, plot_migrated),
        ("frontage_ft", {"maximum": D(5)}, commercial_constraints, commercial_migrated),
        ("conference_rooms", {"maximum": D(5)}, commercial_constraints, commercial_migrated),
        (
            "business_use",
            {"allowed_values": ("retail",)},
            commercial_constraints,
            commercial_migrated,
        ),
    ]
    for field_id, change, generate, migrated in changes:
        monkeypatch.setattr(
            registry,
            "field",
            lambda name, fid=field_id, ch=change: (
                dataclasses.replace(real(name), **ch) if name == fid else real(name)
            ),
        )
        drifted = {c.name: c.condition for c in generate()}
        assert any(migrated[name] != drifted[name] for name in drifted), field_id


def test_storage_follows_the_single_map():
    assert fields.PLOT_FIELDS == PLOT_FIELDS
    assert fields.COMMERCIAL_FIELDS == COMMERCIAL_FIELDS
    assert all(fields.STORAGE[name] == fields.PLOT for name in PLOT_FIELDS)
    assert all(fields.STORAGE[name] == fields.COMMERCIAL for name in COMMERCIAL_FIELDS)
    assert all(fields.STORAGE[name] == fields.RESIDENTIAL for name in UTILITY_FIELDS)
    assert set(fields.RESIDENTIAL_FIELDS) == {*RESIDENTIAL_IDS, *UTILITY_FIELDS}


def test_seller_declared_fields_are_flagged_in_the_registry_and_nowhere_else():
    # `possession_availability` (a sale term, Story 2.6) is the one claim outside these tables
    assert {f.id for f in registry.fields() if f.seller_declared} == {
        *SELLER_DECLARED,
        "possession_availability",
    }
