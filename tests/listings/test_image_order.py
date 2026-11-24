"""Cover and order of a Draft's images (Story 2.10): `set_image_order`, the cover rules of upload
and remove, the media wire of the Listing view, the database constraints and the migration."""

import threading
import uuid

import pytest
from django.db import IntegrityError, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.identity.models import Account
from apps.kernel import runtime
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import MediaAsset
from apps.listings import lifecycle, selectors, services
from apps.listings.models import MAX_IMAGES, ListingImage, ListingRevision, PropertyListing
from tests.imaging import make_image
from tests.listings.support import (
    HOUSE,
    SYSTEM,
    make_account,
    random_email,
    seed_cap,
    seed_media_policies,
    user,
)

PNG = make_image("PNG", (400, 300))


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


def draft(account=None, count=0):
    account = account or make_account(random_email())
    view = services.create_draft(user(account), *HOUSE)
    images = [services.upload_image(user(account), view.id, PNG) for _ in range(count)]
    return account, view, images


def stored(listing_id):
    """(id, position, is_cover) of the images, in position order."""
    rows = ListingImage.objects.filter(listing_id=listing_id).order_by("position")
    return [(r.id, r.position, r.is_cover) for r in rows]


def revision(listing_id):
    return PropertyListing.objects.get(id=listing_id).revision


def reorder(account, view, order, base=None, cover=None):
    base = revision(view.id) if base is None else base
    return services.set_image_order(user(account), view.id, base, order, cover)


def code_of(call, *args):
    with pytest.raises(DomainError) as caught:
        call(*args)
    return caught.value.code


def invalid_fields(account, view, order, base=None, cover=None):
    with pytest.raises(services.FieldsInvalid) as caught:
        reorder(account, view, order, base, cover)
    return caught.value.fields


# --- cover rules of upload and remove ----------------------------------------------------------


def test_the_first_image_is_the_cover_and_later_uploads_never_change_it():
    account, view, (first, second, third) = draft(count=3)
    assert (first.position, first.is_cover) == (0, True)
    assert (second.position, second.is_cover, third.position, third.is_cover) == (
        1,
        False,
        2,
        False,
    )
    assert [(p, c) for _, p, c in stored(view.id)] == [(0, True), (1, False), (2, False)]


def test_removing_the_cover_promotes_the_lowest_remaining_position_and_renumbers():
    account, view, (a, b, c) = draft(count=3)
    reorder(account, view, [b.id, c.id, a.id], cover=c.id)  # cover c, order b c a
    services.remove_image(user(account), view.id, c.id)
    assert stored(view.id) == [(b.id, 0, True), (a.id, 1, False)]  # the lowest, b, is promoted


def test_removing_a_non_cover_keeps_the_cover_and_renumbers():
    account, view, (a, b, c) = draft(count=3)
    services.remove_image(user(account), view.id, b.id)
    assert stored(view.id) == [(a.id, 0, True), (c.id, 1, False)]
    reorder(account, view, [c.id, a.id])
    services.remove_image(user(account), view.id, c.id)  # position 0 but not the cover
    assert stored(view.id) == [(a.id, 0, True)]


def test_removing_the_last_image_leaves_no_cover_and_an_empty_media_list():
    account, view, (only,) = draft(count=1)
    services.remove_image(user(account), view.id, only.id)
    assert stored(view.id) == []
    assert selectors.get_owned_listing(user(account), view.id).media == ()
    again = services.upload_image(user(account), view.id, PNG)
    assert (again.position, again.is_cover) == (0, True)


# --- the wire of the Listing view --------------------------------------------------------------


def test_a_draft_without_images_has_an_empty_media_list_and_edits_are_unaffected():
    account, view, _ = draft()
    assert view.media == ()
    edited = services.apply_updates(user(account), view.id, 1, {"title": "A fine family house"})
    assert edited.media == ()


def test_the_view_lists_the_images_in_position_order_without_internal_ids():
    account, view, (a, b) = draft(count=2)
    reorder(account, view, [b.id, a.id])
    media = selectors.get_owned_listing(user(account), view.id).media
    assert [(m.id, m.position, m.is_cover) for m in media] == [(b.id, 0, False), (a.id, 1, True)]
    asset = MediaAsset.objects.get(pk=ListingImage.objects.get(pk=a.id).asset_id)
    first = media[1]
    assert (first.content_type, first.width, first.height, first.bytes) == (
        asset.content_type,
        asset.width,
        asset.height,
        asset.bytes,
    )
    assert first.url == f"/api/v1/listings/{view.id}/images/{a.id}/file"
    leaked = {"asset_id", "storage_key", "owner_account_id", "path"}
    assert leaked.isdisjoint(vars(first))
    assert asset.storage_key not in repr(media) and str(asset.id) not in repr(media)


