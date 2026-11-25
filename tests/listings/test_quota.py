"""Publication quota policy, events and the allowance (Story 2.14)."""

import importlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.db import IntegrityError, connections, transaction
from django.db.migrations.executor import MigrationExecutor

from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.listings import quota
from apps.listings.models import ListingQuotaPolicy, PublicationEvent
from tests.listings.support import make_account, user

EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
ACCOUNT = uuid.UUID(int=11)
OTHER = uuid.UUID(int=12)
NOV_NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
DEC_START = datetime(2026, 11, 30, 19, 0, tzinfo=UTC)  # 2026-12-01 00:00 Karachi
SECOND = timedelta(seconds=1)


def event(created_at, kind="initial", counted=True, account=ACCOUNT, **extra):
    return PublicationEvent.objects.create(
        account_id=account,
        listing_id=uuid.uuid4(),
        kind=kind,
        counted=counted,
        created_at=created_at,
        **extra,
    )


def rejected(model, **fields):
    with pytest.raises(IntegrityError), transaction.atomic():
        model.objects.create(**fields)


def policy(account_type="individual", limit=3, start=EPOCH, end=None):
    return ListingQuotaPolicy.objects.create(
        account_type=account_type, monthly_limit=limit, effective_from=start, effective_to=end
    )


# policy table


def test_the_seed_is_two_and_ten_open_ended_from_2000(db):
    rows = {p.account_type: p for p in ListingQuotaPolicy.objects.all()}
    assert {k: v.monthly_limit for k, v in rows.items()} == {"individual": 2, "agency": 10}
    assert all(p.effective_from == EPOCH and p.effective_to is None for p in rows.values())


MIGRATION = ("listings", "0013_quota_policy_and_events")
BEFORE = ("listings", "0012_draft_retention")


