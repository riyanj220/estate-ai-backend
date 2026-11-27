"""Location and the private address (Story 2.7): the `reference` registry type, the lazy
`ListingLocation` and `ListingPrivateAddress` rows, the city/locality rule, coordinates, privacy
and drift. Values are low-entropy fake words and exact decimals."""

import logging
import threading
import uuid
from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.test.utils import CaptureQueriesContext

from apps.catalog import registry
from apps.catalog.models import City, Locality
from apps.identity import services as identity_services
from apps.kernel import events
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError, field_message
from apps.listings import fields, selectors, services, validation
from apps.listings.events import ListingModelInputsChanged
from apps.listings.models import (
    ListingLocation,
    ListingPrivateAddress,
    ListingRevision,
    PropertyListing,
    address_constraints,
)
from tests.listings.support import (
    HOUSE,
    LOCATION_IDS,
    PRIVATE_ADDRESS_IDS,
    PUBLIC_LOCATION_IDS,
    SYSTEM,
    make_account,
    random_email,
    seed_cap,
    set_listing_visibility,
    user,
)

D = Decimal
STREET = "Mango Lane"
HOUSE_NO = "Plum 12"
LAT, LON = "24.860000", "67.010000"


@pytest.fixture(autouse=True)
def cap(db):
    seed_cap()


@pytest.fixture
def places(db):
    """Two cities with two localities each (get_or_create: the seed may already hold Karachi)."""
    karachi, _ = City.objects.get_or_create(name="Karachi")
    lahore, _ = City.objects.get_or_create(name="Lahore")
    k1, _ = Locality.objects.get_or_create(city=karachi, name="Testville North", kind="area")
    k2, _ = Locality.objects.get_or_create(city=karachi, name="Testville Phase 3", kind="society")
    l1, _ = Locality.objects.get_or_create(city=lahore, name="Lahoreville Block", kind="area")
    return {"karachi": karachi, "lahore": lahore, "k1": k1, "k2": k2, "l1": l1}


def draft(account=None, values=HOUSE):
    account = account or make_account()
    return account, services.create_draft(user(account), *values)


def update(account, view, updates, base=None):
    return services.apply_updates(
        user(account), view.id, view.revision if base is None else base, updates
    )


def refused(account, view, updates, base=None):
    with pytest.raises(services.FieldsInvalid) as caught:
        update(account, view, updates, base)
    return caught.value.fields


def counts():
    return (
        PropertyListing.objects.count(),
        ListingLocation.objects.count(),
        ListingPrivateAddress.objects.count(),
        ListingRevision.objects.count(),
    )


def pair(places, city="karachi", locality="k1"):
    return {"city": str(places[city].id), "locality": str(places[locality].id)}


# --- the registry -----------------------------------------------------------------------------


def test_the_registry_defines_the_location_fields_at_the_end_with_the_right_classes():
    assert registry.registry_version() == 11
    ids = [f.id for f in registry.fields()]
    assert tuple(ids[-len(LOCATION_IDS) - 1 : -1]) == LOCATION_IDS  # amenities come last
    for name in LOCATION_IDS:
        definition = registry.field(name)
        assert definition.section == "location" and definition.applicability == (
            registry.Applicability()
        )
        assert definition.field_class == ("private" if name in PRIVATE_ADDRESS_IDS else "public")
    for name in ("city", "locality"):
        definition = registry.field(name)
        assert (definition.type, definition.requirement, definition.searchable) == (
            "reference",
            "required_for_publication",
            True,
        )
        assert definition.reference == name
    for name in ("society", "phase", "sector", "block"):
        definition = registry.field(name)
        assert (definition.type, definition.max_length, definition.requirement) == (
            "text",
            60,
            "optional",
        )
        assert definition.ai_extractable and not definition.searchable
    assert registry.field("street").max_length == 120
    assert registry.field("house_number").max_length == 30
    for name in ("latitude", "longitude"):
        definition = registry.field(name)
        assert (definition.type, definition.scale, definition.error_code) == (
            "decimal",
            6,
            "out_of_range",
        )
    assert (registry.field("latitude").minimum, registry.field("latitude").maximum) == (
        D(-90),
        D(90),
    )
    assert (registry.field("longitude").minimum, registry.field("longitude").maximum) == (
        D(-180),
        D(180),
    )


def test_no_private_field_is_searchable_sortable_extractable_or_a_model_input():
    private = [f for f in registry.fields() if f.field_class == "private"]
    assert {f.id for f in private} == set(PRIVATE_ADDRESS_IDS)
    for f in private:
        assert not (f.searchable or f.sortable or f.ai_extractable or f.valuation_input), f.id
        assert f.requirement == "optional" and not f.quota_identity


