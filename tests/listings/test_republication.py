"""Story 2.18: `publication.republish` (an Archived Listing back to Published), the counted or
free decision, the Karachi month boundary, the baseline selection, idempotency, all-or-nothing,
concurrency, and the fingerprint-migration pattern. Values are low-entropy fake words."""

import importlib
import itertools
import threading
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.db import connection, transaction

from apps.catalog import registry
from apps.identity.selectors import lock_account
from apps.kernel import audit, events, idempotency
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord, IdempotencyRecord
from apps.listings import policy, publication, quota, selectors, services, status
from apps.listings import retention as draft_retention
from apps.listings.events import ListingPublished
from apps.listings.models import (
    ListingImage,
    ListingLocation,
    ListingRevision,
    PropertyListing,
    PublicationEvent,
)
from tests.listings.support import (
    HOUSE,
    RENT_HOUSE,
    make_account,
    places,
    random_email,
    ready_draft,
    seed_cap,
    seed_media_policies,
    set_account_state,
    set_listing_visibility,
    user,
)
from tests.listings.test_lifecycle import agency, edit, identity_changes
from tests.listings.test_publication import run_threads

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=1)  # the same Karachi month
NEXT_MONTH = datetime(2026, 12, 3, 12, 0, tzinfo=UTC)
_keys = itertools.count(1)
IDENTITY = (
    "purpose",
    "category",
    "subtype",
    "city",
    "locality",
    "area_value",
    "area_unit",
    "marla_basis",
)  # written out from the spec, not read from the registry


def key():
    return f"rk-{next(_keys):04d}"


@pytest.fixture(autouse=True)
def setup(db):
    seed_cap()
    seed_media_policies()