def test_every_mutation_and_the_list_return_the_media():
    account, view, (a, b) = draft(count=2)
    result = reorder(account, view, [b.id, a.id])
    assert [m.id for m in result.media] == [b.id, a.id]
    edited = services.apply_updates(
        user(account), view.id, result.revision, {"title": "A fine family house"}
    )
    assert [m.id for m in edited.media] == [b.id, a.id]
    page = selectors.list_owned(user(account))
    assert [m.id for m in page.items[0].media] == [b.id, a.id]


# --- set_image_order ---------------------------------------------------------------------------


def test_reorder_rewrites_positions_bumps_once_and_writes_one_revision_row():
    account, view, (a, b, c) = draft(count=3)
    before = revision(view.id)
    result = reorder(account, view, [c.id, a.id, b.id])
    assert result.revision == before + 1 == revision(view.id)
    assert stored(view.id) == [(c.id, 0, False), (a.id, 1, True), (b.id, 2, False)]
    row = ListingRevision.objects.get(listing_id=view.id, revision=before + 1)
    assert row.changes == {"images": 3, "order": True, "cover": False}
    assert (row.origin, row.actor_account_id) == ("manual", account.id)
    assert ListingRevision.objects.filter(listing_id=view.id).count() == before + 1


def test_choosing_a_cover_leaves_exactly_one_cover():
    account, view, (a, b, c) = draft(count=3)
    before = revision(view.id)
    result = reorder(account, view, [a.id, b.id, c.id], cover=c.id)
    assert stored(view.id) == [(a.id, 0, False), (b.id, 1, False), (c.id, 2, True)]
    assert [m.is_cover for m in result.media] == [False, False, True]
    row = ListingRevision.objects.get(listing_id=view.id, revision=before + 1)
    assert row.changes == {"images": 3, "order": False, "cover": True}
    assert sum(1 for _, _, cover in stored(view.id) if cover) == 1


def test_order_and_cover_together_are_one_revision():
    account, view, (a, b) = draft(count=2)
    before = revision(view.id)
    reorder(account, view, [b.id, a.id], cover=b.id)
    assert revision(view.id) == before + 1
    assert ListingRevision.objects.get(listing_id=view.id, revision=before + 1).changes == {
        "images": 2,
        "order": True,
        "cover": True,
    }


@pytest.mark.parametrize("cover_given", [False, True])
def test_a_request_that_changes_nothing_writes_nothing(cover_given):
    account, view, (a, b) = draft(count=2)
    before, rows = revision(view.id), ListingRevision.objects.count()
    result = reorder(account, view, [a.id, b.id], cover=a.id if cover_given else None)
    assert result.revision == before == revision(view.id)
    assert ListingRevision.objects.count() == rows
    assert stored(view.id) == [(a.id, 0, True), (b.id, 1, False)]


def test_an_empty_listing_accepts_an_empty_order_as_no_change():
    account, view, _ = draft()
    assert reorder(account, view, []).revision == 1
    assert invalid_fields(account, view, [], cover=uuid.uuid4()) == {"cover_id": "invalid"}


def test_ids_may_be_given_as_canonical_text():
    account, view, (a, b) = draft(count=2)
    result = reorder(account, view, [str(b.id), str(a.id)], cover=str(b.id))
    assert [m.id for m in result.media] == [b.id, a.id] and result.media[0].is_cover


def test_a_stale_revision_carries_the_current_view_and_writes_nothing():
    account, view, (a, b) = draft(count=2)
    old = revision(view.id)
    reorder(account, view, [b.id, a.id])
    rows, current = ListingRevision.objects.count(), stored(view.id)
    with pytest.raises(services.StaleRevision) as caught:
        reorder(account, view, [a.id, b.id], base=old)  # a replay of the earlier base
    assert caught.value.current.revision == old + 1
    assert [m.id for m in caught.value.current.media] == [b.id, a.id]
    assert ListingRevision.objects.count() == rows and stored(view.id) == current


def test_stale_is_decided_before_the_order_is_judged():
    account, view, (a, b) = draft(count=2)
    with pytest.raises(services.StaleRevision):
        reorder(account, view, [a.id], base=1)  # both stale and a bad order: stale wins


