"""`listings.services.upload_image` and `remove_image` (Story 2.9)."""

import io
import threading
import uuid

import pytest
from django.db import connection
from django.utils import timezone
from PIL import Image

from apps.identity.models import Account
from apps.kernel import images, media, runtime, storage
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import MediaAsset, RateLimitPolicy, StorageUsage
from apps.listings import lifecycle, services
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

JPEG = make_image("JPEG", (1200, 800))


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


def draft(account=None, kind="individual"):
    account = account or make_account(random_email(), kind)
    return account, services.create_draft(user(account), *HOUSE)


def upload(account, view, data=JPEG):
    return services.upload_image(user(account), view.id, data)


def code_of(call, *args):
    with pytest.raises(DomainError) as caught:
        call(*args)
    return caught.value.code


def stored_files(media_root):
    # the keys as stored: the path below the root for filed keys, the file name otherwise
    found = (p.relative_to(media_root).as_posix() for p in media_root.rglob("*") if p.is_file())
    return sorted(k if k.startswith("users/") else k.rsplit("/", 1)[-1] for k in found)


def revision(view):
    return PropertyListing.objects.get(id=view.id).revision


# --- success -----------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["individual", "agency"])
def test_the_owner_uploads_an_image(media_root, kind):
    account, view = draft(kind=kind)
    image = upload(account, view)
    assert (image.position, image.content_type, image.width, image.height) == (
        0,
        "image/jpeg",
        1200,
        800,
    )
    asset = MediaAsset.objects.get()
    assert image.bytes == asset.bytes == StorageUsage.objects.get().bytes_used
    assert asset.attached_at is not None and asset.owner_account_id == account.id
    link = ListingImage.objects.get()
    assert (link.id, link.asset_id, link.listing_id, link.position) == (
        image.id,
        asset.id,
        view.id,
        0,
    )
    assert stored_files(media_root) == [asset.storage_key]
    stored = storage.get_storage().get(asset.storage_key)
    assert stored != JPEG and Image.open(io.BytesIO(stored)).size == (1200, 800)
    assert image.is_cover is True
    assert image.url == f"/api/v1/listings/{view.id}/images/{image.id}/file"
    assert {"storage_key", "path", "asset_id", "owner_account_id"}.isdisjoint(vars(image))


def test_the_listing_revision_rises_by_one_and_records_only_the_count():
    account, view = draft()
    assert revision(view) == 1
    upload(account, view)
    upload(account, view)
    assert revision(view) == 3
    rows = list(ListingRevision.objects.filter(listing_id=view.id).order_by("revision"))
    assert [(r.revision, r.origin, r.actor_account_id) for r in rows[1:]] == [
        (2, "manual", account.id),
        (3, "manual", account.id),
    ]
    assert [r.changes for r in rows[1:]] == [{"images": 1}, {"images": 2}]


def test_positions_stay_contiguous_after_a_removal():
    account, view = draft()
    first, second, third = (upload(account, view) for _ in range(3))
    assert [first.position, second.position, third.position] == [0, 1, 2]
    services.remove_image(user(account), view.id, second.id)
    assert list(ListingImage.objects.order_by("position").values_list("id", "position")) == [
        (first.id, 0),
        (third.id, 1),
    ]
    assert upload(account, view).position == 2


def test_an_unverified_owner_may_upload():
    account = make_account(random_email(), state="unverified")
    view = services.create_draft(user(account), *HOUSE)
    assert upload(account, view).position == 0


def test_an_edit_after_an_upload_needs_the_new_revision():
    account, view = draft()
    upload(account, view)
    with pytest.raises(services.StaleRevision):
        services.apply_updates(user(account), view.id, 1, {"title": "A fine family house"})
    services.apply_updates(user(account), view.id, 2, {"title": "A fine family house"})


# --- refusals ----------------------------------------------------------------------------------


def test_a_visitor_is_unauthenticated():
    _, view = draft()
    assert code_of(services.upload_image, Actor.visitor("r"), view.id, JPEG) == ("unauthenticated")
    assert code_of(services.remove_image, Actor.visitor("r"), view.id, uuid.uuid4()) == (
        "unauthenticated"
    )


def test_everyone_but_the_owner_gets_not_found_and_nothing_is_stored(media_root):
    owner, view = draft()
    other = make_account(random_email())
    admin = make_account(random_email(), "individual")
    Account.objects.filter(id=admin.id).update(account_type="admin")
    for actor in (user(other), Actor.admin(admin.id, "r"), SYSTEM, Actor.user(uuid.uuid4(), "r")):
        assert code_of(services.upload_image, actor, view.id, JPEG) == "not_found"
    for bad in (uuid.uuid4(), "not-a-uuid", None, 12, str(view.id).upper()):
        assert code_of(services.upload_image, user(owner), bad, JPEG) == "not_found"
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []
    assert revision(view) == 1


def test_ownership_is_decided_before_the_image_is_looked_at():
    _, view = draft()
    other = make_account(random_email())
    assert code_of(services.upload_image, user(other), view.id, b"garbage") == "not_found"