@pytest.fixture
def bus(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    seen = []
    events.subscribe(ListingPublished, seen.append)
    return seen


def get(account, view):
    return selectors.get_owned_listing(user(account), view.id)


def archived(account=None, values=HOUSE, at=NOW, **kw):
    """(account, view) of an Archived Listing that was published (counted) at `at`."""
    account = account or agency()
    view = ready_draft(account, values, **kw)
    publication.publish(user(account), view.id, view.revision, key(), at)
    done = get(account, view)
    status.change_status(user(account), view.id, done.revision, "archived")
    return account, get(account, view)


def rearchive(account, view):
    current = get(account, view)
    status.change_status(user(account), view.id, current.revision, "archived")
    return get(account, view)


def republish(account, view, now=LATER, k=None, base=None):
    return publication.republish(
        user(account), view.id, view.revision if base is None else base, k or key(), now
    )


def refused(account, view, exc=DomainError, **kw):
    with pytest.raises(exc) as caught:
        republish(account, view, **kw)
    return caught.value


def events_of(view):
    return list(PublicationEvent.objects.filter(listing_id=view.id).order_by("created_at", "id"))


def kinds_of(view):
    return [(e.kind, e.counted) for e in events_of(view)]


def used(account, now=LATER):
    return quota.allowance_for(account.id, account.type, now).used


def ledger():
    return (
        PublicationEvent.objects.count(),
        AuditRecord.objects.filter(action="listing.republished").count(),
        IdempotencyRecord.objects.filter(scope=publication.REPUBLISH_SCOPE).count(),
        PropertyListing.objects.filter(status="published").count(),
        ListingRevision.objects.count(),
    )


def make_baseline(account, view, kind="initial", at=NOW, fingerprint=None, counted=None):
    return PublicationEvent.objects.create(
        account_id=account.id,
        listing_id=view.id,
        kind=kind,
        counted=kind in ("initial", "republish_counted") if counted is None else counted,
        identity_fingerprint=fingerprint or publication.identity_fingerprint(view),
        created_at=at,
    )


# --- the decision: free ---------------------------------------------------------------------


def test_the_same_month_with_the_identity_unchanged_is_free_and_writes_everything(
    bus, django_capture_on_commit_callbacks
):
    account, view = archived()
    images = list(ListingImage.objects.filter(listing_id=view.id).values_list("id", flat=True))
    before = ListingRevision.objects.filter(listing_id=view.id).count()
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        done = republish(account, view, k="rk-free")
        assert bus == []  # nothing before the commit
    assert len(callbacks) == 1
    assert bus == [ListingPublished(listing_id=view.id, account_id=account.id, republished=True)]
    assert done.kind == "republish_free"
    assert (done.listing.status, done.listing.visibility, done.listing.revision) == (
        "published",
        "public",
        view.revision + 1,
    )
    assert done.listing.fields == get(account, view).fields
    row = PropertyListing.objects.get(id=view.id)
    assert (row.status, row.revision, row.updated_at) == ("published", view.revision + 1, LATER)
    event = events_of(view)[-1]
    assert (event.kind, event.counted, event.idempotency_key, event.created_at) == (
        "republish_free",
        False,
        "rk-free",
        LATER,
    )
    assert event.identity_fingerprint == publication.identity_fingerprint(view)
    assert event.account_id == account.id and event.correction_used is False
    revision = ListingRevision.objects.get(listing_id=view.id, revision=row.revision)
    assert (revision.changes, revision.origin) == ({"status": "published"}, "manual")
    assert ListingRevision.objects.filter(listing_id=view.id).count() == before + 1
    record = AuditRecord.objects.get(action="listing.republished")
    assert (record.target_type, record.target_id, record.actor_id) == (
        "listing",
        view.id,
        account.id,
    )
    assert record.metadata == {"kind": "republish_free"} and record.reason == ""
    assert (
        not AuditRecord.objects.filter(action="listing.published")
        .exclude(target_id=view.id)
        .exists()
    )
    claim = IdempotencyRecord.objects.get(scope=publication.REPUBLISH_SCOPE)
    assert claim.result_ref == f"listing:{view.id}"
    assert (
        list(ListingImage.objects.filter(listing_id=view.id).values_list("id", flat=True)) == images
    )  # the images came back with it
    assert used(account) == 1  # the original publication only


def test_a_non_identity_edit_while_archived_is_still_free():
    account, view = archived()
    done = edit(account, view, {"title": "A different family house", "asking_price": "1700000"})
    assert republish(account, done).kind == "republish_free"
    assert used(account) == 1


def test_several_free_republications_in_one_month_use_nothing_and_keep_the_baseline():
    account, view = archived()
    for day in range(1, 5):
        done = republish(account, view, now=NOW + timedelta(days=day))
        assert done.kind == "republish_free"
        view = rearchive(account, view)
    assert kinds_of(view) == [("initial", True)] + [("republish_free", False)] * 4
    assert used(account) == 1


# --- the decision: counted ------------------------------------------------------------------


def test_a_different_month_is_counted_and_uses_one_publication():
    account, view = archived()
    assert used(account, NEXT_MONTH) == 0
    done = republish(account, view, now=NEXT_MONTH)
    assert done.kind == "republish_counted"
    event = events_of(view)[-1]
    assert (event.kind, event.counted, event.created_at) == ("republish_counted", True, NEXT_MONTH)
    assert used(account, NEXT_MONTH) == 1
    assert AuditRecord.objects.get(action="listing.republished").metadata == {
        "kind": "republish_counted"
    }


@pytest.mark.parametrize("name", IDENTITY)
def test_a_baseline_whose_fingerprint_differs_in_any_one_identity_field_is_counted(name):
    account, view = archived()
    other = {
        "purpose": "rent",
        "category": "plot",
        "subtype": "plot_file",
        "city": "33333333-3333-4333-8333-333333333333",
        "locality": "44444444-4444-4444-8444-444444444444",
        "area_value": "11",
        "area_unit": "kanal",
        "marla_basis": "250",
    }[name]
    changed = SimpleNamespace(fields={**view.fields, name: other})
    PublicationEvent.objects.filter(listing_id=view.id).update(
        identity_fingerprint=publication.identity_fingerprint(changed)
    )
    assert republish(account, view).kind == "republish_counted"
    assert used(account) == 2


@pytest.mark.parametrize("name", ["city", "area_value", "area_unit", "marla_basis"])
def test_really_editing_an_identity_field_while_archived_is_counted(name):
    account, view = archived()
    done = edit(account, view, identity_changes()[name])
    assert republish(account, done).kind == "republish_counted"
    assert used(account) == 2


def test_the_full_chain_counted_free_counted_free():
    account, view = archived()  # published (counted) in November
    place = places()
    view = edit(
        account, view, {"city": str(place["lahore"].id), "locality": str(place["other"].id)}
    )
    assert republish(account, view).kind == "republish_counted"  # city changed: new baseline
    view = rearchive(account, view)
    view = edit(account, view, {"description": "Quiet street, close to the market"})
    assert republish(account, view, now=LATER + timedelta(days=1)).kind == "republish_free"
    view = rearchive(account, view)
    view = edit(
        account, view, {"city": str(place["karachi"].id), "locality": str(place["area"].id)}
    )
    assert republish(account, view, now=LATER + timedelta(days=2)).kind == "republish_counted"
    assert kinds_of(view) == [
        ("initial", True),
        ("republish_counted", True),
        ("republish_free", False),
        ("republish_counted", True),
    ]
    assert used(account) == 3


def test_a_seeded_listing_with_no_counted_baseline_is_counted():
    account, view = archived()
    PublicationEvent.objects.filter(listing_id=view.id).update(kind="seed", counted=False)
    assert kinds_of(view) == [("seed", False)]
    assert republish(account, view).kind == "republish_counted"


def test_seed_and_free_events_are_never_the_baseline():
    account, view = archived()
    initial = events_of(view)[0]
    # A later seed event and a later free event, both in the month and both with a fingerprint
    # that differs from the Listing's: only the counted `initial` event decides.
    make_baseline(account, view, "seed", NOW + timedelta(hours=1), "0" * 64, counted=False)
    make_baseline(account, view, "republish_free", NOW + timedelta(hours=2), "1" * 64)
    assert initial.identity_fingerprint == publication.identity_fingerprint(view)
    assert republish(account, view).kind == "republish_free"
    # And with equal fingerprints but no counted event at all, it is counted.
    PublicationEvent.objects.filter(id=initial.id).update(kind="seed", counted=False)
    view = rearchive(account, view)
    assert republish(account, view, now=LATER + timedelta(days=1)).kind == "republish_counted"


def test_free_history_without_any_counted_event_is_counted():
    account, view = archived()
    PublicationEvent.objects.filter(listing_id=view.id).update(kind="republish_free", counted=False)
    assert republish(account, view).kind == "republish_counted"


def test_the_latest_counted_event_is_the_baseline_not_an_older_one():
    account, view = archived()
    stale = SimpleNamespace(fields={**view.fields, "area_value": "99"})
    older = events_of(view)[0]
    PublicationEvent.objects.filter(id=older.id).update(
        identity_fingerprint=publication.identity_fingerprint(stale)
    )
    make_baseline(account, view, "republish_counted", NOW + timedelta(hours=3))
    assert republish(account, view).kind == "republish_free"  # the newer equal one decides


def test_a_null_baseline_fingerprint_is_never_equal():
    account, view = archived()
    PublicationEvent.objects.filter(listing_id=view.id).update(identity_fingerprint=None)
    assert republish(account, view).kind == "republish_counted"


def test_the_baseline_is_this_listings_own_history():
    account, view = archived()
    PublicationEvent.objects.filter(listing_id=view.id).delete()
    other = ready_draft(account)
    make_baseline(account, other, "initial", NOW)  # another Listing's counted event
    assert republish(account, view).kind == "republish_counted"


# --- the Karachi month boundary -------------------------------------------------------------


def utc(*parts):
    return datetime(*parts, tzinfo=UTC)


FREE, COUNTED = "republish_free", "republish_counted"
# 2026-11-30 18:59:59 UTC is 23:59:59 on 30 November in Karachi; 19:00:00 UTC is 1 December.
BOUNDARY = [
    (utc(2026, 11, 15, 12), utc(2026, 11, 30, 18, 59, 59), FREE),
    (utc(2026, 11, 15, 12), utc(2026, 11, 30, 19, 0, 0), COUNTED),
    (utc(2026, 11, 15, 12), utc(2026, 11, 30, 19, 0, 1), COUNTED),
    # a baseline in the last second of November in Karachi, then the first second of December
    (utc(2026, 11, 30, 18, 59, 59), utc(2026, 11, 30, 19, 0, 0), COUNTED),
    (utc(2026, 11, 30, 18, 59, 59), utc(2026, 11, 1, 0, 0), COUNTED),  # later than now
    # UTC says November, Karachi says December: both are December in Karachi
    (utc(2026, 11, 30, 19, 30), utc(2026, 12, 31, 18, 59, 59), FREE),
    (utc(2026, 11, 30, 19, 30), utc(2026, 12, 31, 19, 0, 0), COUNTED),
    # the start of the month in Karachi is the previous day in UTC
    (utc(2026, 11, 30, 19, 0, 0), utc(2026, 12, 15, 12), FREE),
    (utc(2026, 11, 30, 18, 59, 59), utc(2026, 12, 15, 12), COUNTED),
    # the year boundary, and the same month a year later
    (utc(2026, 12, 31, 18, 59, 59), utc(2026, 12, 31, 19, 0), COUNTED),
    (utc(2025, 11, 15, 12), utc(2026, 11, 15, 12), COUNTED),
    # the first instant of the month in Karachi (31 Oct 19:00:00 UTC), one second either side
    (utc(2026, 10, 31, 19, 0, 0), utc(2026, 11, 15, 12), FREE),
    (utc(2026, 10, 31, 18, 59, 59), utc(2026, 11, 15, 12), COUNTED),
    (utc(2026, 10, 31, 19, 0, 1), utc(2026, 11, 15, 12), FREE),
    # exactly the first instant of the NEXT Karachi month, with now still in November
    (utc(2026, 11, 30, 19, 0, 0), utc(2026, 11, 15, 12), COUNTED),
    # a baseline equal to now is not in the future; one later than now, in the month, is
    (utc(2026, 11, 15, 12), utc(2026, 11, 15, 12), FREE),
    (utc(2026, 11, 20, 12), utc(2026, 11, 15, 12), COUNTED),
    (utc(2026, 11, 30, 18, 59, 59), utc(2026, 11, 30, 18, 59, 59), FREE),
]


@pytest.mark.parametrize(("baseline", "now", "expected"), BOUNDARY)
def test_the_karachi_calendar_month_decides_not_the_utc_one(baseline, now, expected):
    account, view = archived(at=NOW)
    PublicationEvent.objects.filter(listing_id=view.id).update(created_at=baseline)
    assert republish(account, view, now=now).kind == expected


def test_a_baseline_later_than_now_is_never_a_free_republication():  # the rule: not in the past
    account, view = archived(at=NOW)
    PublicationEvent.objects.filter(listing_id=view.id).update(created_at=NOW + timedelta(days=60))
    assert republish(account, view, now=LATER).kind == "republish_counted"


# --- quota ----------------------------------------------------------------------------------


def exhaust(account, at=LATER, count=None):
    limit = quota.quota_limit_at(account.type, at)
    for _ in range(limit if count is None else count):
        PublicationEvent.objects.create(
            account_id=account.id, listing_id=uuid.uuid4(), kind="initial", counted=True,
            created_at=at,
        )  # fmt: skip


def test_a_counted_republication_needs_a_remaining_publication_and_consumes_nothing_without():
    account = make_account()  # an individual: two a month
    view = ready_draft(account)
    publication.publish(user(account), view.id, view.revision, key(), NEXT_MONTH)
    view = rearchive(account, view)
    exhaust(account, NEXT_MONTH, 1)  # the publication above is the first, this the second
    view = edit(account, view, identity_changes()["area_value"])
    before = ledger()
    error = refused(account, view, publication.QuotaExhausted, now=NEXT_MONTH)
    assert error.code == "quota_exhausted"
    assert (error.limit, error.used) == (2, 2)
    assert error.resets_at == datetime(2027, 1, 1, tzinfo=quota.KARACHI)
    assert ledger() == before
    assert PropertyListing.objects.get(id=view.id).status == "archived"
    assert used(account, NEXT_MONTH) == 2


def test_an_exhausted_account_can_still_republish_free_but_not_counted():
    account, view = archived(account=make_account())
    exhaust(account, LATER, count=1)  # the publication in `archived` plus this one: the limit
    assert (
        used(account) == 2 and quota.allowance_for(account.id, account.type, LATER).remaining == 0
    )
    done = republish(account, view)  # same month, unchanged: free
    assert done.kind == "republish_free" and used(account) == 2
    view = rearchive(account, view)
    view = edit(account, view, identity_changes()["area_unit"])
    assert refused(account, view, publication.QuotaExhausted).code == "quota_exhausted"
    assert used(account) == 2


def test_a_limit_of_zero_still_allows_a_free_republication():
    from apps.listings.models import ListingQuotaPolicy

    account, view = archived(account=make_account())
    ListingQuotaPolicy.objects.filter(account_type=account.type).update(monthly_limit=0)
    assert republish(account, view).kind == "republish_free"
    view = rearchive(account, view)
    view = edit(account, view, identity_changes()["marla_basis"])
    assert refused(account, view, publication.QuotaExhausted).code == "quota_exhausted"


def test_the_last_slot_is_used_by_a_counted_republication_and_the_next_is_refused():
    account = make_account()
    view = ready_draft(account)
    publication.publish(user(account), view.id, view.revision, key(), NEXT_MONTH)
    view = rearchive(account, view)
    view = edit(account, view, identity_changes()["area_value"])
    assert republish(account, view, now=NEXT_MONTH).kind == "republish_counted"
    assert used(account, NEXT_MONTH) == 2
    view = rearchive(account, view)
    view = edit(account, view, identity_changes()["area_unit"])
    assert refused(account, view, publication.QuotaExhausted, now=NEXT_MONTH).code == (
        "quota_exhausted"
    )


def test_the_allowance_counts_counted_republications_and_ignores_free_ones():
    account, view = archived()
    republish(account, view)  # free
    assert quota.owner_allowance(user(account), LATER).used == 1
    view = rearchive(account, view)
    republish(account, view, now=NEXT_MONTH)  # counted, in December
    assert quota.owner_allowance(user(account), NEXT_MONTH).used == 1
    assert quota.owner_allowance(user(account), LATER).used == 1  # November: the original only


# --- refusals -------------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["sold", "rented"])
def test_a_sold_or_rented_listing_must_be_archived_first(target):
    account, view = archived(values=RENT_HOUSE if target == "rented" else HOUSE)
    set_listing_visibility(view.id, target)
    before = ledger()
    assert refused(account, get(account, view)).code == "conflict"
    assert ledger() == before
    set_listing_visibility(view.id, "archived")  # archived again: it republishes
    assert republish(account, get(account, view)).kind == "republish_free"


@pytest.mark.parametrize("current", ["draft", "published", "removed", "sold", "rented"])
def test_only_an_archived_listing_republishes(current):
    account, view = archived()
    set_listing_visibility(view.id, current)
    before = ledger()
    assert refused(account, get(account, view)).code == "conflict"
    assert ledger() == before
    assert PropertyListing.objects.get(id=view.id).status == current


def test_a_restricted_archived_listing_is_forbidden_and_nothing_is_written(bus):
    account, view = archived()
    set_listing_visibility(view.id, "archived", restricted=True)
    before = ledger()
    assert refused(account, get(account, view), k="rk-restricted").code == "forbidden"
    assert ledger() == before and bus == []
    assert PropertyListing.objects.get(id=view.id).status == "archived"
    assert not IdempotencyRecord.objects.filter(key="rk-restricted").exists()


def test_a_restricted_listing_that_is_not_archived_is_a_conflict_first():
    account, view = archived()
    set_listing_visibility(view.id, "removed", restricted=True)
    assert refused(account, get(account, view)).code == "conflict"


def test_an_incomplete_archived_listing_reports_every_problem_at_once():
    account, view = archived()
    view = edit(account, view, {"title": None, "asking_price": None})
    for image in view.media:
        services.remove_image(user(account), view.id, image.id)
    view = get(account, view)
    before = ledger()
    error = refused(account, view, publication.FieldsInvalid)
    assert error.fields == {
        "title": "required",
        "asking_price": "required",
        "images": "images_required",
    }
    assert ledger() == before and used(account) == 1


def test_stored_values_are_judged_again_in_full_not_only_the_touched_ones(monkeypatch):
    from apps.listings import completeness
    from apps.listings import validation as listing_validation

    account, view = archived()
    real = listing_validation.evaluate

    def tightened(*args, **kwargs):
        values, invalid = real(*args, **kwargs)
        return values, {**invalid, "bathrooms": "invalid_range"}

    monkeypatch.setattr(
        completeness,
        "validation",
        SimpleNamespace(evaluate=tightened, NON_FIELD=listing_validation.NON_FIELD),
    )
    assert refused(account, view, publication.FieldsInvalid).fields == {
        "bathrooms": "invalid_range"
    }  # an untouched stored value blocks (a Published edit would not be answerable for it)
    assert used(account) == 1


def test_an_inactive_city_and_an_unconfirmed_assistant_value_block_together():
    from apps.catalog.models import City

    account, view = archived()
    row = PropertyListing.objects.get(id=view.id)
    row.provenance = {"area_value": {"origin": "ai", "confirmed": False}}
    row.save(update_fields=["provenance"])
    City.objects.filter(id=places()["karachi"].id).update(is_active=False)
    fields = refused(account, get(account, view), publication.FieldsInvalid).fields
    assert fields["area_value"] == "confirm_required"
    assert "city" in fields or "locality" in fields


@pytest.mark.parametrize(
    ("state", "code"),
    [
        ("unverified", "email_not_verified"),
        ("suspended", "publishing_unavailable"),
        ("deactivated", "forbidden"),
    ],
)
def test_only_an_active_account_republishes(state, code):
    account, view = archived()
    set_account_state(account.id, state)
    before = ledger()
    assert refused(account, view).code == code
    assert ledger() == before


def test_a_stale_revision_carries_the_current_listing_and_writes_nothing():
    account, view = archived()
    before = ledger()
    error = refused(account, view, publication.StaleRevision, base=view.revision - 1)
    assert error.current.revision == view.revision and error.current.status == "archived"
    assert ledger() == before


def test_the_stale_revision_wins_over_the_status():
    account, view = archived()
    set_listing_visibility(view.id, "published")
    assert refused(account, view, publication.StaleRevision, base=99).code == "stale_revision"


def test_another_account_an_admin_a_visitor_and_bad_ids_are_refused_alike():
    from apps.kernel.actor import Actor

    account, view = archived()
    other = make_account(random_email(), "agency")
    call = publication.republish
    for actor, listing_id in [
        (user(other), view.id),
        (user(account), uuid.uuid4()),
        (user(account), "not-a-uuid"),
        (user(account), str(view.id).upper()),
        (Actor.system("r"), view.id),
    ]:
        with pytest.raises(DomainError) as caught:
            call(actor, listing_id, view.revision, key(), LATER)
        assert caught.value.code == "not_found", (actor, listing_id)
    with pytest.raises(DomainError) as caught:
        call(Actor.visitor("r"), view.id, view.revision, key(), LATER)
    assert caught.value.code == "unauthenticated"
    assert PropertyListing.objects.get(id=view.id).status == "archived"
    assert ledger()[0] == 1


@pytest.mark.parametrize(
    ("base", "k", "expected"),
    [
        (None, "rk-1", {"base_revision": "required"}),
        ("3", "rk-1", {"base_revision": "invalid"}),
        (True, "rk-1", {"base_revision": "invalid"}),
        (0, "rk-1", {"base_revision": "invalid"}),
        (3, None, {"idempotency_key": "required"}),
        (3, "has space", {"idempotency_key": "invalid"}),
        (3, "x" * 129, {"idempotency_key": "invalid"}),
        (None, None, {"base_revision": "required", "idempotency_key": "required"}),
    ],
)
def test_the_request_shape_is_judged_before_anything_is_written(base, k, expected):
    account, view = archived()
    with pytest.raises(publication.FieldsInvalid) as caught:
        publication.republish(user(account), view.id, base, k, LATER)
    assert caught.value.fields == expected
    assert ledger()[:3] == (1, 0, 0)


# --- idempotency ----------------------------------------------------------------------------


def test_a_replay_returns_the_original_outcome_and_charges_once(bus):
    account, view = archived()
    first = republish(account, view, now=NEXT_MONTH, k="rk-same")
    again = republish(account, view, now=NEXT_MONTH, k="rk-same")
    assert first.kind == again.kind == "republish_counted"
    assert again.listing == first.listing
    assert ledger()[:3] == (2, 1, 1)
    assert used(account, NEXT_MONTH) == 1


def test_a_free_replay_stays_free_even_when_the_listing_has_changed_since():
    account, view = archived()
    first = republish(account, view, k="rk-same")
    assert first.kind == "republish_free"
    done = status.change_status(user(account), view.id, first.listing.revision, "sold")
    again = republish(account, view, k="rk-same")  # the original base_revision and key
    assert again.kind == "republish_free" and again.listing.status == "sold"
    assert done.status == "sold" and ledger()[:3] == (2, 1, 1)


def test_a_replay_works_when_the_allowance_is_used_up_since():
    account, view = archived()
    republish(account, view, now=NEXT_MONTH, k="rk-same")
    exhaust(account, NEXT_MONTH)
    assert republish(account, view, now=NEXT_MONTH, k="rk-same").kind == "republish_counted"


def test_the_same_key_for_other_content_is_refused():
    account = agency()
    _, one = archived(account)
    _, two = archived(account)
    republish(account, one, k="rk-same")
    assert refused(account, two, k="rk-same").code == "idempotency_key_reused"
    assert refused(account, one, k="rk-same", base=one.revision + 5).code == (
        "idempotency_key_reused"
    )
    assert get(account, two).status == "archived"


def test_a_key_older_than_a_day_replays_from_its_event_with_its_kind():
    account, view = archived()
    first = republish(account, view, now=NEXT_MONTH, k="rk-same")
    later = NEXT_MONTH + timedelta(hours=25)
    again = republish(account, view, now=later, k="rk-same")  # the claim expired
    assert (again.kind, again.listing.status) == (first.kind, "published")
    assert ledger()[:3] == (2, 1, 1)
    assert IdempotencyRecord.objects.get(scope=publication.REPUBLISH_SCOPE).result_ref == (
        f"listing:{view.id}"
    )


def test_a_purged_claim_replays_from_the_event_and_a_free_one_keeps_its_kind():
    account, view = archived()
    republish(account, view, k="rk-same")
    IdempotencyRecord.objects.filter(scope=publication.REPUBLISH_SCOPE).delete()
    again = republish(account, view, k="rk-same")
    assert again.kind == "republish_free" and ledger()[:3] == (2, 1, 1)


def test_a_key_of_a_publication_cannot_republish_and_the_other_way_round():
    account, view = archived()
    first = events_of(view)[0]
    assert refused(account, view, k=first.idempotency_key).code == "idempotency_key_reused"
    assert get(account, view).status == "archived"
    republish(account, view, k="rk-rep")
    view = rearchive(account, view)
    other = ready_draft(account)
    with pytest.raises(DomainError) as caught:
        publication.publish(user(account), other.id, other.revision, "rk-rep", LATER)
    assert caught.value.code == "idempotency_key_reused"
    with pytest.raises(DomainError) as caught:  # the same listing, an expired publish claim
        publication.publish(user(account), view.id, view.revision, "rk-rep", LATER)
    assert caught.value.code == "idempotency_key_reused"
    assert get(account, other).status == "draft"


def test_a_key_is_scoped_to_the_account():
    one, a = archived()
    two, b = archived()
    republish(one, a, k="rk-same")
    assert republish(two, b, k="rk-same").kind == "republish_free"


def test_a_refused_attempt_keeps_no_claim_and_the_key_can_be_retried():
    account, view = archived()
    set_listing_visibility(view.id, "archived", restricted=True)
    assert refused(account, get(account, view), k="rk-retry").code == "forbidden"
    set_listing_visibility(view.id, "archived", restricted=False)
    assert republish(account, get(account, view), k="rk-retry").kind == "republish_free"


def test_a_claim_without_a_result_is_a_conflict_and_never_redoes_the_work():
    account, view = archived()
    IdempotencyRecord.objects.create(
        scope=publication.REPUBLISH_SCOPE,
        account_id=account.id,
        key="rk-empty",
        request_hash=idempotency.request_fingerprint(
            {"listing_id": view.id, "base_revision": view.revision}
        ),
        created_at=LATER,
    )
    assert refused(account, view, k="rk-empty").code == "conflict"
    assert ledger()[0] == 1


# --- all or nothing -------------------------------------------------------------------------


def fail_at(monkeypatch, target):
    def boom(*args, **kwargs):
        raise RuntimeError("injected")

    if target == "event":
        monkeypatch.setattr(PublicationEvent.objects, "create", boom)
    elif target == "status":
        monkeypatch.setattr(PropertyListing, "save", boom)
    elif target == "revision":
        monkeypatch.setattr(ListingRevision.objects, "create", boom)
    elif target == "audit":
        monkeypatch.setattr(audit, "record", boom)
    elif target == "claim":
        from apps.kernel.idempotency import Claim

        monkeypatch.setattr(Claim, "complete", boom)


@pytest.mark.parametrize("target", ["event", "status", "revision", "audit", "claim"])
@pytest.mark.parametrize("now", [LATER, NEXT_MONTH], ids=["free", "counted"])
def test_a_failure_in_any_step_leaves_nothing_half_applied(
    target, now, monkeypatch, bus, django_capture_on_commit_callbacks
):
    account, view = archived()
    before = (PropertyListing.objects.get(id=view.id).revision, ledger())
    with monkeypatch.context() as patched:
        fail_at(patched, target)
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            with pytest.raises(RuntimeError):
                republish(account, view, now=now, k="rk-fail")
    row = PropertyListing.objects.get(id=view.id)
    assert (row.status, row.revision) == ("archived", before[0])
    assert ledger() == before[1]
    assert callbacks == [] and bus == []
    assert republish(account, view, now=now, k="rk-fail").listing.status == "published"


def test_a_failing_subscriber_does_not_undo_the_republication(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})

    def failing(event):
        raise RuntimeError("subscriber")

    events.subscribe(ListingPublished, failing)
    account, view = archived()
    republish(account, view)
    assert PropertyListing.objects.get(id=view.id).status == "published"


