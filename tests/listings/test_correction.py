"""Story 2.19: the one-time correction of locality and area (`publication.correct`), its 24-hour
window, the counted event it marks, the eligibility read, the identity set without the marla
basis and the version-10 fingerprint migration. Values are low-entropy fake words."""

import importlib
import itertools
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.db import connection, connections

from apps.catalog import registry
from apps.catalog.models import Locality
from apps.identity.selectors import lock_account
from apps.kernel import audit, events
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError, get_error
from apps.kernel.models import AuditRecord, IdempotencyRecord
from apps.listings import publication, quota, selectors, services, status
from apps.listings.events import ListingModelInputsChanged
from apps.listings.models import (
    ListingArea,
    ListingImage,
    ListingLocation,
    ListingRevision,
    PropertyListing,
    PublicationEvent,
)
from tests.listings.support import (
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
from tests.listings.test_lifecycle import agency, edit
from tests.listings.test_publication import run_threads

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
SOON = NOW + timedelta(hours=1)
DAY = timedelta(hours=24)
_keys = itertools.count(1)
# written out from the spec, not read from the registry
IDENTITY = ("purpose", "category", "subtype", "city", "locality", "area_value", "area_unit")


def key():
    return f"ck-{next(_keys):04d}"


@pytest.fixture(autouse=True)
def setup(db):
    seed_cap()
    seed_media_policies()


@pytest.fixture
def bus(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    seen = []
    events.subscribe(ListingModelInputsChanged, seen.append)
    return seen


def south():
    """A second locality of the Draft's city."""
    row, _ = Locality.objects.get_or_create(
        city=places()["karachi"], name="Testville South", kind="area"
    )
    return str(row.id)


def get(account, view):
    return selectors.get_owned_listing(user(account), view.id)


def published(account=None, at=NOW, **kw):
    account = account or agency()
    view = ready_draft(account, **kw)
    publication.publish(user(account), view.id, view.revision, key(), at)
    return account, get(account, view)


def correct(account, view, changes=None, now=SOON, base=None):
    changes = {"area_value": "11"} if changes is None else changes
    base = view.revision if base is None else base
    return publication.correct(user(account), view.id, base, changes, now)


def window(account, view, now=SOON):
    return publication.correction_window(user(account), view.id, now)


def archive(account, view):
    current = get(account, view)
    status.change_status(user(account), view.id, current.revision, "archived")
    return get(account, view)


def republish(account, view, now):
    current = get(account, view)
    result = publication.republish(user(account), view.id, current.revision, key(), now)
    return result.kind, result.listing


def events_of(view):
    return list(PublicationEvent.objects.filter(listing_id=view.id).order_by("created_at", "id"))


def event_state(view):
    return [(e.kind, e.counted, e.correction_used, e.identity_fingerprint) for e in events_of(view)]


def snapshot(view):
    """Everything a correction may write, for one Listing."""
    return (
        list(PropertyListing.objects.filter(id=view.id).values("revision", "provenance")),
        list(ListingArea.objects.filter(listing_id=view.id).values()),
        list(ListingLocation.objects.filter(listing_id=view.id).values()),
        ListingRevision.objects.filter(listing_id=view.id).count(),
        event_state(view),
        AuditRecord.objects.filter(action="listing.corrected").count(),
        IdempotencyRecord.objects.count(),
    )


def refused(account, view, changes=None, **kw):
    """The refusal, after asserting that nothing at all was written."""
    before = snapshot(view)
    with pytest.raises(DomainError) as caught:
        correct(account, view, changes, **kw)
    assert snapshot(view) == before
    return caught.value


# --- the identity set -------------------------------------------------------------------------


def test_the_identity_set_is_exactly_the_seven_fields():
    assert {f.id for f in registry.fields() if f.quota_identity} == set(IDENTITY)
    assert registry.registry_version() == 12
    assert publication.CORRECTABLE_FIELDS == ("locality", "area_value", "area_unit", "marla_basis")


def test_the_marla_basis_is_edited_on_a_published_listing_without_a_correction(bus):
    account, view = published()
    stored = events_of(view)[0].identity_fingerprint
    done = edit(account, view, {"marla_basis": "250"})
    assert done.fields["marla_basis"] == "250" and done.revision == view.revision + 1
    assert Decimal(done.fields["area_sqft"]) == Decimal("2500")
    assert event_state(view) == [("initial", True, False, stored)]  # no correction spent
    assert publication.identity_fingerprint(done) == stored
    archive(account, view)
    assert republish(account, view, SOON)[0] == "republish_free"


# --- the successful correction ----------------------------------------------------------------


def test_a_correction_saves_marks_the_counted_event_and_charges_nothing(bus):
    account, view = published()
    (before,) = events_of(view)
    allowance = quota.allowance_for(account.id, account.type, SOON)
    changes = {"locality": south(), "area_value": "11", "marla_basis": "250"}
    done = correct(account, view, changes)
    assert (done.status, done.revision) == ("published", view.revision + 1)
    assert str(done.fields["locality"]) == changes["locality"]
    assert Decimal(done.fields["area_value"]) == 11 and done.fields["marla_basis"] == "250"
    assert Decimal(done.fields["area_sqft"]) == Decimal("2750")
    for name in changes:
        assert done.provenance[name] == {"origin": "manual", "confirmed": True}
    assert done == get(account, view)
    revision = ListingRevision.objects.get(listing_id=view.id, revision=done.revision)
    assert (revision.origin, revision.actor_account_id) == ("manual", account.id)
    assert {"locality", "area_value", "marla_basis", "area_sqft"} == set(revision.changes)
    (after,) = events_of(view)  # no new event
    assert after.correction_used is True
    assert after.identity_fingerprint == publication.identity_fingerprint(done)
    assert after.identity_fingerprint != before.identity_fingerprint
    for column in ("id", "kind", "counted", "created_at", "idempotency_key", "account_id"):
        assert getattr(after, column) == getattr(before, column)
    assert quota.allowance_for(account.id, account.type, SOON) == allowance
    record = AuditRecord.objects.get(action="listing.corrected")
    assert (record.target_type, record.target_id, record.actor_id) == (
        "listing",
        view.id,
        account.id,
    )
    assert record.metadata == {
        "locality": True,
        "area_value": True,
        "area_unit": False,
        "marla_basis": True,
    }


def test_the_model_inputs_event_follows_the_valuation_fields_only_after_commit(
    bus, django_capture_on_commit_callbacks
):
    account, view = published()
    with django_capture_on_commit_callbacks(execute=True):
        done = correct(account, view, {"locality": south()})  # a model input (Story 4.2)
    assert bus == [ListingModelInputsChanged(listing_id=view.id)]
    assert done.revision == view.revision + 1
    bus.clear()
    account, view = published()
    with django_capture_on_commit_callbacks() as callbacks:
        correct(account, view, {"area_value": "11"})
    assert bus == [] and len(callbacks) == 1
    callbacks[0]()
    assert bus == [ListingModelInputsChanged(listing_id=view.id)]


def test_changing_the_unit_away_from_marla_clears_the_basis():
    account, view = published()
    done = correct(account, view, {"area_unit": "sqft", "area_value": "2200"})
    assert done.fields["marla_basis"] is None and Decimal(done.fields["area_sqft"]) == 2200
    assert AuditRecord.objects.get(action="listing.corrected").metadata["marla_basis"] is True


def test_a_used_up_allowance_or_a_zero_limit_never_blocks_a_correction(monkeypatch):
    account, view = published()
    monkeypatch.setattr(quota, "allowance_for", lambda *a: pytest.fail("quota was read"))
    monkeypatch.setattr(quota, "quota_limit_at", lambda *a: pytest.fail("quota was read"))
    assert correct(account, view).revision == view.revision + 1


def test_the_next_same_month_republication_is_free_and_the_old_identity_is_counted():
    account, view = published()
    done = correct(account, view, {"area_value": "11", "locality": south()})
    archive(account, done)
    assert republish(account, done, SOON + timedelta(days=2))[0] == "republish_free"
    archived = archive(account, done)
    edit(account, archived, {"area_value": "10", "locality": str(places()["area"].id)})
    assert republish(account, done, SOON + timedelta(days=3))[0] == "republish_counted"


def test_a_correction_across_the_month_edge_still_leaves_the_next_month_counted():
    late = datetime(2026, 11, 30, 18, 0, tzinfo=UTC)  # 23:00 Karachi
    account, view = published(at=late)
    done = correct(account, view, now=late + timedelta(hours=3))  # December in Karachi
    assert quota.allowance_for(account.id, account.type, late + timedelta(hours=3)).used == 0
    archive(account, done)
    assert republish(account, done, late + timedelta(hours=4))[0] == "republish_counted"


# --- the window -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "offset",
    [timedelta(0), DAY - timedelta(seconds=1), DAY - timedelta(microseconds=1)],
    ids=["at-publication", "one-second-before", "one-microsecond-before"],
)
def test_the_window_is_open_until_just_before_24_hours(offset):
    account, view = published()
    assert window(account, view, NOW + offset) == NOW + DAY
    assert correct(account, view, now=NOW + offset).revision == view.revision + 1


@pytest.mark.parametrize(
    "offset",
    [DAY, DAY + timedelta(seconds=1), timedelta(days=30), -timedelta(seconds=1)],
    ids=["exactly-24h", "one-second-after", "a-month-later", "before-the-event"],
)
def test_the_window_is_closed_from_24_hours_on(offset):
    account, view = published()
    assert window(account, view, NOW + offset) is None
    assert refused(account, view, now=NOW + offset).code == "correction_unavailable"


def test_the_error_is_a_static_409():
    error = get_error("correction_unavailable")
    assert (error.status, error.message) == (
        409,
        "The correction window is closed. Archive the listing to change these details.",
    )


def test_a_second_correction_is_refused():
    account, view = published()
    done = correct(account, view)
    assert window(account, view) is None
    assert refused(account, done, {"area_value": "12"}).code == "correction_unavailable"


def test_a_seed_listing_has_no_window():
    account, view = published()
    PublicationEvent.objects.filter(listing_id=view.id).update(kind="seed", counted=False)
    assert window(account, view) is None
    assert refused(account, view).code == "correction_unavailable"


def test_a_published_listing_without_any_event_has_no_window():
    account = agency()
    view = ready_draft(account)
    set_listing_visibility(view.id, "published")
    view = get(account, view)
    assert window(account, view) is None
    assert refused(account, view).code == "correction_unavailable"


def test_a_free_republication_neither_opens_nor_extends_a_window():
    account, view = published()
    archive(account, view)
    kind, again = republish(account, view, NOW + timedelta(hours=23))
    assert kind == "republish_free"
    # the unused window of the COUNTED event stays, with its own end
    assert window(account, again, NOW + timedelta(hours=23, minutes=30)) == NOW + DAY
    # the free event is younger than 24 hours: it is no counted event
    assert window(account, again, NOW + DAY) is None
    assert refused(account, again, now=NOW + DAY).code == "correction_unavailable"


def test_a_free_republication_does_not_reopen_a_used_window():
    account, view = published()
    done = correct(account, view)
    archive(account, done)
    kind, again = republish(account, done, NOW + timedelta(hours=2))
    assert kind == "republish_free"
    assert window(account, again, NOW + timedelta(hours=3)) is None
    code = refused(account, again, {"area_value": "12"}, now=NOW + timedelta(hours=3)).code
    assert code == "correction_unavailable"
    assert [e.correction_used for e in events_of(view)] == [True, False]


def test_a_counted_republication_opens_a_new_window():
    account, view = published()
    done = correct(account, view)
    archived = archive(account, done)
    edit(account, archived, {"area_value": "12"})
    later = NOW + timedelta(days=3)
    kind, again = republish(account, view, later)
    assert kind == "republish_counted"
    assert window(account, again, later) == later + DAY
    correct(account, again, {"area_value": "13"}, now=later + timedelta(hours=1))
    assert [(e.kind, e.correction_used) for e in events_of(view)] == [
        ("initial", True),
        ("republish_counted", True),
    ]
    assert events_of(view)[1].identity_fingerprint == publication.identity_fingerprint(
        get(account, view)
    )


def test_only_the_last_counted_event_counts_not_an_older_unused_one():
    account, view = published()
    PublicationEvent.objects.create(  # a younger counted event, already used
        account_id=account.id,
        listing_id=view.id,
        kind="republish_counted",
        counted=True,
        correction_used=True,
        created_at=NOW + timedelta(minutes=5),
    )
    assert window(account, view) is None
    assert refused(account, view).code == "correction_unavailable"


@pytest.mark.parametrize("kind", ["seed", "republish_free", "restore"])
def test_a_later_uncounted_event_does_not_move_the_window(kind):
    account, view = published()
    PublicationEvent.objects.create(
        account_id=account.id,
        listing_id=view.id,
        kind=kind,
        counted=False,
        created_at=NOW + timedelta(hours=23),
    )
    assert window(account, view) == NOW + DAY
    assert window(account, view, NOW + DAY) is None
    correct(account, view)
    initial, other = events_of(view)
    assert initial.correction_used and not other.correction_used
    assert other.identity_fingerprint is None


def test_the_clock_is_read_after_the_account_lock(monkeypatch):
    account, view = published()
    clock = {"now": NOW + DAY - timedelta(seconds=1)}

    def slow_lock(account_id):
        clock["now"] = NOW + DAY  # the wait for the lock crosses the end of the window
        return lock_account(account_id)

    monkeypatch.setattr(publication, "lock_account", slow_lock)
    monkeypatch.setattr(publication.timezone, "now", lambda: clock["now"])
    assert refused(account, view, now=None).code == "correction_unavailable"


# --- the field whitelist and the values -------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "value", "found"),
    [
        ("city", str(uuid.UUID(int=1)), {"city": "not_editable"}),
        ("category", "plot", {"category": "not_editable"}),
        ("subtype", "apartment", {"subtype": "not_editable"}),
        ("purpose", "rent", {"purpose": "not_editable"}),
        ("title", "A different family house", {"title": "not_editable"}),
        ("asking_price", "1", {"asking_price": "not_editable"}),
        ("society", "Plum Gardens", {"society": "not_editable"}),
        ("area_sqft", "5", {"area_sqft": "not_editable"}),
        ("status", "draft", {"non_field_errors": "unknown_field"}),
        ("correction_used", False, {"non_field_errors": "unknown_field"}),
    ],
)
def test_only_the_correctable_fields_are_accepted(name, value, found):
    account, view = published()
    problem = refused(account, view, {"area_value": "11", name: value})
    assert (problem.code, problem.fields) == ("validation_error", found)
    assert window(account, view) == NOW + DAY