@pytest.mark.parametrize("state", ["suspended", "deactivated"])
def test_an_account_that_may_not_edit_is_forbidden(state, media_root):
    account, view = draft()
    Account.objects.filter(id=account.id).update(
        **{
            "suspended": {"suspended_at": timezone.now(), "suspension_reason": "r"},
            "deactivated": {"deactivated_at": timezone.now()},
        }[state]
    )
    assert code_of(upload, account, view) == "forbidden"
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []


def test_a_listing_that_cannot_be_edited_is_forbidden(monkeypatch, media_root):
    account, view = draft()
    monkeypatch.setattr(lifecycle, "EDITABLE_STATUSES", frozenset())
    assert code_of(upload, account, view) == "forbidden"
    assert code_of(services.remove_image, user(account), view.id, uuid.uuid4()) == ("forbidden")
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []


def test_the_eleventh_image_is_too_many_and_stores_nothing(media_root):
    account, view = draft()
    for _ in range(MAX_IMAGES):
        upload(account, view)
    before = (revision(view), StorageUsage.objects.get().bytes_used, stored_files(media_root))
    assert code_of(upload, account, view) == "too_many_images"
    assert (
        revision(view),
        StorageUsage.objects.get().bytes_used,
        stored_files(media_root),
    ) == before
    assert MediaAsset.objects.count() == MAX_IMAGES


def test_removing_one_makes_room_again():
    account, view = draft()
    made = [upload(account, view) for _ in range(MAX_IMAGES)]
    services.remove_image(user(account), view.id, made[0].id)
    assert upload(account, view).position == MAX_IMAGES - 1


def test_the_storage_cap_is_enforced_per_account_and_refuses_with_nothing_stored(media_root):
    account, view = draft()
    first = upload(account, view)
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(
        limit=first.bytes + 1
    )
    before = (revision(view), stored_files(media_root))
    assert code_of(upload, account, view) == "storage_cap_exceeded"
    assert (revision(view), stored_files(media_root)) == before
    assert ListingImage.objects.count() == 1
    # an Agency has its own, larger cap
    agency, agency_view = draft(kind="agency")
    upload(agency, agency_view)
    upload(agency, agency_view)


@pytest.mark.parametrize(
    ("data", "reason"),
    [(b"garbage", "invalid_image"), (make_image("PNG", (100, 100)), "too_small")],
)
def test_a_bad_image_is_a_rejection_with_a_reason_and_stores_nothing(data, reason, media_root):
    account, view = draft()
    with pytest.raises(images.ImageRejected) as caught:
        upload(account, view, data)
    assert caught.value.reason == reason
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []
    assert revision(view) == 1


# --- failure after the file write --------------------------------------------------------------


def test_a_failure_after_the_file_was_written_removes_it_and_changes_nothing(
    media_root, monkeypatch
):
    account, view = draft()
    written = []
    real_put = storage.FilesystemStorage.put

    def spy(self, key, data):
        written.append(key)
        real_put(self, key, data)

    monkeypatch.setattr(storage.FilesystemStorage, "put", spy)
    monkeypatch.setattr(ListingRevision.objects, "create", _boom)
    with pytest.raises(RuntimeError):
        upload(account, view)
    assert len(written) == 1  # the file was written before the failure
    assert stored_files(media_root) == []
    assert not MediaAsset.objects.exists() and not ListingImage.objects.exists()
    assert not StorageUsage.objects.exists()  # the whole transaction, usage row included, is gone
    assert revision(view) == 1


def _boom(*args, **kwargs):
    raise RuntimeError("database failed")


def test_a_failure_while_attaching_removes_the_file_too(media_root, monkeypatch):
    account, view = draft()
    monkeypatch.setattr(ListingImage.objects, "create", _boom)
    with pytest.raises(RuntimeError):
        upload(account, view)
    assert stored_files(media_root) == [] and not MediaAsset.objects.exists()


# --- remove ------------------------------------------------------------------------------------


def test_remove_deletes_the_link_the_asset_and_the_file_and_gives_the_bytes_back(
    media_root, django_capture_on_commit_callbacks
):
    account, view = draft()
    keep, gone = upload(account, view), upload(account, view)
    key = MediaAsset.objects.get(pk=ListingImage.objects.get(pk=gone.id).asset_id).storage_key
    before = revision(view)
    with django_capture_on_commit_callbacks(execute=True):
        assert services.remove_image(user(account), view.id, gone.id) is None
    assert list(ListingImage.objects.values_list("id", flat=True)) == [keep.id]
    assert MediaAsset.objects.count() == 1
    assert StorageUsage.objects.get().bytes_used == keep.bytes  # given back exactly once
    assert not storage.get_storage().exists(key) and len(stored_files(media_root)) == 1
    assert revision(view) == before + 1
    assert ListingRevision.objects.get(listing_id=view.id, revision=before + 1).changes == {
        "images": 1
    }