@pytest.mark.parametrize("now", [LATER, NEXT_MONTH], ids=["free", "counted"])
def test_nothing_external_happens_inside_the_transaction(monkeypatch, now):
    from django.core import mail

    from apps.kernel import ai_log, notifications, runtime
    from apps.kernel import mail as kernel_mail
    from apps.kernel import media as kernel_media
    from apps.kernel import storage as kernel_storage

    account, view = archived()

    def boom(*args, **kwargs):
        raise AssertionError("an external call inside the republish transaction")

    for module, name in (
        (runtime, "submit_heavy"),
        (runtime, "submit_interactive"),
        (kernel_mail, "send"),
        (kernel_mail, "deliver"),
        (ai_log, "record_ai_call"),
        (kernel_media, "store_image"),
        (kernel_media, "process_upload"),
        (kernel_media, "store_processed"),
        (kernel_media, "delete_asset"),
        (kernel_media, "discard_file"),
        (kernel_storage, "discard"),
        (kernel_storage, "get_storage"),
        (notifications, "notify"),
    ):
        monkeypatch.setattr(module, name, boom)  # raises if the name does not exist
    assert republish(account, view, now=now).listing.status == "published"
    assert mail.outbox == []


# --- interactions ---------------------------------------------------------------------------


def test_a_republished_listing_is_public_again_under_the_visibility_policy():
    from apps.kernel.actor import Actor

    account, view = archived()
    visitor = Actor.visitor("r")
    with pytest.raises(DomainError):
        selectors.public_listing(visitor, view.id)
    done = republish(account, view)
    assert done.listing.visibility == "public"
    assert selectors.public_listing(visitor, view.id).id == view.id
    row = PropertyListing.objects.get(id=view.id)
    assert policy.is_public(row) and policy.can_view(visitor, row)