@pytest.mark.parametrize(
    ("base", "changes", "found"),
    [
        (None, {"area_value": "11"}, {"base_revision": "required"}),
        ("2", {"area_value": "11"}, {"base_revision": "invalid"}),
        (True, {"area_value": "11"}, {"base_revision": "invalid"}),
        (0, {"area_value": "11"}, {"base_revision": "invalid"}),
        (2, None, {"fields": "required"}),
        (2, [], {"fields": "not_a_dict"}),
        (2, {}, {"fields": "empty"}),
    ],
)
def test_a_bad_request_shape_is_refused(base, changes, found):
    account, view = published()
    with pytest.raises(services.FieldsInvalid) as caught:
        publication.correct(user(account), view.id, base, changes, SOON)
    assert caught.value.fields == found
    assert window(account, view) == NOW + DAY


@pytest.mark.parametrize(
    ("changes", "found"),
    [
        ({"locality": "OTHER"}, {"locality": "locality_not_in_city"}),
        ({"locality": str(uuid.UUID(int=2))}, {"locality": "invalid"}),
        ({"locality": None}, {"locality": "required"}),
        ({"area_value": None}, {"area_value": "required"}),
        ({"area_unit": None, "area_value": "11"}, {"area_unit": "required"}),
        ({"area_value": "0"}, {"area_value": "invalid_area"}),
        ({"area_value": 11}, {"area_value": "invalid"}),
        ({"area_unit": "yard"}, {"area_unit": "invalid_choice"}),
        ({"area_value": "11", "marla_basis": "9"}, {"marla_basis": "invalid_choice"}),
        ({"area_unit": "sqft", "marla_basis": "250"}, {"marla_basis": "not_applicable"}),
        (
            {"locality": "OTHER", "area_value": "-1"},
            {"locality": "locality_not_in_city", "area_value": "invalid_area"},
        ),
        ({"marla_basis": "250"}, {"marla_basis": "not_editable"}),
        ({"marla_basis": None}, {"marla_basis": "not_editable"}),
        ({"marla_basis": "250", "locality": "SOUTH"}, {"marla_basis": "not_editable"}),
        ({"marla_basis": "250", "area_value": "10"}, {"marla_basis": "not_editable"}),
    ],
)
def test_invalid_values_are_reported_at_once_and_the_window_stays(changes, found):
    account, view = published()
    stand_ins = {"OTHER": str(places()["other"].id), "SOUTH": south()}
    changes = {k: stand_ins.get(v, v) if k == "locality" else v for k, v in changes.items()}
    problem = refused(account, view, changes)
    assert problem.fields == found
    assert problem.code == "validation_error"
    assert window(account, view) == NOW + DAY


