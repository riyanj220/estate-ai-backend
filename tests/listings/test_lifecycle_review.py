"""Story 2.17 review batch: the fingerprint migration, what a Published edit is answerable for,
identity resends, sold/rented against purpose, the last live image, the no-quota ledger, and
Suspended and restricted corners. Values are low-entropy fake words."""

import contextlib
import hashlib
import importlib
import threading
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.db import connection, connections
from django.utils import timezone

from apps.catalog import registry
from apps.catalog.models import City, Locality
from apps.kernel import audit, events, media
from apps.kernel.models import IdempotencyRecord, MediaAsset, StorageUsage
from apps.listings import completeness, publication, quota, selectors, services
from apps.listings import validation as listing_validation
from apps.listings.events import ListingStatusChanged
from apps.listings.models import ListingImage, PropertyListing, PublicationEvent
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
from tests.listings.test_lifecycle import (
    PNG,
    agency,
    codes_of,
    edit,
    move,
    published,
    published_for,
    refused,
    row_of,
    snapshot,
)

MIGRATION = "apps.listings.migrations.0015_identity_fingerprint_v9"
OLD_SET = hashlib.sha256(b'{"category":"house","purpose":"sale","subtype":"house"}').hexdigest()


@pytest.fixture(autouse=True)
def setup(db):
    seed_cap()
    seed_media_policies()