def test_archive_then_republish_keeps_the_revision_history_in_order():
    account, view = archived()
    republish(account, view)
    revisions = list(
        ListingRevision.objects.filter(listing_id=view.id)
        .order_by("revision")
        .values_list("revision", flat=True)
    )
    assert revisions == sorted(set(revisions)) and revisions[-1] == view.revision + 1


def test_drafts_and_retention_ignore_archived_and_republished_listings():
    account, view = archived()
    other = ready_draft(account)
    counts = selectors.owner_counts(user(account))
    assert (counts["archived"], counts["draft"]) == (1, 1)
    republish(account, view)
    counts = selectors.owner_counts(user(account))
    assert (counts["published"], counts["archived"], counts["draft"]) == (1, 0, 1)
    far = NOW + timedelta(days=900)
    PropertyListing.objects.filter(id=view.id).update(updated_at=NOW - timedelta(days=900))
    draft_retention.draft_notice(far, False)
    draft_retention.draft_expiry(far, False)
    assert PropertyListing.objects.get(id=view.id).status == "published"
    assert get(account, other).status == "draft"


def test_publish_still_returns_the_bare_view_and_its_event_is_not_flagged_republished(
    bus, django_capture_on_commit_callbacks
):
    account = agency()
    view = ready_draft(account)
    with django_capture_on_commit_callbacks(execute=True):
        done = publication.publish(user(account), view.id, view.revision, key(), NOW)
    assert not hasattr(done, "kind") and done.status == "published"  # publish returns the view
    assert bus == [ListingPublished(listing_id=view.id, account_id=account.id)]
    assert bus[0].republished is False