def test_a_correction_refused_by_the_completeness_check_after_the_write_leaves_nothing(bus):
    account, view = published()
    ListingImage.objects.filter(listing_id=view.id).delete()  # no live image any more
    view = get(account, view)
    problem = refused(account, view, {"area_value": "11", "locality": south()})
    assert (problem.code, problem.fields) == ("validation_error", {"images": "images_required"})
    assert bus == []
    assert window(account, view) == NOW + DAY


@pytest.mark.parametrize(
    "changes",
    [
        {"area_value": "10"},
        {"area_value": "10.0000", "area_unit": "marla"},
        {"locality": "STORED"},
        {"marla_basis": "225"},
        {"locality": "STORED", "area_value": "10", "area_unit": "marla", "marla_basis": "225"},
    ],
)
def test_a_request_that_changes_nothing_does_not_spend_the_correction(changes):
    account, view = published()
    stored = str(view.fields["locality"])
    changes = {k: stored if v == "STORED" else v for k, v in changes.items()}
    problem = refused(account, view, changes)
    assert problem.fields == {"non_field_errors": "no_change"}
    assert window(account, view) == NOW + DAY
    assert correct(account, view).revision == view.revision + 1


def test_unchanged_values_sent_along_with_a_change_are_accepted():
    account, view = published()
    done = correct(
        account,
        view,
        {"locality": str(view.fields["locality"]), "area_value": "11", "marla_basis": "225"},
    )
    assert AuditRecord.objects.get(action="listing.corrected").metadata == {
        "locality": False,
        "area_value": True,
        "area_unit": False,
        "marla_basis": False,
    }
    assert done.revision == view.revision + 1