def test_a_repeat_remove_is_not_found_and_does_not_double_count():
    account, view = draft()
    keep, gone = upload(account, view), upload(account, view)
    services.remove_image(user(account), view.id, gone.id)
    used = StorageUsage.objects.get().bytes_used
    assert code_of(services.remove_image, user(account), view.id, gone.id) == "not_found"
    assert StorageUsage.objects.get().bytes_used == used == keep.bytes


def test_remove_still_works_when_the_asset_row_is_already_gone(media_root):
    account, view = draft()
    keep, lost = upload(account, view), upload(account, view)
    asset_id = ListingImage.objects.get(pk=lost.id).asset_id
    MediaAsset.objects.filter(pk=asset_id).delete()  # a lost row
    used = StorageUsage.objects.get().bytes_used
    services.remove_image(user(account), view.id, lost.id)
    assert list(ListingImage.objects.values_list("id", flat=True)) == [keep.id]
    assert StorageUsage.objects.get().bytes_used == used  # unknown bytes: never guessed
    assert revision(view) == 4


def test_remove_refuses_foreign_and_malformed_ids_alike():
    owner, view = draft()
    image = upload(owner, view)
    other_owner, other_view = draft()
    other_image = upload(other_owner, other_view)
    other = make_account(random_email())
    assert code_of(services.remove_image, user(other), view.id, image.id) == "not_found"
    for listing, image_id in (
        (view.id, other_image.id),  # another Listing's image through my Listing
        (view.id, uuid.uuid4()),
        (view.id, "x"),
        (other_view.id, image.id),
    ):
        assert code_of(services.remove_image, user(owner), listing, image_id) == "not_found"
    assert ListingImage.objects.count() == 2


@pytest.mark.django_db(transaction=True)
def test_a_remove_that_fails_later_keeps_the_image_the_file_and_the_usage(media_root, monkeypatch):
    seed_cap()
    seed_media_policies()
    account, view = draft()
    image = upload(account, view)
    monkeypatch.setattr(ListingRevision.objects, "create", _boom)
    with pytest.raises(RuntimeError):
        services.remove_image(user(account), view.id, image.id)
    assert ListingImage.objects.filter(pk=image.id).exists()
    assert MediaAsset.objects.count() == 1 and len(stored_files(media_root)) == 1
    assert StorageUsage.objects.get().bytes_used == image.bytes
    assert revision(view) == 2


# --- races -------------------------------------------------------------------------------------


def run_parallel(count, work):
    barrier, outcomes = threading.Barrier(count), []

    def run(i):
        try:
            barrier.wait()
            work(i)
            outcomes.append("ok")
        except DomainError as exc:
            outcomes.append(exc.code)
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
def test_two_parallel_uploads_at_nine_images_admit_exactly_one(media_root):
    seed_cap()
    seed_media_policies()
    account, view = draft()
    for _ in range(MAX_IMAGES - 1):
        upload(account, view)
    outcomes = run_parallel(2, lambda i: upload(account, view))
    assert sorted(outcomes) == ["ok", "too_many_images"]
    assert ListingImage.objects.filter(listing_id=view.id).count() == MAX_IMAGES
    assert MediaAsset.objects.count() == MAX_IMAGES
    assert len(stored_files(media_root)) == MAX_IMAGES
    assert StorageUsage.objects.get().bytes_used == sum(
        MediaAsset.objects.values_list("bytes", flat=True)
    )
    positions = ListingImage.objects.values_list("position", flat=True)
    assert len(set(positions)) == MAX_IMAGES


@pytest.mark.django_db(transaction=True)
def test_parallel_uploads_from_one_account_never_pass_the_cap(media_root):
    seed_cap()
    seed_media_policies()
    account, view = draft()
    other_view = services.create_draft(user(account), *HOUSE)
    one = upload(account, view)
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(
        limit=3 * one.bytes + 50
    )
    targets = [view, other_view]
    outcomes = run_parallel(3, lambda i: upload(account, targets[i % 2]))
    # one image is stored already and two more fit: exactly one of the three is refused
    assert sorted(outcomes) == ["ok", "ok", "storage_cap_exceeded"]
    used = StorageUsage.objects.get().bytes_used
    assert used == sum(MediaAsset.objects.values_list("bytes", flat=True))
    assert used <= media.cap_bytes("individual")
    assert MediaAsset.objects.count() == 3 and len(stored_files(media_root)) == 3


@pytest.mark.django_db(transaction=True)
def test_an_upload_racing_an_edit_makes_the_editor_reload():
    seed_cap()
    seed_media_policies()
    account, view = draft()
    outcomes = run_parallel(
        2,
        lambda i: (
            upload(account, view)
            if i == 0
            else services.apply_updates(user(account), view.id, 1, {"title": "A fine family house"})
        ),
    )
    # both based on revision 1: the upload needs no base, the edit is stale if it comes second
    assert outcomes.count("ok") >= 1
    assert revision(view) == 1 + outcomes.count("ok")
    assert set(outcomes) <= {"ok", "stale_revision"}, outcomes