def test_the_republished_flag_is_true_only_on_a_republication(
    bus, django_capture_on_commit_callbacks
):
    account, view = archived()
    with django_capture_on_commit_callbacks(execute=True):
        republish(account, view)
    assert [e.republished for e in bus] == [True]


# --- concurrency ----------------------------------------------------------------------------


def archived_with_counted(account, at=NEXT_MONTH):
    """An Archived Listing whose baseline is last month, so that republishing it is counted."""
    view = ready_draft(account)
    publication.publish(user(account), view.id, view.revision, key(), NOW)
    done = get(account, view)
    status.change_status(user(account), view.id, done.revision, "archived")
    return get(account, view)


@pytest.mark.django_db(transaction=True)
def test_twenty_requests_for_one_slot_republish_exactly_once():
    account = make_account()
    one = archived_with_counted(account)
    two = archived_with_counted(account)
    # Two slots a month: the two publications were November's; December has both free.
    exhaust(account, NEXT_MONTH, 1)

    def work(index):
        view = (one, two)[index % 2]
        return publication.republish(
            user(account), view.id, view.revision, f"ck-{index:02d}", NEXT_MONTH
        )

    kinds, _ = run_threads(20, work)
    assert kinds.count("ok") == 1, kinds
    assert set(kinds) <= {"ok", "quota_exhausted", "conflict", "stale_revision"}
    assert PublicationEvent.objects.filter(kind="republish_counted").count() == 1
    assert PropertyListing.objects.filter(status="published").count() == 1
    assert AuditRecord.objects.filter(action="listing.republished").count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_listings_race_for_one_slot():
    account = make_account()
    one = archived_with_counted(account)
    two = archived_with_counted(account)
    exhaust(account, NEXT_MONTH, 1)

    def work(index):
        view = (one, two)[index]
        return publication.republish(
            user(account), view.id, view.revision, f"ck-{index}", NEXT_MONTH
        )

    kinds, _ = run_threads(2, work)
    assert kinds == ["ok", "quota_exhausted"]


