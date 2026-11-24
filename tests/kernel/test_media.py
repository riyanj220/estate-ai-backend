"""`kernel.media`: stored images, the atomic storage cap, deletion and orphan retention."""

import io
import os
import threading
import time
import uuid
from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.utils import timezone
from PIL import Image

from apps.kernel import images, media, retention, runtime, storage
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import MediaAsset, RateLimitPolicy, StorageUsage
from tests.imaging import gradient, make_image
from tests.listings.support import make_account, random_email, seed_media_policies, user

MB = 1024 * 1024


@pytest.fixture(autouse=True)
def policies(db):
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


def owner(kind="individual"):
    return make_account(random_email(), kind)


def picture(size=(640, 480)) -> images.ProcessedImage:
    out = io.BytesIO()
    gradient(size).save(out, "JPEG")
    return images.process_image(out.getvalue())


def store(account, processed=None, kind="individual", **kw):
    return media.store_processed(user(account), processed or picture(), account_type=kind, **kw)


def usage(account) -> int:
    row = StorageUsage.objects.filter(pk=account.id).first()
    return 0 if row is None else row.bytes_used


def files(media_root):
    return sorted(p.name for p in media_root.rglob("*") if p.is_file())


# --- the seeded policy -------------------------------------------------------------------------


def test_the_migration_seeds_the_caps_and_the_upload_limit():
    import importlib

    module = importlib.import_module("apps.kernel.migrations.0012_seed_media_policies")
    rows = {scope: (limit, window, kind) for scope, limit, window, kind, _ in module.POLICIES}
    assert rows == {
        "storage_cap_bytes_individual": (209_715_200, None, "account"),
        "storage_cap_bytes_agency": (1_073_741_824, None, "account"),
        "image_upload": (30, 3600, "account"),
    }
    assert media.cap_bytes("individual") == 209_715_200
    assert media.cap_bytes("agency") == 1_073_741_824


def test_an_unknown_account_type_or_a_windowed_policy_is_a_configuration_error():
    from django.core.exceptions import ImproperlyConfigured

    with pytest.raises(ImproperlyConfigured):
        media.cap_bytes("admin")
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_agency").update(window_seconds=60)
    with pytest.raises(ImproperlyConfigured):
        media.cap_bytes("agency")


# --- who may store -----------------------------------------------------------------------------


def test_only_a_signed_in_user_account_may_store(media_root):
    data = make_image("JPEG", (400, 300))
    account = owner()
    for actor, code in [
        (Actor.visitor("r"), "unauthenticated"),
        (Actor.admin(account.id, "r"), "forbidden"),
        (Actor.system("r"), "forbidden"),
    ]:
        with pytest.raises(DomainError) as caught:
            media.store_image(actor, data, account_type="individual")
        assert caught.value.code == code
    assert not MediaAsset.objects.exists() and files(media_root) == []


# --- store -------------------------------------------------------------------------------------


def test_store_image_writes_the_file_a_row_and_the_usage(media_root):
    account = owner()
    stored = media.store_image(
        user(account), make_image("PNG", (1200, 800)), account_type="individual"
    )
    row = MediaAsset.objects.get()
    assert row.id == stored.id and row.owner_account_id == account.id
    assert storage.is_valid_key(row.storage_key)
    data = storage.get_storage().get(row.storage_key)
    assert len(data) == row.bytes == stored.bytes == usage(account)
    assert (row.content_type, row.width, row.height) == ("image/png", 1200, 800)
    assert Image.open(io.BytesIO(data)).size == (1200, 800)
    assert row.attached_at is None and (timezone.now() - row.created_at) < timedelta(minutes=1)
    assert files(media_root) == [row.storage_key]


def test_attach_sets_attached_at_in_the_same_row():
    stored = store(owner(), attach=True)
    assert stored.attached_at is not None
    assert MediaAsset.objects.get().attached_at == stored.attached_at


