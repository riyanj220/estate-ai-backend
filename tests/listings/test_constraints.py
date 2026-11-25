"""Database constraints of the Listing tables, by direct insert (Story 2.1)."""

import uuid

import pytest
from django.db import DataError, IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader

from apps.listings import models as listing_models
from apps.listings.models import ListingArea, ListingRevision, ListingTerms, PropertyListing

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
        {"status": "pending"},
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


# --- area (Story 2.3) ---------------------------------------------------------------------------


def area_row(**overrides):
    return ListingArea.objects.create(
        listing=overrides.pop("listing", None) or listing(), **overrides
    )


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"area_value": 0}, "listings_area_value_range"),
        ({"area_value": "0.0000"}, "listings_area_value_range"),
        ({"area_value": "-1"}, "listings_area_value_range"),
        ({"area_unit": "hectare"}, "listings_area_unit_valid"),
        ({"area_unit": "marla", "marla_basis": "300"}, "listings_area_basis_valid"),
        ({"area_unit": "sqft", "marla_basis": "225"}, "listings_area_basis_applies"),
        ({"marla_basis": "225"}, "listings_area_basis_applies"),
        ({"area_sqft": "100"}, "listings_area_sqft_consistent"),  # no value or unit
        ({"area_value": "5", "area_sqft": "5"}, "listings_area_sqft_consistent"),  # no unit
        ({"area_unit": "sqft", "area_sqft": "5"}, "listings_area_sqft_consistent"),  # no value
        (
            {"area_value": "5", "area_unit": "marla", "area_sqft": "1125"},
            "listings_area_sqft_consistent",  # a marla without its basis is not computable
        ),
        (
            {"area_value": "5", "area_unit": "sqft", "area_sqft": "0"},
            "listings_area_sqft_consistent",
        ),
    ],
)
def test_area_constraints_name_themselves(db, overrides, constraint):
    with pytest.raises(IntegrityError, match=constraint), transaction.atomic():
        area_row(**overrides)
    assert ListingArea.objects.count() == 0


def test_area_over_the_column_width_is_a_data_error(db):
    with pytest.raises(DataError), transaction.atomic():
        area_row(area_value="10000000000")


def test_area_accepts_the_bounds_and_consistent_rows(db):
    good = [
        {},
        {"area_value": "0.0001", "area_unit": "sqft", "area_sqft": "0.0001"},
        {"area_value": "9999999999.9999", "area_unit": "acre", "area_sqft": "435599999999995.644"},
        {"area_value": "5", "area_unit": "marla"},  # unknown basis: sqft stays null
        {"area_value": "5", "area_unit": "marla", "marla_basis": "225", "area_sqft": "1125"},
        {"area_value": "1", "area_unit": "kanal", "marla_basis": "272.25", "area_sqft": "5445"},
    ]
    for overrides in good:
        ListingArea.objects.all().delete()
        PropertyListing.objects.all().delete()
        assert area_row(**overrides).pk


def test_area_is_numeric_in_the_database_and_one_per_listing(db):
    row = listing()
    area_row(listing=row, area_value="1.5", area_unit="sqft", area_sqft="1.5")
    with pytest.raises(IntegrityError), transaction.atomic():
        area_row(listing=row)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, data_type, numeric_precision, numeric_scale "
            "FROM information_schema.columns WHERE table_name = 'listings_listingarea' "
            "AND data_type = 'numeric' ORDER BY column_name"
        )
        assert cursor.fetchall() == [
            ("area_sqft", "numeric", 20, 4),
            ("area_value", "numeric", 14, 4),
            ("marla_basis", "numeric", 6, 2),
        ]


def test_migration_0003_matches_the_models_and_the_registry(db):
    from apps.listings.models import area_constraints

    migrated = _migrated("listingarea")
    assert migrated == {c.name: c.condition for c in ListingArea._meta.constraints}
    assert migrated == {c.name: c.condition for c in area_constraints()}
    text = repr(migrated["listings_area_value_range"])
    assert "0.0001" in text and "9999999999.9999" in text
    assert "['sqft', 'sqyd', 'marla', 'kanal', 'acre']" in repr(
        migrated["listings_area_unit_valid"]
    )
    assert all(
        value in repr(migrated["listings_area_basis_valid"]) for value in ("225", "250", "272.25")
    )
    assert "['marla', 'kanal']" in repr(migrated["listings_area_basis_applies"])
    assert "9999999999999999.9999" in repr(migrated["listings_area_sqft_consistent"])


def test_a_changed_area_bound_enum_or_unit_set_would_differ_from_the_migration(db, monkeypatch):
    import dataclasses

    from apps.catalog import registry
    from apps.listings import area as area_module
    from apps.listings.models import area_constraints

    migrated = _migrated("listingarea")
    real = registry.field
    changes = {
        "area_value": {"maximum": registry.field("area_value").maximum / 2},
        "area_unit": {"allowed_values": ("sqft", "sqyd", "marla", "kanal")},
        "marla_basis": {"allowed_values": ("225", "250")},
        "area_sqft": {"maximum": registry.field("area_sqft").maximum / 2},
    }
    for field_id, change in changes.items():
        monkeypatch.setattr(
            registry,
            "field",
            lambda name, fid=field_id, ch=change: (
                dataclasses.replace(real(name), **ch) if name == fid else real(name)
            ),
        )
        drifted = {c.name: c.condition for c in area_constraints()}
        assert any(migrated[name] != drifted[name] for name in drifted), field_id
    monkeypatch.setattr(registry, "field", real)
    monkeypatch.setattr(area_module, "MARLA_UNITS", ("marla",))
    drifted = {c.name: c.condition for c in area_constraints()}
    assert any(migrated[name] != drifted[name] for name in drifted)