@pytest.mark.django_db(transaction=True)
def test_the_same_key_replayed_concurrently_charges_once():
    account = agency()
    view = archived_with_counted(account)

    def work(_):
        return publication.republish(user(account), view.id, view.revision, "ck-same", NEXT_MONTH)

    kinds, outcomes = run_threads(20, work)
    assert kinds == ["ok"] * 20, kinds
    assert {out.kind for _, out in outcomes} == {"republish_counted"}
    assert PublicationEvent.objects.filter(kind="republish_counted").count() == 1
    assert AuditRecord.objects.filter(action="listing.republished").count() == 1
    assert IdempotencyRecord.objects.filter(scope=publication.REPUBLISH_SCOPE).count() == 1


@pytest.mark.django_db(transaction=True)
def test_free_republications_race_without_charging():
    account = agency()
    view = archived_with_counted(account)

    def work(index):
        return publication.republish(user(account), view.id, view.revision, f"ck-{index}", LATER)

    kinds, _ = run_threads(8, work)
    assert kinds.count("ok") == 1 and set(kinds) <= {"ok", "conflict", "stale_revision"}
    assert PublicationEvent.objects.filter(kind="republish_free").count() == 1
    assert quota.allowance_for(account.id, account.type, LATER).used == 1


@pytest.mark.django_db(transaction=True)
def test_a_publish_and_a_republish_never_both_succeed_for_one_listing():
    account = agency()
    view = archived_with_counted(account)

    def work(index):
        if index == 0:
            return publication.republish(user(account), view.id, view.revision, "ck-a", LATER)
        return publication.publish(user(account), view.id, view.revision, "ck-b", LATER)

    kinds, outcomes = run_threads(2, work)
    assert kinds.count("ok") == 1 and set(kinds) <= {"ok", "conflict", "stale_revision"}, kinds
    assert PublicationEvent.objects.filter(listing_id=view.id).count() == 2  # initial + one more


# --- review batch: what a stored value, the order of refusals and the clock decide ------------


def test_a_really_invalid_archived_value_blocks_because_everything_is_judged_again():
    account, view = archived()
    # Written behind the service's back: no field-level validation ever saw this value, and a
    # Published edit (judged on what it touches) would never be answerable for it.
    ListingLocation.objects.filter(listing_id=view.id).update(locality=places()["other"].id)
    view = get(account, view)
    assert view.fields["locality"] == str(places()["other"].id)  # a locality of another city
    before = ledger()
    assert refused(account, view, publication.FieldsInvalid).fields == {
        "locality": "locality_not_in_city"
    }
    assert ledger() == before


def test_a_lost_outcome_replays_as_not_found_and_charges_nothing():
    """The claim says it completed, but the event row it points at is gone: the kind of the
    outcome is unknown, so nothing is guessed and nothing is redone."""
    account, view = archived()
    republish(account, view, k="rk-lost")
    PublicationEvent.objects.filter(idempotency_key="rk-lost").delete()
    assert IdempotencyRecord.objects.get(key="rk-lost").result_ref == f"listing:{view.id}"
    before = ledger()
    assert refused(account, view, k="rk-lost").code == "not_found"
    assert ledger() == before