def test_storage_routes_and_tables():
    assert fields.LOCATION_FIELDS == PUBLIC_LOCATION_IDS
    assert fields.ADDRESS_FIELDS == PRIVATE_ADDRESS_IDS
    for name in PUBLIC_LOCATION_IDS:
        assert ListingLocation._meta.get_field(name).null is True
    assert ListingLocation._meta.get_field("city").column == "city_id"
    assert ListingLocation._meta.get_field("locality").column == "locality_id"
    for name in PRIVATE_ADDRESS_IDS:
        assert ListingPrivateAddress._meta.get_field(name).null is True
    for name in ("latitude", "longitude"):
        column = ListingPrivateAddress._meta.get_field(name)
        assert (column.max_digits, column.decimal_places) == (9, 6)
    assert ListingPrivateAddress._meta.get_field("listing").related_model is PropertyListing


# --- choosing a city and a locality -------------------------------------------------------------


def test_a_valid_pair_is_saved_served_as_uuid_text_and_bumps_the_revision(places):
    account, view = draft()
    assert counts() == (1, 0, 0, 1)  # nothing is created with the Draft
    saved = update(account, view, pair(places))
    assert saved.revision == 2
    assert saved.fields["city"] == str(places["karachi"].id)
    assert saved.fields["locality"] == str(places["k1"].id)
    assert all(saved.fields[n] is None for n in ("society", "phase", "sector", "block"))
    assert saved.provenance["city"] == {"origin": "manual", "confirmed": True}
    row = ListingLocation.objects.get()
    assert (row.city, row.locality) == (places["karachi"].id, places["k1"].id)
    assert counts() == (1, 1, 0, 2)
    revision = ListingRevision.objects.get(revision=2)
    assert revision.changes == {
        "city": str(places["karachi"].id),
        "locality": str(places["k1"].id),
    }


def test_saving_the_same_pair_again_is_a_no_op(places):
    account, view = draft()
    saved = update(account, view, pair(places))
    again = update(account, saved, pair(places))
    assert again.revision == 2 and counts()[3] == 2


@pytest.mark.parametrize(
    "bad",
    [
        "Karachi",
        "not-a-uuid",
        "",
        " ",
        5,
        True,
        1.5,
        ["x"],
        {"id": "x"},
        "A" * 36,
        "00000000-0000-0000-0000-000000000000",  # well formed, unknown
        "{00000000-0000-0000-0000-000000000000}",
        "00000000000000000000000000000000",  # no hyphens
    ],
)
@pytest.mark.parametrize("name", ["city", "locality"])
def test_unknown_or_malformed_ids_are_invalid_and_nothing_is_written(places, name, bad):
    account, view = draft()
    before = counts()
    assert refused(account, view, {name: bad}) == {name: "invalid"}
    assert counts() == before


@pytest.mark.parametrize("form", [str.upper, lambda t: "{" + t + "}", lambda t: t.replace("-", "")])
def test_other_uuid_forms_are_accepted_and_stored_canonical(places, form):
    account, view = draft()
    saved = update(
        account,
        view,
        {"city": form(str(places["karachi"].id)), "locality": form(str(places["k1"].id))},
    )
    assert saved.fields["city"] == str(places["karachi"].id)  # lowercase, hyphenated
    assert saved.fields["locality"] == str(places["k1"].id)
    row = ListingLocation.objects.get()
    assert (row.city, row.locality) == (places["karachi"].id, places["k1"].id)
    assert ListingRevision.objects.get(revision=2).changes["city"] == str(places["karachi"].id)
    again = update(account, saved, {"city": form(str(places["karachi"].id))})
    assert again.revision == 2  # the same value in another form changes nothing


def test_a_stored_city_or_locality_that_became_inactive_can_be_resaved_unchanged(places):
    account, view = draft()
    saved = update(account, view, pair(places))
    City.objects.filter(pk=places["karachi"].pk).update(is_active=False)
    Locality.objects.filter(pk=places["k1"].pk).update(is_active=False)
    again = update(account, saved, {**pair(places), "society": "Plum Society"})
    assert again.revision == 3 and again.fields["society"] == "Plum Society"
    assert again.fields["city"] == str(places["karachi"].id)
    assert again.fields["locality"] == str(places["k1"].id)
    # only NEW values are judged: another city that is inactive, or moving the stored locality
    City.objects.filter(pk=places["lahore"].pk).update(is_active=False)
    assert refused(account, again, {"city": str(places["lahore"].id)}) == {"city": "invalid"}
    City.objects.filter(pk=places["lahore"].pk).update(is_active=True)
    # a stored (now inactive) locality cannot follow a change of city
    assert refused(
        account, again, {"city": str(places["lahore"].id), "locality": str(places["k1"].id)}
    ) == {"locality": "invalid"}
    moved = update(account, again, {"city": str(places["lahore"].id)})
    assert moved.fields["locality"] is None  # the pair is cleared as usual