def test_published_is_a_valid_status_and_restricted_defaults_to_false(db):
    """Story 2.12: the status CHECK allows `published`; `restricted` is not null, default false."""
    row = listing(status="published")
    assert row.restricted is False
    assert PropertyListing.objects.get(pk=row.pk).restricted is False
    with pytest.raises(IntegrityError), transaction.atomic():
        PropertyListing.objects.filter(pk=row.pk).update(restricted=None)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'listings_propertylisting' AND column_name = 'restricted'"
        )
        assert cursor.fetchone() == ("NO",)


@pytest.mark.django_db(transaction=True)
def test_migration_0009_gives_existing_rows_restricted_false_and_adds_the_public_index():
    """A populated table at the 0008 state: rows that exist before the migration read as not
    restricted afterwards, and the status CHECK then allows `published`."""
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor

    conn = connections["default"]
    executor = MigrationExecutor(conn)
    try:
        executor.migrate([("listings", "0008_cover")])
        old = MigrationExecutor(conn).loader.project_state([("listings", "0008_cover")]).apps
        before = old.get_model("listings", "PropertyListing")
        assert not hasattr(before, "restricted")
        old_ids = []
        for n in range(3):
            row = before.objects.create(**{**GOOD, "owner_account_id": uuid.UUID(int=100 + n)})
            old_ids.append(row.pk)
        MigrationExecutor(conn).migrate([("listings", "0009_visibility")])
        # the model as of 0009 (later migrations add columns the live model already reads)
        after = MigrationExecutor(conn).loader.project_state([("listings", "0009_visibility")]).apps
        listing = after.get_model("listings", "PropertyListing")
        rows = list(listing.objects.filter(pk__in=old_ids))
        assert len(rows) == 3
        assert [r.restricted for r in rows] == [False, False, False]
        assert {r.status for r in rows} == {"draft"}
        listing.objects.create(**{**GOOD, "status": "published"})  # CHECK now allows it
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'listings_public_idx'"
            )
            (definition,) = cursor.fetchone()
        assert "status, restricted, owner_account_id" in definition
    finally:
        MigrationExecutor(conn).migrate(MigrationExecutor(conn).loader.graph.leaf_nodes())


def test_the_expiry_notice_columns_are_both_set_or_both_null(db):
    from django.utils import timezone

    now = timezone.now()
    both = listing(expiry_notice_sent_at=now, delete_after=now)  # paired: fine
    none = listing()
    for half in ({"expiry_notice_sent_at": now}, {"delete_after": now}):
        with pytest.raises(IntegrityError), transaction.atomic():
            listing(**half)
        with pytest.raises(IntegrityError), transaction.atomic():
            PropertyListing.objects.filter(id=none.id).update(**half)
    with pytest.raises(IntegrityError), transaction.atomic():
        PropertyListing.objects.filter(id=both.id).update(delete_after=None)
    assert PropertyListing.objects.get(id=none.id).delete_after is None


@pytest.mark.django_db(transaction=True)
def test_migration_0012_keeps_existing_rows_and_adds_the_nullable_retention_columns():
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor

    conn = connections["default"]
    try:
        MigrationExecutor(conn).migrate([("listings", "0011_amenities")])
        old = MigrationExecutor(conn).loader.project_state([("listings", "0011_amenities")]).apps
        before = old.get_model("listings", "PropertyListing")
        assert not hasattr(before, "last_opened_at")
        ids = [
            before.objects.create(**{**GOOD, "owner_account_id": uuid.UUID(int=300 + n)}).pk
            for n in range(3)
        ]
        MigrationExecutor(conn).migrate([("listings", "0012_draft_retention")])
        new = (
            MigrationExecutor(conn)
            .loader.project_state([("listings", "0012_draft_retention")])
            .apps.get_model("listings", "PropertyListing")
        )
        rows = list(new.objects.filter(pk__in=ids))
        assert len(rows) == 3
        for row in rows:
            assert (row.last_opened_at, row.expiry_notice_sent_at, row.delete_after) == (
                None,
                None,
                None,
            )
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT indexname FROM pg_indexes WHERE indexname IN "
                "('listings_draft_idle_idx', 'listings_draft_expiry_idx') ORDER BY 1"
            )
            assert [r[0] for r in cursor.fetchall()] == [
                "listings_draft_expiry_idx",
                "listings_draft_idle_idx",
            ]
    finally:
        MigrationExecutor(conn).migrate(MigrationExecutor(conn).loader.graph.leaf_nodes())