def test_keys_are_random_and_unrelated_to_anything_stored():
    account = owner()
    a, b = store(account), store(account)
    assert a.storage_key != b.storage_key
    assert str(account.id) not in a.storage_key and str(a.id) not in a.storage_key


def test_an_invalid_image_stores_nothing(media_root):
    with pytest.raises(images.ImageRejected):
        media.store_image(user(owner()), b"not an image", account_type="individual")
    assert not MediaAsset.objects.exists() and not StorageUsage.objects.exists()
    assert files(media_root) == []


# --- the cap -----------------------------------------------------------------------------------


def test_an_upload_over_the_cap_is_refused_and_changes_nothing(media_root):
    account = owner()
    first = store(account)
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(
        limit=first.bytes + 10
    )
    with pytest.raises(DomainError) as caught:
        store(account)
    assert caught.value.code == "storage_cap_exceeded"
    assert usage(account) == first.bytes and MediaAsset.objects.count() == 1
    assert files(media_root) == [first.storage_key]


def test_the_cap_counts_the_stored_size_and_may_be_filled_exactly():
    account = owner()
    processed = picture()
    size = len(processed.data)
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(limit=2 * size)
    store(account, processed)
    store(account, processed)  # exactly at the cap is allowed
    assert usage(account) == 2 * size
    with pytest.raises(DomainError):
        store(account, processed)


def test_caps_depend_on_the_account_type_and_are_per_account():
    individual, agency, other = owner(), owner("agency"), owner()
    size = len(picture().data)
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(limit=size)
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_agency").update(limit=3 * size)
    store(individual)
    with pytest.raises(DomainError):
        store(individual)
    for _ in range(3):
        store(agency, kind="agency")
    with pytest.raises(DomainError):
        store(agency, kind="agency")
    store(other)  # another account's usage is its own
    assert usage(other) == size


def test_a_failure_after_the_file_write_removes_the_file_and_keeps_the_usage(
    media_root, monkeypatch
):
    account = owner()
    first = store(account)
    written = []
    real_put = storage.FilesystemStorage.put

    def spy(self, key, data):
        written.append(key)
        real_put(self, key, data)

    monkeypatch.setattr(storage.FilesystemStorage, "put", spy)

    def boom(*args, **kwargs):
        raise RuntimeError("database failed")

    monkeypatch.setattr(MediaAsset.objects, "create", boom)
    with pytest.raises(RuntimeError):
        store(account)
    assert len(written) == 1  # the file really was written first
    assert files(media_root) == [first.storage_key]  # and removed again
    assert usage(account) == first.bytes and MediaAsset.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_a_rolled_back_outer_transaction_leaves_no_row_and_the_caller_discards_the_file(
    media_root,
):
    seed_media_policies()
    account = owner()
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            stored = store(account)
            assert files(media_root) == [stored.storage_key]  # written before any commit
            raise RuntimeError("later step failed")
    # the row and usage rolled back with the transaction; the file is the caller's to remove
    assert not MediaAsset.objects.exists() and usage(account) == 0
    media.discard_file(stored.storage_key)
    assert files(media_root) == []


@pytest.mark.django_db(transaction=True)
def test_parallel_uploads_never_exceed_the_cap():
    seed_media_policies()
    account = owner()
    processed = picture()
    size = len(processed.data)
    fit = 3
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(
        limit=fit * size + size // 2
    )
    barrier, outcomes = threading.Barrier(8), []

    def work():
        try:
            barrier.wait()
            media.store_processed(user(account), processed, account_type="individual")
            outcomes.append("stored")
        except DomainError as exc:
            outcomes.append(exc.code)
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)
        finally:
            connection.close()

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == sorted(["stored"] * fit + ["storage_cap_exceeded"] * (8 - fit))
    assert MediaAsset.objects.count() == fit
    assert usage(account) == fit * size <= media.cap_bytes("individual")
    assert len(files_in_store()) == fit