def test_a_reference_is_never_judged_without_a_resolver(places):
    definition = registry.field("city")
    with pytest.raises(RuntimeError):
        validation.check_reference(definition, str(places["karachi"].id), None)
    with pytest.raises(RuntimeError):
        validation.evaluate("sale", "house", "house", {}, {"city": str(places["karachi"].id)})
    # no reference in the update: no resolver needed
    values, errors = validation.evaluate("sale", "house", "house", {}, {"society": "Plum"})
    assert (values, errors) == ({"society": "Plum"}, {})
    assert validation.evaluate("sale", "house", "house", {}, {"city": None})[0] == {"city": None}


def test_inactive_city_or_locality_is_invalid(places):
    account, view = draft()
    saved = update(account, view, pair(places))
    City.objects.filter(pk=places["lahore"].pk).update(is_active=False)
    assert refused(account, saved, {"city": str(places["lahore"].id)}) == {"city": "invalid"}
    Locality.objects.filter(pk=places["k2"].pk).update(is_active=False)
    assert refused(account, saved, {"locality": str(places["k2"].id)}) == {"locality": "invalid"}
    assert ListingLocation.objects.get().locality == places["k1"].id


def test_a_locality_of_another_city_is_locality_not_in_city(places):
    account, view = draft()
    before = counts()
    problems = refused(account, view, pair(places, "karachi", "l1"))
    assert problems == {"locality": "locality_not_in_city"}
    assert counts() == before
    assert "city" not in problems
    saved = update(account, view, {"city": str(places["karachi"].id)})
    assert refused(account, saved, {"locality": str(places["l1"].id)}) == {
        "locality": "locality_not_in_city"
    }
    assert ListingLocation.objects.get().locality is None


def test_the_message_for_the_distinct_code_is_static_and_neutral():
    message = field_message("locality_not_in_city")
    assert message == "Choose a locality that belongs to the selected city."
    assert field_message("out_of_range").startswith("Enter a number within the allowed range")


def test_a_locality_without_a_saved_city_is_invalid(places):
    account, view = draft()
    assert refused(account, view, {"locality": str(places["k1"].id)}) == {"locality": "invalid"}
    assert counts()[1] == 0


def test_a_locality_with_a_null_city_in_the_same_request_is_invalid(places):
    account, view = draft()
    saved = update(account, view, pair(places))
    assert refused(account, saved, {"city": None, "locality": str(places["k1"].id)}) == {
        "locality": "invalid"
    }


def test_changing_the_city_clears_the_locality_in_the_same_write(places):
    account, view = draft()
    saved = update(account, view, pair(places))
    moved = update(account, saved, {"city": str(places["lahore"].id)})
    assert moved.revision == 3
    assert moved.fields["city"] == str(places["lahore"].id) and moved.fields["locality"] is None
    assert "locality" not in moved.provenance
    row = ListingLocation.objects.get()
    assert (row.city, row.locality) == (places["lahore"].id, None)
    assert ListingRevision.objects.get(revision=3).changes == {
        "city": str(places["lahore"].id),
        "locality": None,
    }


def test_changing_the_city_with_a_locality_of_the_new_city_keeps_that_locality(places):
    account, view = draft()
    saved = update(account, view, pair(places))
    moved = update(account, saved, pair(places, "lahore", "l1"))
    assert (moved.fields["city"], moved.fields["locality"]) == (
        str(places["lahore"].id),
        str(places["l1"].id),
    )


def test_changing_the_city_with_a_locality_of_the_old_city_is_refused(places):
    account, view = draft()
    saved = update(account, view, pair(places))
    assert refused(account, saved, pair(places, "lahore", "k1")) == {
        "locality": "locality_not_in_city"
    }
    row = ListingLocation.objects.get()
    assert (row.city, row.locality) == (places["karachi"].id, places["k1"].id)


def test_clearing_the_city_clears_the_locality(places):
    account, view = draft()
    saved = update(account, view, pair(places))
    cleared = update(account, saved, {"city": None})
    assert cleared.fields["city"] is None and cleared.fields["locality"] is None
    assert ListingRevision.objects.get(revision=3).changes == {"city": None, "locality": None}


def test_saving_the_same_city_keeps_the_locality_and_changes_nothing(places):
    account, view = draft()
    saved = update(account, view, pair(places))
    again = update(account, saved, {"city": str(places["karachi"].id)})
    assert again.revision == 2 and again.fields["locality"] == str(places["k1"].id)


def test_choosing_another_locality_of_the_same_city(places):
    account, view = draft()
    saved = update(account, view, pair(places))
    moved = update(account, saved, {"locality": str(places["k2"].id)})
    assert moved.fields["locality"] == str(places["k2"].id) and moved.revision == 3


def test_clearing_only_the_locality_keeps_the_city(places):
    account, view = draft()
    saved = update(account, view, pair(places))
    cleared = update(account, saved, {"locality": None})
    assert (
        cleared.fields["city"] == str(places["karachi"].id) and cleared.fields["locality"] is None
    )