@pytest.mark.parametrize(
    "order",
    [
        pytest.param(lambda ids: ids[:1], id="missing"),
        pytest.param(lambda ids: [ids[0], ids[0]], id="duplicate"),
        pytest.param(lambda ids: [*ids, uuid.uuid4()], id="extra-unknown"),
        pytest.param(lambda ids: [ids[0], uuid.uuid4()], id="unknown-replacing"),
        pytest.param(lambda ids: [*ids, ids[0]], id="duplicate-extra"),
        pytest.param(lambda ids: [], id="empty"),
    ],
)
def test_a_bad_order_is_invalid_and_nothing_is_written(order):
    account, view, (a, b) = draft(count=2)
    before, rows, current = revision(view.id), ListingRevision.objects.count(), stored(view.id)
    assert invalid_fields(account, view, order([a.id, b.id])) == {"order": "invalid"}
    assert revision(view.id) == before and ListingRevision.objects.count() == rows
    assert stored(view.id) == current


def test_another_listings_image_is_not_a_valid_order_member_or_cover():
    account, view, (a, b) = draft(count=2)
    other_account, other_view, (foreign,) = draft(count=1)
    assert invalid_fields(account, view, [a.id, foreign.id]) == {"order": "invalid"}
    assert invalid_fields(account, view, [a.id, b.id], cover=foreign.id) == {"cover_id": "invalid"}
    assert invalid_fields(account, view, [a.id, foreign.id], cover=foreign.id) == {
        "order": "invalid",
        "cover_id": "invalid",
    }
    assert stored(other_view.id) == [(foreign.id, 0, True)]


@pytest.mark.parametrize("order", ["abc", {"a": 1}, 5, [1, 2], ["x"], [None], [[]], [True]])
def test_an_order_that_is_not_a_list_of_ids_is_invalid(order):
    account, view, _ = draft(count=2)
    assert invalid_fields(account, view, order) == {"order": "invalid"}


def test_a_missing_order_and_a_bad_cover_or_base_revision_are_reported_together():
    account, view, _ = draft(count=1)
    with pytest.raises(services.FieldsInvalid) as caught:
        services.set_image_order(user(account), view.id, 0, None, "nope")
    assert caught.value.fields == {
        "base_revision": "invalid",
        "order": "required",
        "cover_id": "invalid",
    }
    for base in (None, True, "1", 1.5, -2):
        with pytest.raises(services.FieldsInvalid) as caught:
            services.set_image_order(user(account), view.id, base, [], None)
        assert set(caught.value.fields) == {"base_revision"}


def test_a_cover_that_is_not_an_image_of_the_listing_is_invalid():
    account, view, (a, b) = draft(count=2)
    assert invalid_fields(account, view, [a.id, b.id], cover=uuid.uuid4()) == {
        "cover_id": "invalid"
    }
    assert stored(view.id) == [(a.id, 0, True), (b.id, 1, False)]


# --- refusals ----------------------------------------------------------------------------------


def test_a_visitor_is_unauthenticated():
    _, view, _ = draft()
    assert code_of(services.set_image_order, Actor.visitor("r"), view.id, 1, [], None) == (
        "unauthenticated"
    )


def test_everyone_but_the_owner_gets_not_found():
    owner, view, (a,) = draft(count=1)
    other = make_account(random_email())
    admin = make_account(random_email())
    Account.objects.filter(id=admin.id).update(account_type="admin")
    for actor in (user(other), Actor.admin(admin.id, "r"), SYSTEM, Actor.user(uuid.uuid4(), "r")):
        assert code_of(services.set_image_order, actor, view.id, 2, [a.id], None) == "not_found"
    for bad in (uuid.uuid4(), "not-a-uuid", None, 5, str(view.id).upper()):
        assert code_of(services.set_image_order, user(owner), bad, 2, [a.id], None) == ("not_found")


def test_ownership_is_decided_before_the_body_is_judged():
    _, view, _ = draft()
    other = make_account(random_email())
    assert code_of(services.set_image_order, user(other), view.id, "x", "y", 5) == "not_found"


@pytest.mark.parametrize("state", ["suspended", "deactivated"])
def test_an_account_that_may_not_edit_is_forbidden(state):
    account, view, (a, b) = draft(count=2)
    Account.objects.filter(id=account.id).update(
        **{
            "suspended": {"suspended_at": timezone.now(), "suspension_reason": "r"},
            "deactivated": {"deactivated_at": timezone.now()},
        }[state]
    )
    before = stored(view.id)
    assert code_of(services.set_image_order, user(account), view.id, 3, [b.id, a.id], None) == (
        "forbidden"
    )
    assert stored(view.id) == before


