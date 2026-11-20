"""Database constraints of the Listing tables, by direct insert (Story 2.1)."""

import uuid

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader

from apps.listings import models as listing_models
from apps.listings.models import ListingRevision, PropertyListing

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