def test_every_purpose_and_subtype_accepts_a_location(places):
    for values in (
        ("sale", "house", "house"),
        ("rent", "apartment", "apartment"),
        ("sale", "plot", "plot_file"),
        ("rent", "commercial", "office"),
    ):
        account, view = draft(make_account(random_email()), values)
        assert update(account, view, pair(places)).fields["locality"] == str(places["k1"].id)


# --- public location text -----------------------------------------------------------------------


def test_public_text_is_trimmed_and_stored_on_the_location_row(places):
    account, view = draft()
    saved = update(
        account,
        view,
        {"society": "  Plum   Society ", "phase": "Phase 6", "sector": "C-2", "block": " B "},
    )
    assert saved.fields["society"] == "Plum Society" and saved.fields["block"] == "B"
    row = ListingLocation.objects.get()
    assert (row.society, row.phase, row.sector, row.block) == (
        "Plum Society",
        "Phase 6",
        "C-2",
        "B",
    )
    assert counts() == (1, 1, 0, 2)


def test_empty_public_text_is_null_and_never_creates_a_row(places):
    account, view = draft()
    for blank in ("", "   ", "\t", None):
        saved = update(account, view, {"society": blank})
        assert saved.revision == 1 and saved.fields["society"] is None
    assert counts()[1] == 0
    filled = update(account, view, {"society": "Plum Society"})
    cleared = update(account, filled, {"society": "  "})
    assert cleared.fields["society"] is None and cleared.revision == 3
    assert ListingLocation.objects.get().society is None  # the row stays


@pytest.mark.parametrize("name", ["society", "phase", "sector", "block", "street", "house_number"])
@pytest.mark.parametrize(
    "value", ["call 0300 1234567", "plum.fake@example.com", "mail plum.fake@example.com now"]
)
def test_contact_details_are_rejected_in_location_text(name, value):
    account, view = draft()
    assert refused(account, view, {name: value}) == {name: "contains_contact_details"}
    assert counts() == (1, 0, 0, 1)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("street", "Plum Street 12"),
        ("street", "Lane 4"),
        ("street", "14th Street"),
        ("house_number", "House 45-B"),
        ("house_number", "Flat 3"),
    ],
)
def test_the_private_address_fields_accept_street_addresses(name, value):
    account, view = draft()
    assert update(account, view, {name: value}).fields[name] == value


@pytest.mark.parametrize("name", ["society", "phase", "sector", "block"])
def test_public_location_text_still_refuses_a_street_address(name):
    account, view = draft()
    assert refused(account, view, {name: "Plum Street 12"}) == {name: "contains_contact_details"}


@pytest.mark.parametrize(
    ("name", "limit"),
    [
        ("society", 60),
        ("phase", 60),
        ("sector", 60),
        ("block", 60),
        ("street", 120),
        ("house_number", 30),
    ],
)
def test_text_length_limits(name, limit):
    account, view = draft()
    assert refused(account, view, {name: "a" * (limit + 1)}) == {name: "too_long"}
    assert update(account, view, {name: "a" * limit}).fields[name] == "a" * limit


@pytest.mark.parametrize("name", ["society", "street", "house_number"])
@pytest.mark.parametrize("value", [5, True, ["x"], {"a": 1}, "-- ..", "\x00x"])
def test_text_must_be_text_with_a_letter_or_digit(name, value):
    account, view = draft()
    assert refused(account, view, {name: value}) == {name: "invalid"}


# --- the private address ------------------------------------------------------------------------


def test_private_values_are_stored_in_the_separate_table_and_served_to_the_owner(places):
    account, view = draft()
    saved = update(
        account,
        view,
        {"street": STREET, "house_number": HOUSE_NO, "latitude": LAT, "longitude": LON},
    )
    assert saved.fields["street"] == STREET and saved.fields["house_number"] == HOUSE_NO
    assert (saved.fields["latitude"], saved.fields["longitude"]) == (LAT, LON)
    assert counts() == (1, 0, 1, 2)  # the public location row was not created
    row = ListingPrivateAddress.objects.get()
    assert (row.street, row.house_number) == (STREET, HOUSE_NO)
    assert (row.latitude, row.longitude) == (D(LAT), D(LON))
    owned = selectors.get_owned_listing(user(account), view.id)
    assert owned.fields["street"] == STREET and owned.fields["latitude"] == LAT
    page = selectors.list_owned(user(account))
    assert page.items[0].fields["house_number"] == HOUSE_NO


def test_the_revision_row_records_that_private_fields_changed_never_their_values(places):
    account, view = draft()
    saved = update(
        account,
        view,
        {
            "street": STREET,
            "house_number": HOUSE_NO,
            "latitude": LAT,
            "longitude": LON,
            "block": "B",
        },
    )
    changes = ListingRevision.objects.get(revision=2).changes
    assert changes == {
        "street": True,
        "house_number": True,
        "latitude": True,
        "longitude": True,
        "block": "B",
    }
    cleared = update(account, saved, {"street": None, "latitude": None, "longitude": None})
    assert ListingRevision.objects.get(revision=3).changes == {
        "street": None,
        "latitude": None,
        "longitude": None,
    }
    dump = str([r.changes for r in ListingRevision.objects.all()])
    for secret in (STREET, HOUSE_NO, "24.86", "67.01"):
        assert secret not in dump
    assert cleared.fields["house_number"] == HOUSE_NO