# --- who and when -----------------------------------------------------------------------------


@pytest.mark.parametrize("moved", ["draft", "archived", "sold", "rented", "removed"])
def test_only_a_published_listing_is_corrected(moved):
    account, view = published()
    set_listing_visibility(view.id, moved)
    assert window(account, view) is None
    assert refused(account, view).code == "conflict"


def test_a_restricted_listing_is_forbidden():
    account, view = published()
    set_listing_visibility(view.id, "published", restricted=True)
    assert window(account, view) is None
    assert refused(account, view).code == "forbidden"


@pytest.mark.parametrize("state", ["suspended", "deactivated"])
def test_a_suspended_or_deactivated_owner_is_forbidden(state):
    account, view = published()
    set_account_state(account.id, state)
    assert window(account, view) is None
    assert refused(account, view).code == "forbidden"


def test_an_unverified_owner_may_correct_as_they_may_edit():
    account, view = published()
    set_account_state(account.id, "unverified")
    assert window(account, view) == NOW + DAY
    assert correct(account, view).revision == view.revision + 1


def test_other_accounts_administrators_the_system_and_unknown_ids_are_not_found():
    account, view = published()
    other = make_account(random_email())
    admin = make_account(random_email(), "admin")
    actors = [user(other), user(admin), Actor.admin(admin.id, "r"), Actor.system()]
    for actor in actors:
        for call in (
            lambda a=actor: publication.correct(a, view.id, view.revision, {"area_value": "11"}),
            lambda a=actor: publication.correction_window(a, view.id, SOON),
        ):
            with pytest.raises(DomainError) as caught:
                call()
            assert caught.value.code == "not_found"
    for bad in (uuid.uuid4(), "nope", None):
        for call in (
            lambda b=bad: publication.correct(user(account), b, 2, {"area_value": "11"}),
            lambda b=bad: publication.correction_window(user(account), b, SOON),
        ):
            with pytest.raises(DomainError) as caught:
                call()
            assert caught.value.code == "not_found"
    for call in (
        lambda: publication.correct(Actor.visitor(), view.id, view.revision, {"area_value": "1"}),
        lambda: publication.correction_window(Actor.visitor(), view.id),
    ):
        with pytest.raises(DomainError) as caught:
            call()
        assert caught.value.code == "unauthenticated"
    assert window(account, view) == NOW + DAY