def files_in_store():
    from pathlib import Path

    from django.conf import settings

    return [p for p in Path(settings.MEDIA_ROOT).rglob("*") if p.is_file()]


# --- delete ------------------------------------------------------------------------------------


def test_delete_removes_the_row_gives_the_bytes_back_and_deletes_the_file_after_commit(
    media_root, django_capture_on_commit_callbacks
):
    account = owner()
    keep, gone = store(account), store(account)
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        media.delete_asset(user(account), gone.id)
        assert not MediaAsset.objects.filter(pk=gone.id).exists()
        assert usage(account) == keep.bytes
        assert storage.get_storage().exists(gone.storage_key)  # not before the commit
    assert len(callbacks) == 1
    callbacks[0]()
    assert not storage.get_storage().exists(gone.storage_key)
    assert files(media_root) == [keep.storage_key]


def test_delete_never_takes_usage_below_zero():
    account = owner()
    stored = store(account)
    StorageUsage.objects.filter(pk=account.id).update(bytes_used=1)
    media.delete_asset(user(account), stored.id)
    assert usage(account) == 0


def test_a_foreign_or_unknown_asset_is_not_found_and_nothing_changes():
    mine, theirs = owner(), owner()
    stored = store(theirs)
    for asset_id in (stored.id, uuid.uuid4()):
        with pytest.raises(DomainError) as caught:
            media.delete_asset(user(mine), asset_id)
        assert caught.value.code == "not_found"
    assert MediaAsset.objects.count() == 1 and usage(theirs) == stored.bytes


@pytest.mark.django_db(transaction=True)
def test_a_rolled_back_delete_keeps_the_file_the_row_and_the_usage(media_root):
    seed_media_policies()
    account = owner()
    stored = store(account)
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            media.delete_asset(user(account), stored.id)
            raise RuntimeError("later step failed")
    assert MediaAsset.objects.filter(pk=stored.id).exists()
    assert usage(account) == stored.bytes
    assert files(media_root) == [stored.storage_key]


@pytest.mark.django_db(transaction=True)
def test_a_committed_delete_removes_the_file(media_root):
    seed_media_policies()
    account = owner()
    stored = store(account)
    media.delete_asset(user(account), stored.id)  # autocommit: after commit runs at once
    assert files(media_root) == [] and usage(account) == 0


def test_delete_with_missing_ok_tolerates_a_missing_row_and_leaves_usage_alone():
    account = owner()
    stored = store(account)
    media.delete_asset(user(account), uuid.uuid4(), missing_ok=True)
    media.delete_asset(user(account), stored.id, missing_ok=True)
    media.delete_asset(user(account), stored.id, missing_ok=True)  # already gone
    assert usage(account) == 0 and not MediaAsset.objects.exists()


# --- retention ---------------------------------------------------------------------------------


@pytest.fixture
def purge(django_capture_on_commit_callbacks):
    """Run the hook the way `run_retention` does: the files go after the commit."""

    def run(dry_run=False):
        with django_capture_on_commit_callbacks(execute=True):
            return media.purge_orphans(timezone.now(), dry_run)

    return run


def age(asset, hours, media_root, file=True):
    MediaAsset.objects.filter(pk=asset.id).update(
        created_at=timezone.now() - timedelta(hours=hours)
    )
    if file:
        path = media_root / asset.storage_key[:2] / asset.storage_key[2:4] / asset.storage_key
        past = time.time() - hours * 3600
        os.utime(path, (past, past))


def test_the_hook_is_registered_with_the_retention_mechanism():
    assert "kernel.media_orphans" in retention.registered()