def test_a_clear_of_an_unset_private_value_is_a_no_op_and_creates_no_row():
    account, view = draft()
    for name in PRIVATE_ADDRESS_IDS:
        saved = update(account, view, {name: None})
        assert saved.revision == 1 and saved.fields[name] is None
    assert counts() == (1, 0, 0, 1)


def test_clearing_a_stored_private_value_keeps_the_row():
    account, view = draft()
    saved = update(account, view, {"street": STREET})
    cleared = update(account, saved, {"street": None})
    assert cleared.fields["street"] is None and cleared.revision == 3
    assert ListingPrivateAddress.objects.get().street is None


@pytest.mark.parametrize(
    ("updates", "missing"),
    [
        ({"latitude": LAT}, "longitude"),
        ({"longitude": LON}, "latitude"),
    ],
)
def test_one_coordinate_without_the_other_is_invalid_on_the_missing_one(updates, missing):
    account, view = draft()
    assert refused(account, view, updates) == {missing: "invalid"}
    assert counts() == (1, 0, 0, 1)


def test_coordinates_are_both_or_neither_on_the_resulting_state():
    account, view = draft()
    saved = update(account, view, {"latitude": LAT, "longitude": LON})
    assert refused(account, saved, {"latitude": None}) == {"latitude": "invalid"}
    assert refused(account, saved, {"longitude": None}) == {"longitude": "invalid"}
    moved = update(account, saved, {"latitude": "25.5"})
    assert (moved.fields["latitude"], moved.fields["longitude"]) == ("25.500000", LON)
    both = update(account, moved, {"latitude": None, "longitude": None})
    assert both.fields["latitude"] is None and both.fields["longitude"] is None


@pytest.mark.parametrize(
    ("name", "value", "code"),
    [
        ("latitude", "90.000001", "out_of_range"),
        ("latitude", "-90.000001", "out_of_range"),
        ("latitude", "91", "out_of_range"),
        ("latitude", "100", "out_of_range"),
        ("longitude", "180.000001", "out_of_range"),
        ("longitude", "-181", "out_of_range"),
        ("latitude", "24.8600001", "out_of_range"),  # seven decimals
        ("longitude", "67.0100001", "out_of_range"),
        ("latitude", 24.86, "invalid"),  # a JSON number is never accepted
        ("latitude", 24, "invalid"),
        ("longitude", True, "invalid"),
        ("latitude", "24,86", "invalid"),
        ("latitude", "1e1", "invalid"),
        ("latitude", " 24.86", "invalid"),
        ("latitude", "+24.86", "invalid"),
        ("latitude", "024.86", "invalid"),
        ("latitude", "--1", "invalid"),
        ("latitude", "NaN", "invalid"),
        ("latitude", "", "invalid"),
        ("latitude", None, None),
    ],
)
def test_coordinate_bounds_scale_and_shape(name, value, code):
    account, view = draft()
    other = "longitude" if name == "latitude" else "latitude"
    other_value = LON if name == "latitude" else LAT
    updates = {name: value, other: other_value}
    if code is None:
        assert update(account, view, {name: value}).revision == 1
        return
    assert refused(account, view, updates) == {name: code}
    assert counts() == (1, 0, 0, 1)


@pytest.mark.parametrize(
    ("name", "value", "stored"),
    [
        ("latitude", "90", "90.000000"),
        ("latitude", "-90", "-90.000000"),
        ("latitude", "-0", "0.000000"),
        ("latitude", "-0.000000", "0.000000"),
        ("longitude", "-0.000000", "0.000000"),
        ("longitude", "-0", "0.000000"),
        ("latitude", "0", "0.000000"),
        ("latitude", "-0.5", "-0.500000"),
        ("latitude", "24.123456", "24.123456"),
        ("longitude", "180", "180.000000"),
        ("longitude", "-180", "-180.000000"),
        ("longitude", "-67.01", "-67.010000"),
    ],
)
def test_coordinate_edges_are_stored_exactly(name, value, stored):
    account, view = draft()
    other = "longitude" if name == "latitude" else "latitude"
    saved = update(account, view, {name: value, other: "1"})
    assert saved.fields[name] == stored
    assert saved.fields[other] == "1.000000"