def test_an_unverified_owner_may_reorder():
    account = make_account(random_email(), state="unverified")
    view = services.create_draft(user(account), *HOUSE)
    a = services.upload_image(user(account), view.id, PNG)
    b = services.upload_image(user(account), view.id, PNG)
    assert reorder(account, view, [b.id, a.id]).media[0].id == b.id


def test_a_listing_that_cannot_be_edited_is_forbidden(monkeypatch):
    account, view, (a,) = draft(count=1)
    monkeypatch.setattr(lifecycle, "EDITABLE_STATUSES", frozenset())
    assert code_of(services.set_image_order, user(account), view.id, 2, [a.id], None) == (
        "forbidden"
    )


# --- failure and races -------------------------------------------------------------------------


def _boom(*args, **kwargs):
    raise RuntimeError("database failed")


@pytest.mark.django_db(transaction=True)
def test_a_failure_after_the_writes_rolls_back_order_cover_and_revision(monkeypatch):
    seed_cap()
    seed_media_policies()
    account, view, (a, b, c) = draft(count=3)
    before, current = revision(view.id), stored(view.id)
    monkeypatch.setattr(ListingRevision.objects, "create", _boom)
    with pytest.raises(RuntimeError):
        reorder(account, view, [c.id, b.id, a.id], cover=c.id)
    assert revision(view.id) == before and stored(view.id) == current


def run_parallel(count, work):
    barrier, outcomes = threading.Barrier(count), []

    def run(i):
        try:
            barrier.wait()
            work(i)
            outcomes.append("ok")
        except DomainError as exc:
            outcomes.append(exc.code)
        except services.StaleRevision:
            outcomes.append("stale_revision")
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return outcomes


@pytest.mark.django_db(transaction=True)
def test_two_reorders_with_the_same_base_admit_exactly_one():
    seed_cap()
    seed_media_policies()
    account, view, (a, b, c) = draft(count=3)
    base = revision(view.id)
    orders = [[c.id, b.id, a.id], [b.id, a.id, c.id]]
    outcomes = run_parallel(
        2, lambda i: services.set_image_order(user(account), view.id, base, orders[i], None)
    )
    assert sorted(outcomes) == ["ok", "stale_revision"]
    assert revision(view.id) == base + 1
    assert ListingRevision.objects.filter(listing_id=view.id, revision=base + 1).count() == 1
    final = [image_id for image_id, _, _ in stored(view.id)]
    assert final in orders  # one whole order, never a mix
    assert [p for _, p, _ in stored(view.id)] == [0, 1, 2]
    assert sum(1 for _, _, cover in stored(view.id) if cover) == 1


@pytest.mark.django_db(transaction=True)
def test_a_reorder_racing_a_removal_leaves_a_consistent_listing():
    seed_cap()
    seed_media_policies()
    account, view, (a, b, c) = draft(count=3)
    base = revision(view.id)

    def work(i):
        if i == 0:
            services.set_image_order(user(account), view.id, base, [c.id, b.id, a.id], c.id)
        else:
            services.remove_image(user(account), view.id, a.id)

    outcomes = run_parallel(2, work)
    assert set(outcomes) <= {"ok", "stale_revision", "invalid"}
    rows = stored(view.id)
    assert [p for _, p, _ in rows] == list(range(len(rows)))
    assert sum(1 for _, _, cover in rows if cover) == 1


# --- constraints and migration -----------------------------------------------------------------


def test_the_database_allows_at_most_one_cover_per_listing():
    account, view, (a, b) = draft(count=2)
    with pytest.raises(IntegrityError), transaction.atomic():
        ListingImage.objects.filter(pk=b.id).update(is_cover=True)  # a is still the cover
    other_account, other_view, _ = draft(count=1)  # another Listing has its own cover
    assert sum(1 for _, _, cover in stored(other_view.id) if cover) == 1
    assert sum(1 for _, _, cover in stored(view.id) if cover) == 1


def test_a_listing_may_have_images_and_no_cover_at_the_database_level():
    account, view, (a, b) = draft(count=2)
    ListingImage.objects.filter(pk=a.id).update(is_cover=False)
    assert [cover for _, _, cover in stored(view.id)] == [False, False]