def test_unattached_assets_older_than_24_hours_are_removed_and_usage_given_back(
    media_root, django_capture_on_commit_callbacks
):
    account = owner()
    old, young, attached = store(account), store(account), store(account, attach=True)
    age(old, 25, media_root)
    age(young, 23, media_root)
    age(attached, 100, media_root)
    before = usage(account)
    assert media.purge_orphans(timezone.now(), dry_run=True) == 1
    assert MediaAsset.objects.count() == 3 and len(files(media_root)) == 3
    with django_capture_on_commit_callbacks(execute=True):
        results = retention.run_all(timezone.now())
    assert {r.name: r.count for r in results}["kernel.media_orphans"] == 1
    assert set(MediaAsset.objects.values_list("id", flat=True)) == {young.id, attached.id}
    assert usage(account) == before - old.bytes
    assert files(media_root) == sorted([young.storage_key, attached.storage_key])


def test_nothing_is_deleted_before_the_commit(media_root, django_capture_on_commit_callbacks):
    account = owner()
    old = store(account)
    age(old, 25, media_root)
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        assert media.purge_orphans(timezone.now()) == 1
        assert storage.get_storage().exists(old.storage_key)
    assert len(callbacks) == 1


def test_a_second_run_is_idempotent(media_root, purge):
    account = owner()
    old = store(account)
    age(old, 30, media_root)
    assert purge() == 1
    assert purge() == 0
    assert usage(account) == 0 and files(media_root) == []


def test_files_with_no_row_are_removed_only_when_old(media_root, purge):
    kept = store(owner())
    old_key, young_key = storage.new_key(), storage.new_key()
    port = storage.get_storage()
    port.put(old_key, b"stray")
    port.put(young_key, b"stray")
    past = time.time() - 30 * 3600
    for key in (old_key, kept.storage_key):
        os.utime(media_root / key[:2] / key[2:4] / key, (past, past))
    assert purge(dry_run=True) == 1
    assert files(media_root) == sorted([old_key, young_key, kept.storage_key])
    assert purge() == 1
    assert files(media_root) == sorted([young_key, kept.storage_key])  # kept has a row


def test_an_old_file_of_a_removed_orphan_is_counted_once(media_root, purge):
    old = store(owner())
    age(old, 48, media_root)
    assert purge() == 1
    assert files(media_root) == []


def test_leftover_temp_files_older_than_24_hours_are_swept(media_root, purge):
    stored = store(owner())
    directory = media_root / stored.storage_key[:2] / stored.storage_key[2:4]
    leftover = directory / ".tmp-crashed"
    leftover.write_bytes(b"half")
    past = time.time() - 30 * 3600
    os.utime(leftover, (past, past))
    purge()
    assert not leftover.exists() and storage.get_storage().exists(stored.storage_key)


def test_a_failing_file_delete_does_not_stop_the_rest(media_root, monkeypatch, purge):
    account = owner()
    first, second = store(account), store(account)
    age(first, 30, media_root)
    age(second, 30, media_root)
    real = storage.FilesystemStorage.delete

    def flaky(self, key):
        if key == first.storage_key:
            raise PermissionError("/private/name.jpg")
        real(self, key)

    monkeypatch.setattr(storage.FilesystemStorage, "delete", flaky)
    assert purge() == 2
    assert not MediaAsset.objects.exists() and usage(account) == 0
    assert files(media_root) == [first.storage_key]  # the failed one is left for the next run
    monkeypatch.undo()
    assert purge() == 1  # now a file with no row, old enough
    assert files(media_root) == []


def test_a_batch_is_bounded(media_root, monkeypatch, purge):
    monkeypatch.setattr(media, "BATCH", 2)
    account = owner()
    for _ in range(3):
        age(store(account), 30, media_root)
    assert purge(dry_run=True) == 2
    assert purge() == 2
    assert purge() == 1
    assert files(media_root) == [] and usage(account) == 0


def test_run_retention_reports_the_media_job(media_root, purge):
    from django.core.management import call_command

    old = store(owner())
    age(old, 30, media_root)
    out = io.StringIO()
    call_command("run_retention", "--dry-run", stdout=out)
    assert "kernel.media_orphans: would delete 1" in out.getvalue().splitlines()