def test_every_location_field_applies_to_every_listing_and_null_clears_an_unset_one():
    """No location field can be `not_applicable` (all apply to every purpose and Subtype), so
    `null` is only ever a clear of a value that is not there: a no-op, no row, no revision."""
    for field_id in LOCATION_IDS:
        assert registry.field(field_id).applicability == registry.Applicability()
    account, view = draft(values=("rent", "commercial", "shop"))
    assert update(account, view, dict.fromkeys(LOCATION_IDS)).revision == 1
    assert counts() == (1, 0, 0, 1)


# --- privacy ------------------------------------------------------------------------------------


def owner_with_everything(places):
    account, view = draft()
    saved = update(
        account,
        view,
        {
            **pair(places),
            "society": "Plum Society",
            "street": STREET,
            "house_number": HOUSE_NO,
            "latitude": LAT,
            "longitude": LON,
        },
    )
    return account, saved


def test_nobody_but_the_owner_reads_the_draft_or_its_private_values(places):
    account, saved = owner_with_everything(places)
    other = make_account("other@example.com")
    admin = identity_services.provision_admin(
        SYSTEM, "root.admin@example.com", "mango river 4291 x"
    )
    for actor in (
        user(other),
        Actor.visitor("r"),
        Actor.system("r"),
        Actor.admin(admin.id, "r"),
    ):
        with pytest.raises(DomainError) as caught:
            selectors.get_owned_listing(actor, saved.id)
        assert caught.value.code in {"not_found", "unauthenticated"}
        with pytest.raises(DomainError):
            services.apply_updates(actor, saved.id, saved.revision, {"street": "x"})
    with pytest.raises(DomainError) as caught:
        selectors.public_listing(Actor.visitor("r"), saved.id)  # a Draft is not public
    assert caught.value.code == "not_found"


def test_the_public_projection_has_the_public_location_and_never_a_private_field(places):
    account, saved = owner_with_everything(places)
    set_listing_visibility(saved.id, "published")
    public = selectors.public_listing(Actor.visitor("r"), saved.id)
    shown = dict(public.fields)
    for name in PUBLIC_LOCATION_IDS:
        assert name in shown, name
    assert shown["city"] == str(places["karachi"].id) and shown["society"] == "Plum Society"
    for name in PRIVATE_ADDRESS_IDS:
        assert name not in shown, f"{name} reached the public projection"
    flat = str(public)
    for secret in (STREET, HOUSE_NO, "24.86", "67.01"):
        assert secret not in flat
    owner_view = selectors.get_owned_listing(user(account), saved.id)
    assert all(owner_view.fields[n] is not None for n in PRIVATE_ADDRESS_IDS)  # set, yet withheld


def test_the_public_read_never_touches_the_private_table(places):
    account, saved = owner_with_everything(places)
    set_listing_visibility(saved.id, "published")
    with CaptureQueriesContext(connection) as queries:
        selectors.public_listing(Actor.visitor("r"), saved.id)
    sql = " ".join(q["sql"] for q in queries.captured_queries)
    assert "listings_listingprivateaddress" not in sql
    assert "listings_listinglocation" in sql
    with CaptureQueriesContext(connection) as queries:
        selectors.get_owned_listing(user(account), saved.id)
    assert "listings_listingprivateaddress" in " ".join(q["sql"] for q in queries.captured_queries)


def test_a_private_field_cannot_become_public_by_a_registry_class_slip():
    """The guard bites on a real private field: `public_fields` drops it by class."""
    values = dict.fromkeys(LOCATION_IDS, "x")
    shown = selectors.public_fields(values)
    assert set(shown) == set(PUBLIC_LOCATION_IDS)