def test_a_stale_revision_carries_the_current_listing_and_comes_first():
    account, view = published()
    set_listing_visibility(view.id, "archived")
    problem = refused(account, view, base=view.revision - 1)
    assert isinstance(problem, services.StaleRevision)
    assert problem.current.revision == view.revision and problem.current.status == "archived"


def test_the_ordinary_edit_still_refuses_the_identity_fields_after_a_correction():
    account, view = published()
    done = correct(account, view)
    with pytest.raises(services.FieldsInvalid) as caught:
        edit(account, done, {"area_value": "12"})
    assert caught.value.fields == {"area_value": "not_editable"}


def test_reading_the_window_never_writes_and_gives_a_karachi_time():
    account, view = published()
    before = snapshot(view)
    expires = window(account, view)
    assert expires == NOW + DAY and expires.utcoffset() == timedelta(hours=5)
    assert snapshot(view) == before


# --- all or nothing and concurrency -----------------------------------------------------------


def boom(*args, **kwargs):
    raise RuntimeError("injected")


@pytest.mark.parametrize("point", ["revision", "event", "audit", "emit"])
def test_a_failure_at_any_step_leaves_everything_untouched(
    point, bus, monkeypatch, django_capture_on_commit_callbacks
):
    account, view = published()
    before = snapshot(view)
    with monkeypatch.context() as patch:
        if point == "revision":
            patch.setattr(ListingRevision.objects, "create", boom)
        elif point == "event":
            patch.setattr(publication, "identity_fingerprint", boom)
        elif point == "audit":
            patch.setattr(audit, "record", boom)
        else:
            patch.setattr(publication, "emit", boom)
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            with pytest.raises(RuntimeError, match="injected"):
                correct(account, view, {"area_value": "11", "locality": south()})
    assert callbacks == [] and bus == []
    assert snapshot(view) == before
    assert correct(account, view).revision == view.revision + 1  # still unused


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_corrections_succeed_exactly_once():
    seed_cap()
    seed_media_policies()
    account, view = published()
    kinds, _ = run_threads(
        2, lambda i: correct(account, view, {"area_value": str(11 + i)}).revision
    )
    assert kinds == ["ok", "stale_revision"]
    assert get(account, view).revision == view.revision + 1
    assert AuditRecord.objects.filter(action="listing.corrected").count() == 1
    (event,) = events_of(view)
    assert event.correction_used
    assert event.identity_fingerprint == publication.identity_fingerprint(get(account, view))