def test_positions_are_unique_per_listing_but_the_check_waits_for_the_commit():
    account, view, (a, b) = draft(count=2)
    with pytest.raises(IntegrityError), transaction.atomic():
        ListingImage.objects.filter(pk=b.id).update(position=0)
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    with transaction.atomic():  # a swap is fine: the check is at the end
        ListingImage.objects.filter(pk=a.id).update(position=1)
        ListingImage.objects.filter(pk=b.id).update(position=0)
    assert [(i, p) for i, p, _ in stored(view.id)] == [(b.id, 0), (a.id, 1)]


def test_a_negative_position_is_refused():
    account, view, (a,) = draft(count=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        ListingImage.objects.create(listing_id=view.id, asset_id=uuid.uuid4(), position=-1)


def test_the_migrated_constraints_match_the_model():
    from django.db.migrations.loader import MigrationLoader

    state = MigrationLoader(connection).project_state()
    options = state.models[("listings", "listingimage")].options
    migrated = {c.name: c.deconstruct()[2] for c in options["constraints"]}
    current = {c.name: c.deconstruct()[2] for c in ListingImage._meta.constraints}
    assert migrated == current
    assert set(current) == {
        "listings_image_position_unique",
        "listings_image_one_cover",
        "listings_image_position_gte_0",
    }
    assert current["listings_image_position_unique"]["deferrable"] is not None
    assert "condition" in current["listings_image_one_cover"]


@pytest.mark.django_db(transaction=True)
def test_the_cover_migration_renumbers_and_marks_the_first_image_then_reverses():
    executor = MigrationExecutor(connections["default"])
    try:
        executor.migrate([("listings", "0007_images")])
        old = MigrationExecutor(connections["default"]).loader.project_state(
            [("listings", "0007_images")]
        )
        Listing, Image = (
            old.apps.get_model("listings", n) for n in ("PropertyListing", "ListingImage")
        )
        one = Listing.objects.create(
            owner_account_id=uuid.uuid4(),
            status="draft",
            revision=1,
            registry_version=1,
            purpose="sale",
            category="house",
            subtype="house",
        )
        two = Listing.objects.create(
            owner_account_id=uuid.uuid4(),
            status="draft",
            revision=1,
            registry_version=1,
            purpose="sale",
            category="house",
            subtype="house",
        )
        made = {}
        for listing, positions in ((one, [1, 3, 4]), (two, [2])):  # 2.9 left gaps
            for position in positions:
                made[(listing.id, position)] = Image.objects.create(
                    listing_id=listing.id, asset_id=uuid.uuid4(), position=position
                ).id
        executor = MigrationExecutor(connections["default"])
        executor.migrate([("listings", "0008_cover")])
        rows = {(r.listing_id, r.id): (r.position, r.is_cover) for r in ListingImage.objects.all()}
        assert rows == {
            (one.id, made[(one.id, 1)]): (0, True),
            (one.id, made[(one.id, 3)]): (1, False),
            (one.id, made[(one.id, 4)]): (2, False),
            (two.id, made[(two.id, 2)]): (0, True),
        }
        executor = MigrationExecutor(connections["default"])
        executor.migrate([("listings", "0007_images")])
        with connection.cursor() as cursor:
            cursor.execute("SELECT listing_id, position FROM listings_listingimage ORDER BY 1, 2")
            back = cursor.fetchall()
        assert sorted(p for lid, p in back if lid == one.id) == [1, 2, 3]
        assert [p for lid, p in back if lid == two.id] == [1]
    finally:
        MigrationExecutor(connections["default"]).migrate(
            MigrationExecutor(connections["default"]).loader.graph.leaf_nodes()
        )
        PropertyListing.objects.all().delete()


# --- ghosts (a link whose asset row is gone), contiguity and cover repair -----------------------


def ghost(image):
    """Lose the asset row of an image; the link stays."""
    MediaAsset.objects.filter(pk=ListingImage.objects.get(pk=image.id).asset_id).delete()


def test_a_ghost_is_omitted_from_the_view_and_does_not_crash_reads():
    account, view, (a, b) = draft(count=2)
    ghost(b)
    assert [m.id for m in selectors.get_owned_listing(user(account), view.id).media] == [a.id]
    assert [m.id for m in selectors.list_owned(user(account)).items[0].media] == [a.id]


def test_a_reorder_removes_the_ghost_renumbers_and_rides_on_its_revision():
    account, view, (a, b, c) = draft(count=3)
    ghost(b)
    before = revision(view.id)
    result = reorder(account, view, [c.id, a.id])
    assert stored(view.id) == [(c.id, 0, False), (a.id, 1, True)]
    assert [m.id for m in result.media] == [c.id, a.id]
    assert revision(view.id) == before + 1
    changes = ListingRevision.objects.get(listing_id=view.id, revision=before + 1).changes
    assert changes == {"images": 2, "order": True, "cover": False, "repaired": True}


def test_a_ghost_cover_is_repaired_even_when_the_request_changes_nothing_else():
    account, view, (a, b) = draft(count=2)
    ghost(a)  # the cover is lost
    before, rows = revision(view.id), ListingRevision.objects.count()
    result = reorder(account, view, [b.id])
    assert stored(view.id) == [(b.id, 0, True)]
    assert result.revision == before + 1 and ListingRevision.objects.count() == rows + 1
    assert ListingRevision.objects.get(listing_id=view.id, revision=before + 1).changes == {
        "images": 1,
        "order": False,
        "cover": False,  # the repair itself promoted b; the request asked for no cover change
        "repaired": True,
    }
    # now consistent: the same request is a plain no-op
    assert reorder(account, view, [b.id]).revision == before + 1


def test_a_ghost_in_the_order_is_an_unknown_id():
    account, view, (a, b) = draft(count=2)
    ghost(b)
    assert invalid_fields(account, view, [a.id, b.id]) == {"order": "invalid"}
    assert stored(view.id)[1][0] == b.id  # the failed request repaired nothing (rolled back)


def test_an_upload_is_not_blocked_by_ghosts_at_the_cap():
    account, view, made = draft(count=MAX_IMAGES)
    ghost(made[0])
    ghost(made[1])
    image = upload_one(account, view)
    assert [(p, c) for _, p, c in stored(view.id)] == [(i, i == 0) for i in range(MAX_IMAGES - 1)]
    assert image.position == MAX_IMAGES - 2 and not image.is_cover


def upload_one(account, view):
    return services.upload_image(user(account), view.id, PNG)


def test_the_cap_still_holds_with_live_images_only():
    account, view, made = draft(count=MAX_IMAGES)
    ghost(made[0])
    upload_one(account, view)  # nine live plus one new
    assert code_of(upload_one, account, view) == "too_many_images"


def test_removing_an_image_repairs_ghosts_and_the_cover():
    account, view, (a, b, c) = draft(count=3)
    ghost(b)
    services.remove_image(user(account), view.id, a.id)  # the cover, with a ghost behind it
    assert stored(view.id) == [(c.id, 0, True)]


def test_a_ghost_itself_can_be_removed():
    account, view, (a, b) = draft(count=2)
    ghost(b)
    services.remove_image(user(account), view.id, b.id)
    assert stored(view.id) == [(a.id, 0, True)]


def test_removal_makes_position_zero_the_cover_when_none_is_left():
    account, view, (a, b, c) = draft(count=3)
    ListingImage.objects.filter(listing_id=view.id).update(is_cover=False)  # corrupt: no cover
    services.remove_image(user(account), view.id, c.id)
    assert stored(view.id) == [(a.id, 0, True), (b.id, 1, False)]


def test_upload_uses_the_highest_position_and_repairs_non_contiguous_ones():
    account, view, (a, b) = draft(count=2)
    ListingImage.objects.filter(pk=b.id).update(position=5)  # a gap
    image = upload_one(account, view)
    assert image.position == 2  # repaired to 0, 1 first, then max + 1
    assert stored(view.id) == [(a.id, 0, True), (b.id, 1, False), (image.id, 2, False)]


def test_a_stale_base_wins_over_the_status_on_a_listing_that_cannot_be_edited(monkeypatch):
    account, view, (a,) = draft(count=1)
    monkeypatch.setattr(lifecycle, "EDITABLE_STATUSES", frozenset())
    with pytest.raises(services.StaleRevision):
        services.set_image_order(user(account), view.id, 1, [a.id], None)  # stale: the stored is 2
    assert code_of(services.set_image_order, user(account), view.id, 2, [a.id], None) == (
        "forbidden"
    )


def test_an_order_longer_than_the_limit_is_refused_before_anything_else():
    account, view, _ = draft(count=1)
    too_long = [uuid.uuid4() for _ in range(MAX_IMAGES + 1)]
    with pytest.raises(services.FieldsInvalid) as caught:
        services.set_image_order(user(account), view.id, 1, too_long, None)
    assert caught.value.fields == {"order": "invalid"}  # not even a stale base is looked at
