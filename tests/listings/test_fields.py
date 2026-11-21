"""The single map of editable fields (`listings.fields`) against the registry and storage."""

from apps.catalog import registry
from apps.listings import fields, selectors
from apps.listings.models import ListingArea, ListingTerms, PropertyListing
from apps.listings.validation import EDITABLE_FIELDS as VALIDATED


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
    }
    for name, table in {**fields.STORAGE, **fields.DERIVED}.items():
        assert table in tables, name
        assert tables[table]._meta.get_field(name), name
    parts = [fields.LISTING_FIELDS, fields.TERMS_FIELDS, fields.AREA_FIELDS]
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
    assert fields.WIRE_FIELDS[-4:] == ("area_value", "area_unit", "marla_basis", "area_sqft")
    assert fields.WIRE_FIELDS.index("rental_period") + 1 == fields.WIRE_FIELDS.index("area_value")