@pytest.mark.django_db(transaction=True)
def test_two_corrections_of_two_revisions_still_spend_the_window_once():
    """The second one is not stale (it names the revision the first will produce), so only the
    used flag can stop it."""
    seed_cap()
    seed_media_policies()
    account, view = published()

    def work(index):
        for _ in range(200):
            try:
                return correct(
                    account, view, {"area_value": str(11 + index)}, base=view.revision + index
                )
            except services.StaleRevision as exc:
                if index == 0 or exc.current.revision > view.revision:
                    raise
        raise AssertionError("the first correction never finished")

    kinds, _ = run_threads(2, work)
    assert kinds == ["correction_unavailable", "ok"]
    assert AuditRecord.objects.filter(action="listing.corrected").count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("target", ["archived", "sold"])
def test_a_correction_racing_a_status_change_has_one_winner(target):
    seed_cap()
    seed_media_policies()
    account, view = published()
    before = events_of(view)[0].identity_fingerprint

    def work(index):
        if index == 0:
            return correct(account, view)
        return status.change_status(user(account), view.id, view.revision, target)

    kinds, _ = run_threads(2, work)
    assert kinds == ["ok", "stale_revision"]
    final = get(account, view)
    (event,) = events_of(view)
    assert final.revision == view.revision + 1
    if final.status == "published":  # the correction won
        assert event.correction_used and Decimal(final.fields["area_value"]) == 11
    else:
        assert final.status == target and Decimal(final.fields["area_value"]) == 10
        assert (event.correction_used, event.identity_fingerprint) == (False, before)


@pytest.mark.django_db(transaction=True)
def test_a_correction_racing_an_ordinary_edit_has_one_winner():
    seed_cap()
    seed_media_policies()
    account, view = published()

    def work(index):
        if index == 0:
            return correct(account, view)
        return edit(account, view, {"title": "A different family house"})

    kinds, _ = run_threads(2, work)
    assert kinds == ["ok", "stale_revision"]
    assert get(account, view).revision == view.revision + 1
    assert events_of(view)[0].correction_used == (
        Decimal(get(account, view).fields["area_value"]) == 11
    )