@pytest.fixture
def bus(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    seen = []
    events.subscribe(ListingModelInputsChanged, seen.append)
    return seen


def test_address_changes_emit_no_event_and_a_locality_change_emits_one(
    bus, places, django_capture_on_commit_callbacks
):
    account, view = draft()
    with django_capture_on_commit_callbacks(execute=True):
        saved = update(account, view, pair(places))  # the locality is a model input (Story 4.2)
    assert [event.listing_id for event in bus] == [view.id]
    bus.clear()
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        update(account, saved, {"street": STREET, "latitude": LAT, "longitude": LON})
    assert bus == [] and callbacks == []
    assert [n for n in LOCATION_IDS if registry.field(n).valuation_input] == ["locality"]


def test_canary_private_values_never_leave_the_owner_resource(places, caplog):
    caplog.set_level(logging.DEBUG)
    account, saved = owner_with_everything(places)
    secrets = (STREET, HOUSE_NO, LAT, LON, "24.86", "67.01")
    other = make_account("other@example.com")
    leaks = []

    def check(label, blob):
        leaks.extend(f"{label}: {s}" for s in secrets if s in blob)

    # a validation error and a stale revision as the owner never echo the stored or sent values
    with pytest.raises(services.FieldsInvalid) as caught:
        update(
            account,
            saved,
            {
                "street": f"{STREET} 0300 1234567",
                "house_number": "h" * 31 + HOUSE_NO,
                "latitude": LAT + "9",
                "longitude": "999" + LON,
            },
        )
    check("fields invalid", str(caught.value.fields) + str(caught.value) + repr(caught.value))
    assert set(caught.value.fields) == {"street", "house_number", "latitude", "longitude"}
    with pytest.raises(services.StaleRevision) as stale:
        update(account, saved, {"street": STREET, "latitude": LAT, "longitude": LON}, base=1)
    check("stale error", str(stale.value) + repr(stale.value) + repr(stale.value.current))
    # another account's attempts see nothing
    for attempt in (
        lambda: selectors.get_owned_listing(user(other), saved.id),
        lambda: services.apply_updates(user(other), saved.id, 1, {"street": STREET + "x"}),
        lambda: selectors.list_owned(user(other)),
    ):
        try:
            result = attempt()
        except DomainError as exc:
            check("non-owner error", str(exc))
        else:
            check("non-owner result", str(result))
    check("revisions", str([r.changes for r in ListingRevision.objects.all()]))
    check(
        "logs",
        " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records),
    )
    from apps.kernel.models import AuditRecord

    check("audit", str([(a.action, a.metadata) for a in AuditRecord.objects.all()]))
    assert leaks == []


# --- stale revisions and concurrency ----------------------------------------------------------


def test_a_stale_revision_is_a_conflict_and_writes_nothing(places):
    account, view = draft()
    update(account, view, {"society": "First Society"})
    before = counts()
    with pytest.raises(services.StaleRevision) as caught:
        update(account, view, {"street": STREET, **pair(places)}, base=1)
    assert caught.value.current.revision == 2
    assert caught.value.current.fields["street"] is None
    assert counts() == before


@pytest.mark.django_db(transaction=True)
def test_two_sessions_writing_the_location_make_one_success_and_one_conflict():
    seed_cap()
    city, _ = City.objects.get_or_create(name="Karachi")
    account, view = draft()
    barrier, outcomes = threading.Barrier(2), []

    def work(street):
        try:
            barrier.wait()
            saved = services.apply_updates(
                user(account), view.id, 1, {"street": street, "society": street}
            )
            outcomes.append(("saved", saved.fields["street"]))
        except services.StaleRevision as exc:
            outcomes.append(("stale", exc.current.revision))
        except Exception as exc:  # noqa: BLE001
            outcomes.append((type(exc).__name__, None))
        finally:
            connection.close()

    threads = [threading.Thread(target=work, args=(s,)) for s in ("Mango Lane", "Plum Lane")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)
    assert sorted(kind for kind, _ in outcomes) == ["saved", "stale"]
    winner = next(value for kind, value in outcomes if kind == "saved")
    assert PropertyListing.objects.get().revision == 2
    assert ListingPrivateAddress.objects.get().street == winner
    assert ListingLocation.objects.get().society == winner
    assert ListingRevision.objects.filter(revision=2).count() == 1
    assert city.pk


@pytest.mark.django_db(transaction=True)
def test_a_failure_while_saving_the_address_rolls_everything_back(places, monkeypatch):
    seed_cap()
    account, view = draft()
    original = ListingPrivateAddress.save

    def boom(self, *args, **kwargs):
        raise RuntimeError

    monkeypatch.setattr(ListingPrivateAddress, "save", boom)
    with pytest.raises(RuntimeError):
        update(account, view, {**pair(places), "society": "Plum Society", "street": STREET})
    monkeypatch.setattr(ListingPrivateAddress, "save", original)
    assert counts() == (1, 0, 0, 1)
    assert PropertyListing.objects.get().revision == 1


# --- the database and drift ---------------------------------------------------------------------


def listing_row():
    return PropertyListing.objects.get()


def test_the_database_refuses_unpaired_or_out_of_range_coordinates():
    account, view = draft()
    row = listing_row()
    for kwargs in (
        {"latitude": D("1")},
        {"longitude": D("1")},
        {"latitude": D("90.5"), "longitude": D("1")},
        {"latitude": D("1"), "longitude": D("-180.5")},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            ListingPrivateAddress.objects.create(listing=row, **kwargs)
    ListingPrivateAddress.objects.create(listing=row, latitude=D("-90"), longitude=D("180"))
    assert account


def test_the_database_refuses_a_locality_without_a_city():
    draft()
    with pytest.raises(IntegrityError), transaction.atomic():
        ListingLocation.objects.create(listing=listing_row(), locality=uuid.uuid4())
    ListingLocation.objects.create(listing=listing_row(), city=uuid.uuid4())


def test_the_location_tables_cascade_with_the_listing():
    draft()
    row = listing_row()
    ListingLocation.objects.create(listing=row, society="Plum")
    ListingPrivateAddress.objects.create(listing=row, street="x")
    row.delete()
    assert counts()[1:3] == (0, 0)


def _migrated(model):
    state = MigrationLoader(connection).project_state()
    return {c.name: c.condition for c in state.models[("listings", model)].options["constraints"]}


def _state_options(model):
    state = MigrationLoader(connection).project_state()
    return state.models[("listings", model)].options


def test_the_migration_matches_the_constraints_generated_from_the_registry():
    generated = {c.name: c.condition for c in address_constraints()}
    assert set(generated) == {
        "listings_addr_latitude_range",
        "listings_addr_longitude_range",
        "listings_addr_coordinates_paired",
    }
    assert _migrated("listingprivateaddress") == generated  # names AND conditions
    declared = {c.name: c.condition for c in ListingLocation._meta.constraints}
    assert _migrated("listinglocation") == declared
    assert set(declared) == {"listings_location_locality_needs_city"}
    indexes = {i.name: tuple(i.fields) for i in _state_options("listinglocation")["indexes"]}
    assert indexes == {"listings_location_city_idx": ("city", "locality")}
    assert indexes == {i.name: tuple(i.fields) for i in ListingLocation._meta.indexes}


@pytest.mark.parametrize(("name", "limit"), [("latitude", 90), ("longitude", 180)])
def test_the_registry_bounds_drive_the_validator_and_the_constraint(name, limit):
    """The edges are accepted by the service and the database, one step beyond is refused by
    both: a bound changed in the registry moves all three together."""
    definition = registry.field(name)
    assert (definition.minimum, definition.maximum) == (D(-limit), D(limit))
    assert definition.signed and definition.scale == 6
    other = "longitude" if name == "latitude" else "latitude"
    for edge in (limit, -limit):
        account, view = draft(make_account(random_email()))
        saved = update(account, view, {name: str(edge), other: "0"})
        assert saved.fields[name] == f"{edge}.000000"
    for beyond in (f"{limit}.000001", f"-{limit}.000001"):
        account, view = draft(make_account(random_email()))
        assert refused(account, view, {name: beyond, other: "0"}) == {name: "out_of_range"}
        with pytest.raises(IntegrityError), transaction.atomic():
            ListingPrivateAddress.objects.create(
                listing=PropertyListing.objects.get(id=view.id),
                **{name: D(beyond), other: D(0)},
            )


# --- review batch: defaults, repr, one-sided coordinates, the list cap ------------------------


def test_a_default_view_of_carries_no_private_field(places):
    account, saved = owner_with_everything(places)
    row = PropertyListing.objects.get(id=saved.id)
    plain = selectors.view_of(row)  # the default is the PUBLIC-safe shape
    for name in PRIVATE_ADDRESS_IDS:
        assert name not in plain.fields
    owner = selectors.view_of(row, with_private=True)
    assert all(owner.fields[n] is not None for n in PRIVATE_ADDRESS_IDS)
    with pytest.raises(TypeError):
        selectors.view_of(row, None, None, None, None, None, None, None, None, True)  # keyword-only


def test_the_repr_and_str_of_a_view_and_a_conflict_redact_private_values(places):
    account, saved = owner_with_everything(places)
    for text in (repr(saved), str(saved)):
        assert not any(n in text for n in (STREET, HOUSE_NO, LAT, LON, "24.86", "67.01"))
        assert "Plum Society" in text  # public values stay readable
    with pytest.raises(services.StaleRevision) as stale:
        update(account, saved, {"street": "Other Lane"}, base=1)
    for text in (str(stale.value), repr(stale.value), repr(stale.value.current)):
        assert not any(n in text for n in (STREET, HOUSE_NO, LAT, LON, "24.86", "67.01"))
    assert "<private>" in repr(stale.value.current)


def test_one_sided_coordinates_name_the_field_the_client_cleared_or_the_one_it_left_out():
    account, view = draft()
    # nothing stored: the one left out is reported
    assert refused(account, view, {"latitude": LAT}) == {"longitude": "invalid"}
    assert refused(account, view, {"longitude": LON}) == {"latitude": "invalid"}
    # both sent, one of them cleared: the field the client sent as null is reported
    assert refused(account, view, {"latitude": LAT, "longitude": None}) == {"longitude": "invalid"}
    assert refused(account, view, {"latitude": None, "longitude": LON}) == {"latitude": "invalid"}
    # both stored, one cleared: the cleared (sent) one
    saved = update(account, view, {"latitude": LAT, "longitude": LON})
    assert refused(account, saved, {"longitude": None}) == {"longitude": "invalid"}
    assert refused(account, saved, {"latitude": None}) == {"latitude": "invalid"}


def test_the_locality_list_is_capped(db):
    from apps.catalog import selectors as catalog

    city = City.objects.create(name="Capville")
    Locality.objects.bulk_create(
        Locality(city=city, name=f"Cap Place {i:04d}", kind="area")
        for i in range(catalog.LOCALITY_LIMIT + 5)
    )
    listed = catalog.list_localities(city.id)
    assert catalog.LOCALITY_LIMIT == 500 and len(listed) == 500
    assert listed[0].name == "Cap Place 0000"  # the first by name, deterministic