@pytest.fixture
def bus(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    seen = []
    events.subscribe(ListingStatusChanged, seen.append)
    return seen


def ledger(account):
    """Everything a publication could touch: keys, events and the allowance."""
    return (
        IdempotencyRecord.objects.count(),
        PublicationEvent.objects.count(),
        quota.owner_allowance(user(account), timezone.now()),
    )


def real_published(account=None, images=2):
    """A Published Listing whose images were really uploaded (stored files, asset rows, usage).
    The status is set directly: the allowance is not what these tests are about."""
    account = account or agency()
    view = ready_draft(account, images=0)
    for _ in range(images):
        services.upload_image(user(account), view.id, PNG)
    set_listing_visibility(view.id, "published")
    return account, selectors.get_owned_listing(user(account), view.id)


# --- the fingerprint migration ----------------------------------------------------------------


def test_the_migration_recomputes_an_old_set_fingerprint_without_touching_anything_else():
    account, view = published()
    other = make_account(random_email())
    gone = ready_draft(other)
    PublicationEvent.objects.create(
        account_id=other.id, listing_id=gone.id, kind="seed", counted=False,
        identity_fingerprint=OLD_SET,
    )  # fmt: skip
    PropertyListing.objects.filter(id=gone.id).delete()  # its event keeps the old value
    PublicationEvent.objects.filter(listing_id=view.id).update(identity_fingerprint=OLD_SET)
    before = ledger(account)
    event = PublicationEvent.objects.get(listing_id=view.id)
    keep = (event.id, event.kind, event.counted, event.created_at, event.idempotency_key)
    module = importlib.import_module(MIGRATION)
    module.recompute(django_apps, None)
    event.refresh_from_db()
    fresh = selectors.get_owned_listing(user(account), view.id)
    assert event.identity_fingerprint == publication.identity_fingerprint(fresh) != OLD_SET
    assert (event.id, event.kind, event.counted, event.created_at, event.idempotency_key) == keep
    assert PublicationEvent.objects.get(listing_id=gone.id).identity_fingerprint == OLD_SET
    assert ledger(account) == before  # nothing consumed, nothing written
    module.recompute(django_apps, None)  # idempotent
    event.refresh_from_db()
    assert event.identity_fingerprint == publication.identity_fingerprint(fresh)


def test_the_frozen_canonicalisation_matches_the_live_one_for_every_identity_field_shape():
    module = importlib.import_module(MIGRATION)
    place = places()
    cases = [
        {},
        {"area_value": "10", "area_unit": "marla", "marla_basis": "225"},
        {"area_value": "250.5000", "area_unit": "sqft"},
        {"area_value": "0.5", "area_unit": "kanal", "marla_basis": "272.25"},
        {"city": str(place["karachi"].id), "locality": str(place["area"].id)},
        {"city": str(place["lahore"].id)},
    ]
    from decimal import Decimal

    for extra in cases:
        fields = {"purpose": "sale", "category": "house", "subtype": "house", **extra}
        live = publication.identity_fingerprint(SimpleNamespace(fields=fields))
        stored = {
            **fields,
            "area_value": Decimal(fields["area_value"]) if "area_value" in fields else None,
            "marla_basis": Decimal(fields["marla_basis"]) if "marla_basis" in fields else None,
        }
        assert module.fingerprint(stored) == live, extra


@pytest.mark.django_db(transaction=True)
def test_the_migration_runs_through_the_executor_and_back_as_a_no_op():
    from django.db.migrations.executor import MigrationExecutor

    seed_cap()
    seed_media_policies()
    account, view = published()
    PublicationEvent.objects.update(identity_fingerprint=OLD_SET)
    conn = connections["default"]
    try:
        MigrationExecutor(conn).migrate([("listings", "0014_lifecycle_statuses")])
        assert PublicationEvent.objects.get().identity_fingerprint == OLD_SET  # reverse: no-op
        MigrationExecutor(conn).migrate([("listings", "0015_identity_fingerprint_v9")])
        event = PublicationEvent.objects.get()
        assert event.identity_fingerprint == publication.identity_fingerprint(
            selectors.get_owned_listing(user(account), view.id)
        )
    finally:
        MigrationExecutor(conn).migrate(MigrationExecutor(conn).loader.graph.leaf_nodes())


# --- a Published edit answers only for what it touches ----------------------------------------


@pytest.mark.parametrize("kind", ["locality", "city"])
def test_a_deactivated_place_does_not_block_an_unrelated_published_edit(kind):
    account, view = published()
    place = places()
    model = Locality if kind == "locality" else City
    model.objects.filter(id=place["area" if kind == "locality" else "karachi"].id).update(
        is_active=False
    )
    done = edit(account, view, {"title": "A different family house", "asking_price": "1750000"})
    assert done.revision == view.revision + 1 and done.status == "published"
    assert done.fields["city"] == view.fields["city"]
    assert done.fields["locality"] == view.fields["locality"]


def test_a_tightened_rule_on_an_untouched_field_does_not_block_but_touching_it_does(monkeypatch):
    account, view = published()
    real = listing_validation.evaluate

    def tightened(*args, **kwargs):
        values, invalid = real(*args, **kwargs)
        return values, {**invalid, "bathrooms": "invalid_range"}

    monkeypatch.setattr(
        completeness,
        "validation",
        SimpleNamespace(evaluate=tightened, NON_FIELD=listing_validation.NON_FIELD),
    )
    done = edit(account, view, {"title": "A different family house"})
    assert done.revision == view.revision + 1
    assert codes_of(account, done, {"bathrooms": 3}) == {"bathrooms": "invalid_range"}
    assert row_of(view).revision == done.revision


def test_a_newly_required_field_blocks_until_it_is_filled(monkeypatch):
    import dataclasses

    account, view = published()
    flagged = tuple(
        dataclasses.replace(f, requirement="required_for_publication") if f.id == "condition" else f
        for f in registry.fields()
    )
    monkeypatch.setattr(registry, "fields", lambda: flagged)
    before = snapshot()
    assert codes_of(account, view, {"title": "A different family house"}) == {
        "condition": "required"
    }
    assert snapshot() == before
    done = edit(account, view, {"condition": "good", "title": "A different family house"})
    assert done.fields["condition"] == "good"


def test_a_ghost_image_only_listing_takes_a_real_upload_and_a_no_op_patch_passes():
    account, view = published()
    MediaAsset.objects.filter(owner_account_id=account.id).delete()  # the link is now a ghost
    assert selectors.live_image_count(view.id) == 0
    assert codes_of(account, view, {"title": "A different family house"}) == {
        "images": "images_required"
    }
    again = edit(account, view, {"title": view.fields["title"]})  # changes nothing: no check
    assert again.revision == view.revision
    services.upload_image(user(account), view.id, PNG)
    assert selectors.live_image_count(view.id) == 1
    current = selectors.get_owned_listing(user(account), view.id)
    assert edit(account, current, {"title": "A different family house"}).revision == (
        current.revision + 1
    )


# --- identity fields: resends and the city-only change ------------------------------------------


def forms(value):
    return [str(value), str(value).upper(), value.hex, "{" + str(value) + "}"]


@pytest.mark.parametrize(
    "field",
    ["city", "locality", "area_value", "area_unit", "marla_basis"],
)
def test_resending_a_stored_identity_value_in_any_form_is_accepted_and_changes_nothing(field):
    account, view = published()
    stored = {f: view.fields[f] for f in ("city", "locality", "area_value", "area_unit")}
    stored["marla_basis"] = view.fields["marla_basis"]
    assert stored["area_value"] == "10.0000" and stored["marla_basis"] == "225"
    if field in ("city", "locality"):
        import uuid

        sends = forms(uuid.UUID(stored[field]))
    else:
        sends = {
            "area_value": ["10", "10.00", "10.0000"],
            "area_unit": ["marla"],
            "marla_basis": ["225"],
        }[field]
    for sent in sends:
        before = snapshot()
        done = edit(account, view, {field: sent})
        assert done.revision == view.revision, (field, sent)
        assert snapshot() == before, (field, sent)


def test_a_city_only_change_is_refused_on_the_city_and_the_implicit_locality_clear_cannot_leak():
    account, view = published()
    place = places()
    before = snapshot()
    assert codes_of(account, view, {"city": str(place["lahore"].id)}) == {"city": "not_editable"}
    assert snapshot() == before
    row = selectors.get_owned_listing(user(account), view.id)
    assert row.fields["city"] == view.fields["city"]
    assert row.fields["locality"] == view.fields["locality"]


def test_a_more_specific_code_is_never_replaced_by_the_identity_lock():
    account, view = published()
    assert codes_of(account, view, {"area_unit": "furlong"}) == {"area_unit": "invalid_choice"}
    assert codes_of(account, view, {"area_value": "abc"})["area_value"] != "not_editable"


# --- sold is for a sale, rented for a rental ---------------------------------------------------


@pytest.mark.parametrize(("values", "wrong"), [(HOUSE, "rented"), (RENT_HOUSE, "sold")])
def test_the_outcome_must_match_the_purpose(values, wrong, bus, django_capture_on_commit_callbacks):
    account, view = published(values=values)
    before = snapshot()
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        refused(lambda: move(account, view, wrong), "conflict")
    assert snapshot() == before and callbacks == [] and bus == []
    assert move(account, view, "archived").status == "archived"  # archive has no purpose rule


# --- images ------------------------------------------------------------------------------------


def storage_state(account):
    usage = StorageUsage.objects.filter(account_id=account.id).first()
    return (
        MediaAsset.objects.filter(owner_account_id=account.id).count(),
        usage.bytes_used if usage else 0,
        sorted(MediaAsset.objects.values_list("storage_key", flat=True)),
    )


def test_the_last_image_is_refused_before_anything_is_deleted(monkeypatch):
    account, view = real_published(images=1)
    image = ListingImage.objects.get(listing_id=view.id)
    before = (snapshot(), storage_state(account))
    calls = []
    monkeypatch.setattr(media, "delete_asset", lambda *a, **k: calls.append(a))
    with pytest.raises(services.FieldsInvalid) as caught:
        services.remove_image(user(account), view.id, image.id)
    assert caught.value.fields == {"images": "images_required"}
    assert calls == []  # not even attempted: the rule is decided first
    assert (snapshot(), storage_state(account)) == before


def test_removing_one_of_two_real_images_gives_the_bytes_back_and_keeps_the_other():
    account, view = real_published(images=2)
    first, second = ListingImage.objects.filter(listing_id=view.id).order_by("position")
    one = StorageUsage.objects.get(account_id=account.id).bytes_used
    services.remove_image(user(account), view.id, second.id)
    count, used, _ = storage_state(account)
    assert count == 1 and 0 < used < one
    assert ListingImage.objects.filter(listing_id=view.id).count() == 1


def test_a_reorder_that_ghost_repair_would_empty_is_refused_and_the_repair_rolled_back():
    account, view = published()
    MediaAsset.objects.filter(owner_account_id=account.id).delete()
    link = ListingImage.objects.get(listing_id=view.id)
    before = snapshot()
    with pytest.raises(services.FieldsInvalid) as caught:
        services.set_image_order(user(account), view.id, view.revision, [link.id])
    assert caught.value.fields == {"images": "images_required"}
    assert snapshot() == before
    assert ListingImage.objects.filter(listing_id=view.id).count() == 1  # the ghost link stays


@pytest.mark.django_db(transaction=True)
def test_two_removals_racing_on_a_published_listing_leave_exactly_one_image():
    seed_cap()
    seed_media_policies()
    account, view = real_published(images=2)
    links = list(ListingImage.objects.filter(listing_id=view.id))
    barrier, outcomes = threading.Barrier(2), []

    def work(link):
        try:
            barrier.wait()
            services.remove_image(user(account), view.id, link.id)
            outcomes.append("removed")
        except services.FieldsInvalid as exc:
            outcomes.append(exc.fields["images"])
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)
        finally:
            connection.close()

    threads = [threading.Thread(target=work, args=(link,)) for link in links]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads)
    assert sorted(outcomes) == ["images_required", "removed"]
    assert ListingImage.objects.filter(listing_id=view.id).count() == 1
    [asset] = MediaAsset.objects.filter(owner_account_id=account.id)
    assert StorageUsage.objects.get(account_id=account.id).bytes_used == asset.bytes
    assert selectors.live_image_count(view.id) == 1