def drop_extension_if_owned(conn):
    """Drop btree_gist when the migrator owns it (a database whose extension was created by the
    superuser keeps it: the migration must then only find it present)."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'btree_gist' "
            "AND pg_get_userbyid(extowner) = current_user"
        )
        if cursor.fetchone():
            cursor.execute("DROP EXTENSION btree_gist")


def seed_with(apps):
    importlib.import_module("apps.listings.migrations.0013_quota_policy_and_events").seed_policies(
        apps, None
    )


def test_the_migration_declares_the_extension_before_any_table(db):
    from django.contrib.postgres.operations import BtreeGistExtension
    from django.db.migrations.operations.models import CreateModel

    operations = (
        MigrationExecutor(connections["default"]).loader.get_migration(*MIGRATION).operations
    )
    first_model = next(i for i, op in enumerate(operations) if isinstance(op, CreateModel))
    extension = next(i for i, op in enumerate(operations) if isinstance(op, BtreeGistExtension))
    assert extension < first_model
    assert operations[extension].name == "btree_gist"


@pytest.mark.django_db(transaction=True)
def test_the_migration_runs_forward_twice_and_the_seed_is_idempotent():
    conn = connections["default"]
    try:
        for _ in range(2):  # forward, back (drops the tables), forward again from nothing
            MigrationExecutor(conn).migrate([BEFORE])
            drop_extension_if_owned(conn)  # so the migration must provision it itself
            MigrationExecutor(conn).migrate([MIGRATION])
            seeded = {p.account_type: p.monthly_limit for p in ListingQuotaPolicy.objects.all()}
            assert seeded == {"individual": 2, "agency": 10}
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")
                assert cursor.fetchone() is not None
        ListingQuotaPolicy.objects.filter(account_type="agency").update(monthly_limit=12)
        historical = MigrationExecutor(conn).loader.project_state([MIGRATION]).apps
        seed_with(historical)
        seed_with(historical)  # the historical models, run twice
        assert ListingQuotaPolicy.objects.count() == 2
        assert ListingQuotaPolicy.objects.get(account_type="agency").monthly_limit == 12
    finally:
        MigrationExecutor(conn).migrate(MigrationExecutor(conn).loader.graph.leaf_nodes())
        if not ListingQuotaPolicy.objects.exists():
            from django.apps import apps

            seed_with(apps)  # leave the shared database seeded for the tests after this one


@pytest.mark.parametrize(
    "fields",
    [
        {"account_type": "admin"},
        {"account_type": "individual", "monthly_limit": -1},
        {"effective_to": EPOCH},  # equal to the start
        {"effective_to": EPOCH - SECOND},  # before the start
    ],
    ids=["bad_type", "negative_limit", "empty_range", "reversed_range"],
)
def test_policy_checks_reject_bad_rows(db, fields):
    ListingQuotaPolicy.objects.all().delete()
    base = {
        "account_type": "individual",
        "monthly_limit": 1,
        "effective_from": EPOCH,
        "effective_to": None,
    }
    rejected(ListingQuotaPolicy, **{**base, **fields})
    assert ListingQuotaPolicy.objects.count() == 0


def test_a_zero_limit_is_allowed(db):
    ListingQuotaPolicy.objects.all().delete()
    assert policy(limit=0).monthly_limit == 0


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2030, 1, 1, tzinfo=UTC), None),  # open-ended overlaps the open-ended seed
        (datetime(2010, 1, 1, tzinfo=UTC), datetime(2011, 1, 1, tzinfo=UTC)),  # inside it
        (datetime(1999, 1, 1, tzinfo=UTC), datetime(2000, 1, 2, tzinfo=UTC)),  # straddles start
    ],
    ids=["open_later", "inside", "straddles_start"],
)
def test_overlapping_ranges_of_one_type_are_rejected_by_the_database(db, start, end):
    rejected(
        ListingQuotaPolicy,
        account_type="individual",
        monthly_limit=5,
        effective_from=start,
        effective_to=end,
    )
    assert ListingQuotaPolicy.objects.filter(account_type="individual").count() == 1


def test_adjacent_ranges_and_other_types_do_not_overlap(db):
    cut = datetime(2027, 1, 1, tzinfo=UTC)
    ListingQuotaPolicy.objects.filter(account_type="individual").update(effective_to=cut)
    policy(limit=4, start=cut)  # starts exactly where the old one ends
    rows = ListingQuotaPolicy.objects
    assert rows.filter(account_type="individual").count() == 2
    assert rows.filter(account_type="agency").count() == 1  # untouched by the other type


def test_limit_resolution_at_the_edges_and_after_a_policy_change(db):
    cut = datetime(2027, 1, 1, tzinfo=UTC)
    ListingQuotaPolicy.objects.filter(account_type="individual").update(effective_to=cut)
    policy(limit=5, start=cut)
    at = quota.quota_limit_at
    assert at("individual", EPOCH) == 2  # exactly at effective_from
    assert at("individual", cut - SECOND) == 2
    assert at("individual", cut) == 5  # effective_to is exclusive, the next row starts
    assert (
        ListingQuotaPolicy.objects.filter(
            effective_from__lte=cut, account_type="individual"
        ).count()
        == 2
    )
    assert ListingQuotaPolicy.objects.filter(effective_to__gt=cut).count() == 0  # the old one ended
    assert at("individual", cut + SECOND) == 5
    assert at("agency", cut) == 10
    # An old month still resolves to the old limit when asked for an old instant.
    assert allowance(NOV_NOW).limit == 2
    assert allowance(cut + timedelta(days=3)).limit == 5


def test_a_missing_policy_is_a_clear_internal_error(db):
    ListingQuotaPolicy.objects.all().delete()
    with pytest.raises(quota.QuotaPolicyMissing):
        quota.quota_limit_at("individual", NOV_NOW)
    ListingQuotaPolicy.objects.all().delete()
    policy(start=datetime(2030, 1, 1, tzinfo=UTC))
    with pytest.raises(quota.QuotaPolicyMissing):
        quota.quota_limit_at("individual", NOV_NOW)  # before the first row


# events table


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "publish"},
        {"kind": "republish_free", "counted": True},
        {"kind": "restore", "counted": True},
        {"kind": "seed", "counted": True},
    ],
    ids=["bad_kind", "free_counted", "restore_counted", "seed_counted"],
)
def test_event_checks_reject_bad_rows(db, fields):
    base = {"account_id": ACCOUNT, "listing_id": uuid.uuid4(), "kind": "initial", "counted": False}
    rejected(PublicationEvent, **{**base, **fields})
    assert PublicationEvent.objects.count() == 0


def test_a_counted_kind_may_be_uncounted_and_defaults_hold(db):
    row = event(NOV_NOW, kind="initial", counted=False)
    assert (row.correction_used, row.identity_fingerprint, row.idempotency_key) == (
        False,
        None,
        None,
    )


def test_the_idempotency_key_is_unique_per_account_and_optional(db):
    event(NOV_NOW, idempotency_key="k1")
    event(NOV_NOW, idempotency_key="k1", account=OTHER)  # another account may reuse it
    event(NOV_NOW)
    event(NOV_NOW)  # no key: never a conflict
    with pytest.raises(IntegrityError), transaction.atomic():
        event(NOV_NOW, idempotency_key="k1")


def test_events_have_no_foreign_key_to_a_listing(db):
    fields = {f.name: f for f in PublicationEvent._meta.get_fields()}
    assert not any(f.is_relation for f in fields.values())


# allowance


def allowance(now, account=ACCOUNT, account_type="individual"):
    return quota.allowance_for(account, account_type, now)


def test_an_empty_table_leaves_the_whole_limit(db):
    for account_type, limit in (("individual", 2), ("agency", 10)):
        got = allowance(NOV_NOW, account_type=account_type)
        assert (got.limit, got.used, got.remaining) == (limit, 0, limit)


def test_the_period_is_the_karachi_month_with_offset_aware_bounds(db):
    got = allowance(NOV_NOW)
    assert got.period_start == datetime(2026, 10, 31, 19, 0, tzinfo=UTC)
    assert got.resets_at == DEC_START
    assert got.period_start.utcoffset() == timedelta(hours=5)
    assert got.resets_at.isoformat() == "2026-12-01T00:00:00+05:00"


def test_counted_events_inside_the_month_are_used(db):
    event(NOV_NOW)
    event(NOV_NOW + timedelta(days=1), kind="republish_counted")
    got = allowance(NOV_NOW, account_type="agency")
    assert (got.used, got.remaining, got.limit) == (2, 8, 10)


def test_only_counted_events_of_this_account_this_month_count(db):
    event(NOV_NOW, kind="republish_free", counted=False)
    event(NOV_NOW, kind="restore", counted=False)
    event(NOV_NOW, kind="seed", counted=False)
    event(NOV_NOW, counted=False)
    event(NOV_NOW, account=OTHER)
    event(NOV_NOW - timedelta(days=40))  # last month
    event(NOV_NOW + timedelta(days=40))  # a later month
    assert allowance(NOV_NOW).used == 0
    assert allowance(NOV_NOW, account=OTHER).used == 1
    event(NOV_NOW)
    assert allowance(NOV_NOW).used == 1


def test_the_karachi_month_boundary_is_exact(db):
    event(DEC_START - SECOND)  # still November in Karachi
    event(DEC_START)  # December
    assert allowance(DEC_START - SECOND).used == 1
    assert allowance(DEC_START).used == 1
    assert allowance(DEC_START - SECOND).resets_at == DEC_START
    assert allowance(DEC_START).period_start == DEC_START
    # The UTC month would put both events in November: Karachi is five hours ahead.
    assert allowance(datetime(2026, 11, 30, 20, 0, tzinfo=UTC)).used == 1


def test_a_month_is_not_the_utc_month(db):
    start = datetime(2026, 10, 31, 19, 0, tzinfo=UTC)  # 2026-11-01 00:00 Karachi, still October UTC
    event(start)
    event(start - SECOND)
    assert allowance(NOV_NOW).used == 1


def test_december_rolls_into_january_of_the_next_year(db):
    got = allowance(datetime(2026, 12, 15, tzinfo=UTC))
    assert got.resets_at == datetime(2026, 12, 31, 19, 0, tzinfo=UTC)
    assert got.resets_at.astimezone(quota.KARACHI).isoformat() == "2027-01-01T00:00:00+05:00"
    assert got.period_start.astimezone(quota.KARACHI).isoformat() == "2026-12-01T00:00:00+05:00"


def test_remaining_never_goes_below_zero_when_the_limit_drops(db):
    for _ in range(3):
        event(NOV_NOW)
    got = allowance(NOV_NOW)
    assert (got.limit, got.used, got.remaining) == (2, 3, 0)


def test_a_naive_clock_is_refused_everywhere(db):
    naive = datetime(2026, 11, 1)
    for call in (
        lambda: quota.allowance_for(ACCOUNT, "individual", naive),
        lambda: quota.quota_limit_at("individual", naive),
        lambda: quota.month_bounds(naive),
    ):
        with pytest.raises(ValueError, match="timezone-aware"):
            call()


def test_reads_never_write(db):
    before = (PublicationEvent.objects.count(), ListingQuotaPolicy.objects.count())
    allowance(NOV_NOW)
    quota.quota_limit_at("agency", NOV_NOW)
    assert (PublicationEvent.objects.count(), ListingQuotaPolicy.objects.count()) == before


WRITES = {"create", "bulk_create", "get_or_create", "update_or_create", "save", "update", "delete"}


def _writes_to_publication_events(tree) -> bool:
    """Any `PublicationEvent...<write>(...)` call or `PublicationEvent(...)` construction."""
    import ast

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "PublicationEvent":
            return True
        if isinstance(func, ast.Attribute) and func.attr in WRITES:
            names = {n.id for n in ast.walk(func.value) if isinstance(n, ast.Name)}
            attrs = {n.attr for n in ast.walk(func.value) if isinstance(n, ast.Attribute)}
            if "PublicationEvent" in names | attrs:
                return True
    return False


def test_the_write_detector_sees_every_write_form():
    import ast

    for code in (
        "PublicationEvent.objects.create(a=1)",
        "PublicationEvent.objects.filter(a=1).update(a=2)",
        "PublicationEvent.objects.filter(a=1).delete()",
        "PublicationEvent(a=1).save()",
        "models.PublicationEvent.objects.bulk_create([])",
    ):
        assert _writes_to_publication_events(ast.parse(code)), code
    assert not _writes_to_publication_events(ast.parse("PublicationEvent.objects.filter(a=1)"))


def test_only_the_publish_service_writes_publication_events():
    import ast
    import pathlib

    root = pathlib.Path(quota.__file__).resolve().parents[1]  # apps/
    offenders = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "migrations" not in path.parts
        and _writes_to_publication_events(ast.parse(path.read_text()))
    ]
    assert offenders == ["listings/publication.py"]  # Story 2.15's publish service, exactly


# owner check


@pytest.mark.parametrize(
    ("state", "account_type", "limit"),
    [
        ("active", "individual", 2),
        ("active", "agency", 10),
        ("unverified", "individual", 2),
        ("suspended", "agency", 10),
    ],
)
def test_owners_read_their_own_allowance(db, state, account_type, limit):
    view = make_account(account_type=account_type, state=state)
    event(NOV_NOW, account=view.id)
    event(NOV_NOW, account=OTHER)
    got = quota.owner_allowance(user(view), NOV_NOW)
    assert (got.limit, got.used, got.remaining) == (limit, 1, limit - 1)


def test_visitors_deactivated_and_system_actors_are_refused(db):
    with pytest.raises(DomainError) as visitor:
        quota.owner_allowance(Actor.visitor(), NOV_NOW)
    assert visitor.value.code == "unauthenticated"
    gone = make_account(state="deactivated")
    for actor in (user(gone), Actor.system()):
        with pytest.raises(DomainError) as refused:
            quota.owner_allowance(actor, NOV_NOW)
        assert refused.value.code == "forbidden"


def test_an_account_that_disappears_between_the_checks_is_forbidden(db, monkeypatch):
    view = make_account()
    monkeypatch.setattr(quota.identity, "get_signed_in_account", lambda account_id: None)
    with pytest.raises(DomainError) as refused:
        quota.owner_allowance(user(view), NOV_NOW)
    assert refused.value.code == "forbidden"
