"""Database constraints of the Listing tables, by direct insert (Story 2.1)."""

import uuid

import pytest
from django.db import DataError, IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader

from apps.listings import models as listing_models
from apps.listings.models import ListingRevision, ListingTerms, PropertyListing

GOOD = {
    "owner_account_id": uuid.UUID(int=7),
    "status": "draft",
    "revision": 1,
    "registry_version": 1,
    "purpose": "sale",
    "category": "house",
    "subtype": "house",
}


def listing(**overrides):
    return PropertyListing.objects.create(**{**GOOD, **overrides})


def rejected(**overrides):
    with pytest.raises(IntegrityError), transaction.atomic():
        listing(**overrides)
    assert PropertyListing.objects.count() == 0


def test_the_good_row_is_accepted(db):
    assert listing().revision == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"purpose": "lease"},
        {"category": "castle"},
        {"category": "apartment", "subtype": "shop"},
        {"category": "plot", "subtype": "house"},
        {"status": "published"},
        {"revision": 0},
        {"registry_version": 0},
    ],
)
def test_listing_constraints(db, overrides):
    rejected(**overrides)


def test_revision_constraints(db):
    row = listing()
    base = {"listing": row, "origin": "manual", "actor_account_id": uuid.UUID(int=7), "changes": {}}
    ListingRevision.objects.create(revision=1, **base)
    with pytest.raises(IntegrityError), transaction.atomic():
        ListingRevision.objects.create(revision=1, **base)  # duplicate (listing, revision)
    with pytest.raises(IntegrityError), transaction.atomic():
        ListingRevision.objects.create(revision=2, **{**base, "origin": "robot"})
    assert ListingRevision.objects.count() == 1


def test_the_migration_matches_the_registry_taxonomy(db):
    """The taxonomy is frozen into CHECK constraints: a registry change needs a migration."""
    state = MigrationLoader(connection).project_state()
    options = state.models[("listings", "propertylisting")].options
    migrated = {c.name: c.condition for c in options["constraints"]}
    current = {c.name: c.condition for c in PropertyListing._meta.constraints}
    assert migrated == current
    assert listing_models._taxonomy_pairs() == migrated["listings_subtype_in_category"]


def test_a_changed_taxonomy_would_differ_from_the_migration(db, monkeypatch):
    from apps.catalog import registry

    state = MigrationLoader(connection).project_state()
    migrated = {
        c.name: c.condition
        for c in state.models[("listings", "propertylisting")].options["constraints"]
    }
    extra = registry.Category("villa", ("villa",))
    monkeypatch.setattr(registry, "categories", lambda: (*registry.load().categories, extra))
    assert listing_models._taxonomy_pairs() != migrated["listings_subtype_in_category"]


# --- editing columns (Story 2.2) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"title": "x" * 9}, "listings_title_length"),
        ({"description": "x" * 5001}, "listings_description_length"),
    ],
)
def test_text_length_checks_name_themselves(db, overrides, constraint):
    with pytest.raises(IntegrityError, match=constraint), transaction.atomic():
        listing(**overrides)
    assert PropertyListing.objects.count() == 0


def test_a_title_over_the_column_width_is_a_data_error_not_a_check(db):
    with pytest.raises(DataError), transaction.atomic():
        listing(title="x" * 101)


def test_text_columns_accept_null_and_the_bounds(db):
    assert listing(title=None, description=None).title is None
    PropertyListing.objects.all().delete()
    row = listing(title="x" * 10, description="y" * 5000)
    assert (len(row.title), len(row.description)) == (10, 5000)
    assert row.provenance == {}


@pytest.mark.parametrize(
    "overrides",
    [
        {"asking_price": 0},
        {"asking_price": "0.00"},
        {"asking_price": "-1.00"},
        {"asking_price": "1000000000000.00"},
        {"price_basis": "monthly"},
        {"rental_period": "decade"},
    ],
)
def test_terms_constraints(db, overrides):
    row = listing()
    with pytest.raises((IntegrityError, DataError)), transaction.atomic():
        ListingTerms.objects.create(listing=row, **overrides)
    assert ListingTerms.objects.count() == 0


def test_terms_accept_good_values_and_are_one_per_listing(db):
    row = listing()
    terms = ListingTerms.objects.create(
        listing=row, asking_price="999999999999.99", price_basis="per_unit", rental_period="week"
    )
    assert str(terms.asking_price) == "999999999999.99"
    with pytest.raises(IntegrityError), transaction.atomic():
        ListingTerms.objects.create(listing=row)


def test_the_revision_origin_may_be_ai_but_nothing_else(db):
    row = listing()
    base = {"listing": row, "actor_account_id": uuid.UUID(int=7), "changes": {}}
    ListingRevision.objects.create(revision=1, origin="ai", **base)
    with pytest.raises(IntegrityError), transaction.atomic():
        ListingRevision.objects.create(revision=2, origin="robot", **base)


def test_the_migration_matches_the_models_for_the_editing_tables(db):
    state = MigrationLoader(connection).project_state()
    for label, model in (
        ("propertylisting", PropertyListing),
        ("listingterms", ListingTerms),
        ("listingrevision", ListingRevision),
    ):
        migrated = {
            c.name: c.condition for c in state.models[("listings", label)].options["constraints"]
        }
        assert migrated == {c.name: c.condition for c in model._meta.constraints}


def _migrated(label):
    state = MigrationLoader(connection).project_state()
    return {c.name: c.condition for c in state.models[("listings", label)].options["constraints"]}


def test_migration_0002_carries_the_current_registry_bounds_and_enums(db):

    from apps.listings.models import terms_constraints, text_constraints

    generated = {c.name: c.condition for c in [*text_constraints(), *terms_constraints()]}
    migrated = {**_migrated("propertylisting"), **_migrated("listingterms")}
    for name, condition in generated.items():
        assert migrated[name] == condition, name
    price = migrated["listings_terms_price_range"]
    assert "0.01" in repr(price)
    assert "999999999999.99" in repr(price)
    assert "['total', 'per_unit', 'rental_period']" in repr(migrated["listings_terms_basis_valid"])
    assert "['day', 'week', 'month', 'year']" in repr(migrated["listings_terms_period_valid"])
    assert "10" in repr(migrated["listings_title_length"])
    assert "100" in repr(migrated["listings_title_length"])
    assert "5000" in repr(migrated["listings_description_length"])


def test_a_loosened_or_tightened_registry_bound_would_differ_from_the_migration(db, monkeypatch):
    import dataclasses

    from apps.catalog import registry
    from apps.listings.models import terms_constraints, text_constraints

    real = registry.field
    changes = {
        "title": {"max_length": 120},
        "description": {"max_length": 6000},
        "asking_price": {"maximum": registry.field("asking_price").maximum / 2},
        "price_basis": {"allowed_values": ("total", "per_unit", "rental_period", "lease")},
        "rental_period": {"allowed_values": ("day", "week", "month")},
    }
    migrated = {**_migrated("propertylisting"), **_migrated("listingterms")}
    for field_id, change in changes.items():
        monkeypatch.setattr(
            registry,
            "field",
            lambda name, fid=field_id, ch=change: (
                dataclasses.replace(real(name), **ch) if name == fid else real(name)
            ),
        )
        drifted = {c.name: c.condition for c in [*text_constraints(), *terms_constraints()]}
        assert any(migrated[name] != drifted[name] for name in drifted), field_id