# --- the no-quota ledger ----------------------------------------------------------------------


@pytest.mark.parametrize("target", ["archived", "sold", "rented"])
def test_a_status_move_leaves_the_ledger_untouched(target):
    account, view = published_for(target)
    before = ledger(account)
    move(account, view, target)
    assert ledger(account) == before


def test_archived_edits_and_published_image_changes_leave_the_ledger_untouched():
    account, view = real_published(images=1)
    before = ledger(account)
    uploaded = services.upload_image(user(account), view.id, PNG)
    current = selectors.get_owned_listing(user(account), view.id)
    order = [m.id for m in reversed(current.media)]
    services.set_image_order(user(account), view.id, current.revision, order, uploaded.id)
    services.remove_image(user(account), view.id, uploaded.id)
    current = selectors.get_owned_listing(user(account), view.id)
    archived = move(account, current, "archived")
    edit(account, archived, {"title": None, "area_value": "11"})
    assert ledger(account) == before


# --- events under the commit hook -------------------------------------------------------------


@pytest.mark.parametrize("target", ["archived", "sold", "rented"])
def test_each_move_emits_exactly_one_event_after_commit(
    target, bus, django_capture_on_commit_callbacks
):
    account, view = published_for(target)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        move(account, view, target)
    assert len(callbacks) == 1
    assert bus == [ListingStatusChanged(listing_id=view.id, account_id=account.id, status=target)]


