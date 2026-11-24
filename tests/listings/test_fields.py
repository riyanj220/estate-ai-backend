"""The single map of editable fields (`listings.fields`) against the registry and storage."""

import datetime
from decimal import Decimal

from django.db import connection, models

from apps.catalog import registry
from apps.listings import fields, selectors
from apps.listings.models import (
    ListingArea,
    ListingCommercial,
    ListingPlot,
    ListingResidential,
    ListingTerms,
    PropertyListing,
)
from apps.listings.validation import EDITABLE_FIELDS as VALIDATED
from tests.listings.support import (
    COMMERCIAL_FIELDS,
    PLOT_FIELDS,
    RESIDENTIAL_IDS,
    TAIL_IDS,
    UNIT_IDS,
)


def test_every_registry_field_has_exactly_one_storage_route_and_vice_versa():
    defined = {f.id for f in registry.fields()} - set(fields.IDENTITY_FIELDS) - set(fields.DERIVED)
    mapped = set(fields.STORAGE)
    assert not defined - mapped, (
        f"registry field(s) {sorted(defined - mapped)} have no storage route: add them to "
        "`apps/listings/fields.py` STORAGE (and a column plus migration)"
    )
    assert not mapped - defined, (
        f"STORAGE maps {sorted(mapped - defined)}, which the registry does not define"
    )
    assert len(fields.EDITABLE_FIELDS) == len(set(fields.EDITABLE_FIELDS)) == len(mapped)


def test_each_mapped_field_is_a_column_of_its_table():
    tables = {
        fields.LISTING: PropertyListing,
        fields.TERMS: ListingTerms,
        fields.AREA: ListingArea,
        fields.RESIDENTIAL: ListingResidential,
        fields.PLOT: ListingPlot,
        fields.COMMERCIAL: ListingCommercial,
    }
    for name, table in {**fields.STORAGE, **fields.DERIVED}.items():
        assert table in tables, name
        assert tables[table]._meta.get_field(name), name
    parts = [
        fields.LISTING_FIELDS,
        fields.TERMS_FIELDS,
        fields.AREA_FIELDS,
        fields.RESIDENTIAL_FIELDS,
        fields.PLOT_FIELDS,
        fields.COMMERCIAL_FIELDS,
    ]
    assert set().union(*parts) == set(fields.EDITABLE_FIELDS)
    assert sum(len(part) for part in parts) == len(fields.EDITABLE_FIELDS)  # one table each


def test_identity_fields_are_never_editable():
    assert not set(fields.IDENTITY_FIELDS) & set(fields.EDITABLE_FIELDS)
    for name in fields.IDENTITY_FIELDS:
        assert PropertyListing._meta.get_field(name)


def test_validation_selectors_and_the_wire_all_follow_the_map():
    assert VALIDATED == fields.EDITABLE_FIELDS
    assert selectors.TERMS_FIELDS == fields.TERMS_FIELDS
    assert fields.WIRE_FIELDS == (
        *fields.IDENTITY_FIELDS,
        *fields.EDITABLE_FIELDS,
        *fields.DERIVED_FIELDS,
    )


def test_derived_fields_are_the_registrys_derived_fields_and_never_editable():
    flagged = {f.id for f in registry.fields() if f.derived}
    assert flagged == set(fields.DERIVED) == {"area_sqft"}, (
        "a registry field flagged `derived` must be listed in `listings.fields.DERIVED` (and the "
        "other way round)"
    )
    assert not flagged & set(fields.EDITABLE_FIELDS)
    assert not flagged & set(fields.STORAGE)
    for name in flagged:
        definition = registry.field(name)
        assert not (definition.ai_extractable or definition.valuation_input)
        assert definition.requirement == "optional"
    assert fields.WIRE_FIELDS[-1] == "area_sqft"
    assert fields.WIRE_FIELDS.index("rental_period") + 1 == fields.WIRE_FIELDS.index("area_value")
    assert fields.WIRE_FIELDS[-1 - len(TAIL_IDS) : -1] == TAIL_IDS


def test_the_unit_plot_and_commercial_fields_are_the_registrys_details_in_registry_order():
    assert fields.RESIDENTIAL_FIELDS == UNIT_IDS
    assert fields.PLOT_FIELDS == PLOT_FIELDS
    assert fields.COMMERCIAL_FIELDS == COMMERCIAL_FIELDS
    registry_ids = [f.id for f in registry.fields()]
    assert registry_ids[-len(TAIL_IDS) :] == list(TAIL_IDS)
    assert tuple(fields.STORAGE)[-len(TAIL_IDS) :] == TAIL_IDS
    assert UNIT_IDS[: len(RESIDENTIAL_IDS)] == RESIDENTIAL_IDS
    tables = {
        fields.RESIDENTIAL: (ListingResidential, UNIT_IDS),
        fields.PLOT: (ListingPlot, PLOT_FIELDS),
        fields.COMMERCIAL: (ListingCommercial, COMMERCIAL_FIELDS),
    }
    for table, (model, names) in tables.items():
        for name in names:
            column = model._meta.get_field(name)
            assert column.null is True and column.default is models.NOT_PROVIDED, name
            assert fields.STORAGE[name] == table, name
            assert registry.field(name).field_class == "public", name
            claim = registry.field(name).seller_declared
            # legal and possession claims are never extracted by the Assistant
            assert registry.field(name).ai_extractable is not claim, name


def test_every_decimal_money_integer_and_date_field_fits_its_column():
    tables = {
        fields.LISTING: PropertyListing,
        fields.TERMS: ListingTerms,
        fields.AREA: ListingArea,
        fields.RESIDENTIAL: ListingResidential,
        fields.PLOT: ListingPlot,
        fields.COMMERCIAL: ListingCommercial,
    }
    checked = dict.fromkeys(("decimal", "money", "integer", "date"), 0)
    for definition in registry.fields():
        if definition.type not in checked or definition.id in fields.IDENTITY_FIELDS:
            continue
        table = {**fields.STORAGE, **fields.DERIVED}[definition.id]
        column = tables[table]._meta.get_field(definition.id)
        name = definition.id
        if definition.type in ("decimal", "money"):
            assert isinstance(column, models.DecimalField), name
            scale = definition.scale if definition.type == "decimal" else registry.MONEY_DECIMALS
            assert scale <= column.decimal_places, f"{name}: scale {scale} exceeds its column"
            ceiling = Decimal(10) ** (column.max_digits - column.decimal_places)
            assert definition.maximum < ceiling, f"{name}: max does not fit its column"
            assert definition.minimum > -ceiling, f"{name}: min does not fit its column"
        elif definition.type == "integer":
            assert isinstance(column, models.IntegerField | models.SmallIntegerField), name
            low, high = connection.ops.integer_field_range(column.get_internal_type())
            assert low <= definition.minimum <= definition.maximum <= high, (
                f"{name}: its bounds do not fit the {column.get_internal_type()} column"
            )
        else:
            assert type(column) is models.DateField, (
                f"{name}: a date is a SQL DATE, never a datetime"
            )
            for bound in (definition.minimum, definition.maximum):
                assert type(bound) is datetime.date, name  # not a datetime, not a Decimal
                assert datetime.date.min <= bound <= datetime.date.max
        checked[definition.type] += 1
    assert checked["decimal"] + checked["money"] >= 11
    assert checked["integer"] >= 20 and checked["date"] == 1