def test_publish_refuses_a_restricted_draft_and_writes_nothing():
    account = agency()
    view = ready_draft(account)
    set_listing_visibility(view.id, "draft", restricted=True)
    before = (PublicationEvent.objects.count(), IdempotencyRecord.objects.count())
    with pytest.raises(DomainError) as caught:
        publication.publish(user(account), view.id, view.revision, key(), NOW)
    assert caught.value.code == "forbidden"
    assert (PublicationEvent.objects.count(), IdempotencyRecord.objects.count()) == before
    assert PropertyListing.objects.get(id=view.id).status == "draft"
    set_account_state(account.id, "unverified")  # restricted is decided before the account state
    with pytest.raises(DomainError) as caught:
        publication.publish(user(account), view.id, view.revision, key(), NOW)
    assert caught.value.code == "forbidden"


# The order of refusals inside the transaction: stale revision, status, restricted, account
# state, validation, quota.


def test_stale_revision_wins_over_restricted_and_restricted_loses_to_status():
    account, view = archived()
    set_listing_visibility(view.id, "archived", restricted=True)
    assert refused(account, view, publication.StaleRevision, base=99).code == "stale_revision"
    set_listing_visibility(view.id, "published", restricted=True)
    assert refused(account, get(account, view)).code == "conflict"  # status before restricted


@pytest.mark.parametrize("state", ["unverified", "suspended"])
def test_restricted_is_refused_before_the_account_state(state):
    account, view = archived()
    set_listing_visibility(view.id, "archived", restricted=True)
    set_account_state(account.id, state)
    assert refused(account, get(account, view)).code == "forbidden"


@pytest.mark.parametrize(
    ("state", "code"),
    [("unverified", "email_not_verified"), ("suspended", "publishing_unavailable")],
)
def test_the_account_state_is_refused_before_validation(state, code):
    account, view = archived()
    view = edit(account, view, {"title": None})  # incomplete as well
    set_account_state(account.id, state)
    assert refused(account, view).code == code


def test_validation_is_refused_before_quota():
    account = make_account()
    view = ready_draft(account)
    publication.publish(user(account), view.id, view.revision, key(), NOW)
    view = rearchive(account, view)
    exhaust(account, NEXT_MONTH)  # the December allowance is gone
    view = edit(account, view, {"title": None, **identity_changes()["area_value"]})  # counted too
    assert refused(account, view, publication.FieldsInvalid, now=NEXT_MONTH).fields == {
        "title": "required"
    }  # a 400, not a 409


# The clock is read after the account lock: a request that waits across the Karachi month edge
# is judged (and counted) in the new month.


def wait_across_the_edge(monkeypatch, account, view, key_text):
    before, after = utc(2026, 11, 30, 18, 59, 59), utc(2026, 11, 30, 19, 0, 0)
    held, released, outcome = threading.Event(), threading.Event(), {}

    def holder():
        try:
            with transaction.atomic():
                lock_account(account.id)
                held.set()
                released.wait(30)
        finally:
            connection.close()

    def worker():
        try:
            outcome["result"] = publication.republish(
                user(account), view.id, view.revision, key_text
            )  # no `now`: the service reads the clock itself
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc
        finally:
            connection.close()

    monkeypatch.setattr("django.utils.timezone.now", lambda: after if released.is_set() else before)
    first = threading.Thread(target=holder)
    first.start()
    assert held.wait(30)
    second = threading.Thread(target=worker)
    second.start()
    second.join(timeout=1.5)
    assert second.is_alive()  # it is waiting for the account lock
    released.set()
    first.join(30)
    second.join(30)
    return outcome, before, after


@pytest.mark.django_db(transaction=True)
def test_the_decision_follows_the_clock_read_after_the_lock(monkeypatch):
    account, view = archived(agency())  # counted baseline on 15 November
    outcome, before, after = wait_across_the_edge(monkeypatch, account, view, "lock-1")
    assert outcome["result"].kind == "republish_counted"  # before the edge it would be free
    assert events_of(view)[-1].created_at == after
    assert quota.allowance_for(account.id, account.type, after).used == 1  # December
    assert quota.allowance_for(account.id, account.type, before).used == 1  # November: initial


@pytest.mark.django_db(transaction=True)
def test_the_allowance_month_follows_the_clock_read_after_the_lock(monkeypatch):
    account = make_account()
    view = ready_draft(account)
    publication.publish(user(account), view.id, view.revision, key(), NOW)
    view = rearchive(account, view)
    view = edit(account, view, identity_changes()["area_value"])  # counted in either month
    exhaust(account, NOW, count=1)  # November is full (2 of 2), December is empty
    outcome, _, after = wait_across_the_edge(monkeypatch, account, view, "lock-2")
    assert outcome.get("error") is None, outcome
    assert outcome["result"].kind == "republish_counted"
    assert quota.allowance_for(account.id, account.type, after).used == 1


# --- chains of identity edits ---------------------------------------------------------------


def test_changing_identity_and_back_is_counted_each_time_because_the_baseline_moves():
    account, view = archived()  # baseline A (area 10)
    view = edit(account, view, identity_changes()["area_value"])  # B (area 11)
    assert republish(account, view, now=LATER).kind == "republish_counted"  # baseline is now B
    view = rearchive(account, view)
    assert republish(account, view, now=LATER + timedelta(days=1)).kind == "republish_free"  # B
    view = rearchive(account, view)
    view = edit(account, view, {"area_value": "10"})  # back to A
    assert republish(account, view, now=LATER + timedelta(days=2)).kind == "republish_counted"
    assert kinds_of(view) == [
        ("initial", True),
        ("republish_counted", True),
        ("republish_free", False),
        ("republish_counted", True),
    ]
    assert used(account) == 3


@pytest.mark.django_db(transaction=True)
def test_twenty_requests_with_different_keys_for_one_listing_and_one_slot():
    account = make_account()
    view = archived_with_counted(account)
    view = edit(account, view, identity_changes()["area_value"])  # counted whenever it runs
    exhaust(account, NEXT_MONTH, 1)  # one December slot left

    def work(index):
        return publication.republish(
            user(account), view.id, view.revision, f"one-{index:02d}", NEXT_MONTH
        )

    kinds, _ = run_threads(20, work)
    assert kinds.count("ok") == 1, kinds
    assert set(kinds) <= {"ok", "quota_exhausted", "conflict", "stale_revision"}
    assert PublicationEvent.objects.filter(kind="republish_counted").count() == 1
    assert AuditRecord.objects.filter(action="listing.republished").count() == 1