def test_a_failing_audit_write_emits_nothing_and_changes_nothing(
    monkeypatch, bus, django_capture_on_commit_callbacks
):
    account, view = published()
    before = snapshot()

    def boom(*args, **kwargs):
        raise RuntimeError("audit down")

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with monkeypatch.context() as scoped:
            scoped.setattr(audit, "record", boom)
            with pytest.raises(RuntimeError):
                move(account, view, "archived")
    assert callbacks == [] and bus == []  # the bus patch is still in force here
    assert snapshot() == before


@pytest.mark.parametrize(
    "refusal",
    ["stale", "conflict", "removed", "restricted", "suspended", "bad_target"],
)
def test_a_refusal_schedules_no_callback_and_emits_nothing(
    refusal, bus, django_capture_on_commit_callbacks
):
    account, view = published()
    call = {
        "stale": lambda: move(account, view, "archived", base=99),
        "conflict": lambda: (move(account, view, "archived"), move(account, view, "archived")),
        "removed": lambda: (
            set_listing_visibility(view.id, "removed"),
            move(account, view, "sold"),
        ),
        "restricted": lambda: (
            PropertyListing.objects.filter(id=view.id).update(restricted=True),
            move(account, view, "sold"),
        ),
        "suspended": lambda: (
            set_account_state(account.id, "suspended"),
            move(account, view, "sold"),
        ),
        "bad_target": lambda: move(account, view, "published"),
    }[refusal]
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with contextlib.suppress(Exception):  # the refusals themselves have their own tests
            call()
    expected = 1 if refusal == "conflict" else 0  # the first, valid archive in that case
    assert len(callbacks) == expected and len(bus) == expected


# --- Suspended and restricted corners -----------------------------------------------------------


@pytest.mark.parametrize("state", ["sold", "rented"])
def test_a_suspended_owner_may_archive_a_sold_or_rented_listing(state):
    account, view = published_for(state)
    done = move(account, view, state)
    set_account_state(account.id, "suspended")
    assert move(account, done, "archived").status == "archived"


def test_a_restricted_listing_refuses_reorder_and_cover_changes():
    account, view = real_published(images=2)
    PropertyListing.objects.filter(id=view.id).update(restricted=True)
    current = selectors.get_owned_listing(user(account), view.id)
    ids = [m.id for m in current.media]
    before = snapshot()
    refused(
        lambda: services.set_image_order(
            user(account), view.id, current.revision, list(reversed(ids))
        ),
        "forbidden",
    )
    refused(
        lambda: services.set_image_order(user(account), view.id, current.revision, ids, ids[1]),
        "forbidden",
    )
    assert snapshot() == before


def test_a_publish_replay_after_a_status_change_returns_the_owners_current_view():
    account = agency()
    view = ready_draft(account)
    first = publication.publish(user(account), view.id, view.revision, "replay-key")
    move(account, first, "archived")
    replay = publication.publish(user(account), view.id, view.revision, "replay-key")
    assert replay.status == "archived"  # clients must read `status`; nothing was published again
    assert PublicationEvent.objects.count() == 1