def test_the_event_update_runs_inside_the_same_transaction(monkeypatch):
    """The revision, the event mark and the audit record commit together or not at all (the
    failure tests above); here the mark is made while the transaction is open."""
    account, view = published()
    seen = []
    original = audit.record

    def watch(*args, **kwargs):
        seen.append((connection.in_atomic_block, events_of(view)[0].correction_used))
        return original(*args, **kwargs)

    monkeypatch.setattr(audit, "record", watch)
    correct(account, view)
    assert seen == [(True, True)]


# --- the version-10 fingerprint migration -----------------------------------------------------

V9 = "apps.listings.migrations.0015_identity_fingerprint_v9"
V10 = "apps.listings.migrations.0016_identity_fingerprint_v10"


def test_the_frozen_version_10_canonicalisation_matches_the_live_one():
    module = importlib.import_module(V10)
    assert set(module.IDENTITY) == set(IDENTITY)
    assert set(module.OLD_IDENTITY) == {*IDENTITY, "marla_basis"}
    assert set(importlib.import_module(V9).IDENTITY) == set(module.OLD_IDENTITY)
    place = places()
    cases = [
        {},
        {"area_value": "10", "area_unit": "marla", "marla_basis": "225"},
        {"area_value": "250.5000", "area_unit": "sqft"},
        {"city": str(place["karachi"].id), "locality": str(place["area"].id)},
    ]
    for extra in cases:
        fields = {"purpose": "sale", "category": "house", "subtype": "house", **extra}
        live = publication.identity_fingerprint(SimpleNamespace(fields=fields))
        stored = dict(fields)
        for name in ("area_value", "marla_basis"):
            stored[name] = Decimal(fields[name]) if name in fields else None
        assert module.fingerprint(stored) == live, extra


@pytest.mark.django_db(transaction=True)
def test_the_version_10_migration_recomputes_unchanged_nulls_drifted_and_keeps_the_rest():
    from django.db.migrations.executor import MigrationExecutor

    from apps.listings.identity_migration import fingerprint

    seed_cap()
    seed_media_policies()
    module = importlib.import_module(V10)
    account = agency()
    _, unchanged = published(account)
    _, drifted = published(account)
    _, already = published(account)
    _, gone = published(account)
    _, unset = published(account)

    def old(values):
        return fingerprint(module.OLD_IDENTITY, values, module._canonical)

    def stored(view):
        return [e.identity_fingerprint for e in events_of(view)]

    def put(view, value):
        PublicationEvent.objects.filter(listing_id=view.id).update(identity_fingerprint=value)

    def ledger():
        return (
            list(
                PublicationEvent.objects.order_by("id").values_list(
                    "id", "kind", "counted", "correction_used", "created_at"
                )
            ),
            AuditRecord.objects.count(),
            IdempotencyRecord.objects.count(),
        )

    conn = connections["default"]
    try:
        put(unchanged, old(unchanged.fields))
        put(drifted, old({**drifted.fields, "marla_basis": "250"}))  # no proof any more
        put(already, publication.identity_fingerprint(already))
        put(gone, old(gone.fields))
        put(unset, None)
        # before the rollback: the current models do not fit an older schema
        PropertyListing.objects.filter(id=gone.id).delete()
        MigrationExecutor(conn).migrate([("listings", "0015_identity_fingerprint_v9")])
        before = ledger()
        MigrationExecutor(conn).migrate([("listings", "0016_identity_fingerprint_v10")])
        assert stored(unchanged) == [publication.identity_fingerprint(unchanged)]
        assert stored(unchanged) != [old(unchanged.fields)]
        assert stored(drifted) == [None]
        assert stored(already) == [publication.identity_fingerprint(already)]
        assert stored(gone) == [old(gone.fields)]
        assert stored(unset) == [None]
        assert ledger() == before
        MigrationExecutor(conn).migrate([("listings", "0015_identity_fingerprint_v9")])  # no-op
        assert stored(unchanged) == [publication.identity_fingerprint(unchanged)]
        MigrationExecutor(conn).migrate([("listings", "0016_identity_fingerprint_v10")])  # again
        assert stored(unchanged) == [publication.identity_fingerprint(unchanged)]
        assert stored(drifted) == [None] and ledger() == before
    finally:
        MigrationExecutor(conn).migrate(MigrationExecutor(conn).loader.graph.leaf_nodes())
    archive(account, unchanged)
    assert republish(account, unchanged, SOON)[0] == "republish_free"