# --- the fingerprint migration pattern ------------------------------------------------------

MIGRATION = "apps.listings.migrations.0015_identity_fingerprint_v9"


def ledger_snapshot(account):
    return (
        list(
            PublicationEvent.objects.order_by("id").values_list(
                "id", "kind", "counted", "created_at", "idempotency_key", "listing_id"
            )
        ),
        IdempotencyRecord.objects.count(),
        AuditRecord.objects.count(),
        quota.allowance_for(account.id, account.type, NOW),
    )


def stored(view):
    return list(
        PublicationEvent.objects.filter(listing_id=view.id)
        .order_by("created_at", "id")
        .values_list("identity_fingerprint", flat=True)
    )


def set_stored(view, value):
    PublicationEvent.objects.filter(listing_id=view.id).update(identity_fingerprint=value)


def old_set(module, fields):
    """The fingerprint of the registry-version-8 identity (purpose, category, subtype)."""
    from apps.listings.identity_migration import fingerprint

    return fingerprint(module.OLD_IDENTITY, fields, module._canonical)


@pytest.mark.django_db(transaction=True)
def test_the_migration_through_the_executor_recomputes_unchanged_nulls_drifted_and_keeps_the_rest():
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor

    seed_cap()
    seed_media_policies()
    module = importlib.import_module(MIGRATION)
    account = agency()
    _, unchanged = archived(account)
    _, drifted = archived(account)
    _, mixed = archived(account)
    _, already = archived(account)
    gone_account, gone = archived()
    first = events_of(mixed)[0]
    PublicationEvent.objects.create(  # a seed and a free event of the same Listing
        account_id=account.id, listing_id=mixed.id, kind="seed", counted=False,
        created_at=NOW + timedelta(hours=1),
    )  # fmt: skip
    PublicationEvent.objects.create(
        account_id=account.id, listing_id=mixed.id, kind="republish_free", counted=False,
        created_at=NOW + timedelta(hours=2),
    )  # fmt: skip
    conn = connections["default"]
    try:
        MigrationExecutor(conn).migrate([("listings", "0014_lifecycle_statuses")])
        for view in (unchanged, mixed):
            set_stored(view, old_set(module, view.fields))  # version 8 evidence, data unchanged
        set_stored(drifted, old_set(module, {**drifted.fields, "category": "plot"}))  # drifted
        set_stored(already, publication.identity_fingerprint(already))  # already the new value
        set_stored(gone, old_set(module, gone.fields))
        PropertyListing.objects.filter(id=gone.id).delete()  # no data left to compare
        before = ledger_snapshot(account)
        assert stored(unchanged) == [old_set(module, unchanged.fields)]
        MigrationExecutor(conn).migrate([("listings", "0015_identity_fingerprint_v9")])
        live = publication.identity_fingerprint
        assert stored(unchanged) == [live(unchanged)]
        assert stored(mixed) == [live(mixed)] * 3  # initial, seed and free alike
        assert stored(drifted) == [None]
        assert stored(already) == [live(already)]
        assert stored(gone) == [old_set(module, gone.fields)]  # a deleted Listing is left alone
        assert ledger_snapshot(account) == before  # only the fingerprint column changed
        module.recompute(django_apps, None)  # idempotent: a second run changes nothing
        assert stored(drifted) == [None] and stored(unchanged) == [live(unchanged)]
        assert ledger_snapshot(account) == before
        MigrationExecutor(conn).migrate([("listings", "0014_lifecycle_statuses")])  # reverse: no-op
        assert stored(unchanged) == [live(unchanged)] and stored(drifted) == [None]
    finally:
        MigrationExecutor(conn).migrate(MigrationExecutor(conn).loader.graph.leaf_nodes())
    assert first.id in {e.id for e in events_of(mixed)}
    assert republish(account, get(account, unchanged), now=NOW).kind == "republish_free"
    # a NULL baseline can never match: the drifted Listing's next republication is counted
    assert republish(account, get(account, drifted), now=NOW).kind == "republish_counted"
    assert gone_account is not None


def growing_registry(monkeypatch):
    grown = [
        replace(definition, quota_identity=True) if definition.id == "bedrooms" else definition
        for definition in registry.fields()
    ]
    monkeypatch.setattr(registry, "fields", lambda: grown)


def grown_recompute():
    """A migration to the pattern for a NEW identity field (`bedrooms`, an integer): the frozen
    canonicalisation of 0015 plus integers kept as numbers, over a loader that reads the column."""
    from apps.listings.identity_migration import load_listing_values, recompute_fingerprints

    module = importlib.import_module(MIGRATION)

    def canonical(name, value):
        if isinstance(value, bool) or isinstance(value, int):
            return value
        return module._canonical(name, value)

    def load(apps, ids):
        values = load_listing_values(apps, ids)
        for row in (
            apps.get_model("listings", "ListingResidential")
            .objects.filter(listing_id__in=values)
            .values("listing_id", "bedrooms")
        ):
            values[row["listing_id"]]["bedrooms"] = row["bedrooms"]
        return values

    return lambda: recompute_fingerprints(
        django_apps, module.IDENTITY, (*module.IDENTITY, "bedrooms"), canonical, load
    )


def test_a_new_identity_field_recomputes_unchanged_listings_and_nulls_drifted_ones(monkeypatch):
    """The AC of Story 2.18: a registry change that adds an identity field, handled by a data
    migration written to the pattern, consumes no quota and writes no event; an unchanged
    Listing then republishes free under the NEW set, a drifted one counted."""
    account = agency()
    _, unchanged = archived(account)
    _, drifted = archived(account)
    old = publication.identity_fingerprint(unchanged)
    assert stored(unchanged) == [old] and stored(drifted) == [
        publication.identity_fingerprint(drifted)
    ]
    edit(account, drifted, {"area_value": "11"})  # identity edited while Archived: drift
    growing_registry(monkeypatch)
    assert publication.identity_fingerprint(unchanged) != old  # the stored value is out of date
    before = ledger_snapshot(account)
    counts = grown_recompute()()
    assert counts == {"recomputed": 1, "nulled": 1, "kept": 0}
    assert stored(unchanged) == [publication.identity_fingerprint(unchanged)]  # frozen == live
    assert stored(drifted) == [None]
    assert ledger_snapshot(account) == before
    assert grown_recompute()() == {"recomputed": 0, "nulled": 0, "kept": 1}  # idempotent
    assert stored(drifted) == [None] and ledger_snapshot(account) == before
    assert republish(account, get(account, unchanged)).kind == "republish_free"
    assert republish(account, get(account, drifted)).kind == "republish_counted"
