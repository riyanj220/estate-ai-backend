"""`listings.services.delete_draft` (Story 2.13)."""

import threading

import pytest
from django.db import connection

from apps.kernel import runtime
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord, MediaAsset, StorageUsage
from apps.listings import services
from apps.listings.models import (
    ListingAmenity,
    ListingArea,
    ListingImage,
    ListingLocation,
    ListingPrivateAddress,
    ListingResidential,
    ListingRevision,
    ListingTerms,
    PropertyListing,
)
from tests.imaging import make_image
from tests.listings.support import (
    HOUSE,
    make_account,
    random_email,
    seed_cap,
    seed_media_policies,
    set_account_state,
    set_listing_visibility,
    user,
)

JPEG = make_image("JPEG", (400, 300))
TITLE = "Quiet corner villa"
STREET = "Mango Lane"
CHILD_MODELS = (
    ListingImage,
    ListingRevision,
    ListingTerms,
    ListingArea,
    ListingResidential,
    ListingLocation,
    ListingPrivateAddress,
    ListingAmenity,
)


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


def code_of(call, *args):
    with pytest.raises(DomainError) as caught:
        call(*args)
    return caught.value.code


def stored_files(media_root):
    # the keys as stored: the path below the root for filed keys, the file name otherwise
    found = (p.relative_to(media_root).as_posix() for p in media_root.rglob("*") if p.is_file())
    return sorted(k if k.startswith("users/") else k.rsplit("/", 1)[-1] for k in found)


def full_draft(account=None, images=2):
    account = account or make_account(random_email())
    view = services.create_draft(user(account), *HOUSE)
    services.apply_updates(
        user(account),
        view.id,
        view.revision,
        {
            "title": TITLE,
            "area_value": "10",
            "area_unit": "marla",
            "marla_basis": "225",
            "bedrooms": 3,
            "city": None,
            "street": STREET,
            "latitude": "31.5",
            "longitude": "74.3",
            "society": "Gulberg",
            "amenities": {"elevator": "present"},
        },
    )
    for _ in range(images):
        services.upload_image(user(account), view.id, JPEG)
    return account, view


def test_the_owner_deletes_a_draft_and_everything_of_it(
    media_root, django_capture_on_commit_callbacks
):
    owner, view = full_draft()
    other, kept = full_draft(images=1)
    assert any(model.objects.filter(listing_id=view.id).exists() for model in CHILD_MODELS)
    with django_capture_on_commit_callbacks(execute=True):
        assert services.delete_draft(user(owner), view.id) is None
    assert not PropertyListing.objects.filter(id=view.id).exists()
    for model in CHILD_MODELS:
        assert not model.objects.filter(listing_id=view.id).exists(), model
    assert StorageUsage.objects.get(pk=owner.id).bytes_used == 0
    assert MediaAsset.objects.count() == 1 and len(stored_files(media_root)) == 1
    # the other account's Draft is untouched
    assert PropertyListing.objects.filter(id=kept.id).exists()
    assert ListingImage.objects.filter(listing_id=kept.id).count() == 1
    assert StorageUsage.objects.get(pk=other.id).bytes_used > 0


def test_the_files_go_after_commit_not_before(media_root, django_capture_on_commit_callbacks):
    owner, view = full_draft(images=2)
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        services.delete_draft(user(owner), view.id)
    assert len(stored_files(media_root)) == 2 and len(callbacks) >= 2  # still there, queued
    for callback in callbacks:
        callback()
    assert stored_files(media_root) == []


def test_a_failing_file_delete_never_fails_the_delete(media_root, monkeypatch):
    from apps.kernel import storage

    owner, view = full_draft(images=1)
    real = storage.get_storage()

    def boom(key):
        raise OSError("disk gone")

    monkeypatch.setattr(real, "delete", boom)
    services.delete_draft(user(owner), view.id)  # no exception
    assert not PropertyListing.objects.filter(id=view.id).exists()
    assert not MediaAsset.objects.exists()  # the orphan purge finds the file within 24 hours


def test_a_lost_asset_row_is_no_obstacle(media_root):
    owner, view = full_draft(images=1)
    MediaAsset.objects.all().delete()
    services.delete_draft(user(owner), view.id)
    assert not PropertyListing.objects.filter(id=view.id).exists()
    assert not ListingImage.objects.exists()


def test_one_audit_row_with_the_id_and_the_reason_and_no_content():
    owner, view = full_draft()
    services.delete_draft(user(owner), view.id)
    record = AuditRecord.objects.get(action="listing.draft_deleted")
    assert (record.actor_id, record.actor_kind) == (owner.id, "user")
    assert (record.target_type, record.target_id) == ("listing", view.id)
    assert record.metadata == {"reason": "owner"} and record.reason == ""
    dumped = str(record.__dict__)
    assert TITLE not in dumped and STREET not in dumped and "Gulberg" not in dumped


def test_nothing_else_is_written_but_the_audit_row():
    owner, view = full_draft(images=0)
    before = AuditRecord.objects.count()
    services.delete_draft(user(owner), view.id)
    assert AuditRecord.objects.count() == before + 1


def test_a_repeat_another_account_an_administrator_and_a_bad_id_are_the_same_not_found():
    owner, view = full_draft(images=0)
    stranger = make_account(random_email())
    assert code_of(services.delete_draft, user(stranger), view.id) == "not_found"
    assert code_of(services.delete_draft, Actor.admin(stranger.id, "r"), view.id) == "not_found"
    assert code_of(services.delete_draft, Actor.system("r"), view.id) == "not_found"
    for bad in (
        "not-a-uuid",
        "",
        None,
        7,
        str(view.id).upper(),
        "00000000-0000-4000-8000-000000000000",
    ):
        assert code_of(services.delete_draft, user(owner), bad) == "not_found"
    assert PropertyListing.objects.filter(id=view.id).exists()
    services.delete_draft(user(owner), view.id)
    assert code_of(services.delete_draft, user(owner), view.id) == "not_found"


def test_a_visitor_is_unauthenticated():
    _, view = full_draft(images=0)
    assert code_of(services.delete_draft, Actor.visitor("r"), view.id) == "unauthenticated"
    assert PropertyListing.objects.filter(id=view.id).exists()


@pytest.mark.parametrize("state", ["suspended", "deactivated"])
def test_a_suspended_or_deactivated_owner_is_forbidden_and_nothing_changes(state, media_root):
    owner, view = full_draft(images=1)
    set_account_state(owner.id, state)
    assert code_of(services.delete_draft, user(owner), view.id) == "forbidden"
    assert PropertyListing.objects.filter(id=view.id).exists()
    assert ListingImage.objects.count() == 1 and len(stored_files(media_root)) == 1
    assert not AuditRecord.objects.filter(action="listing.draft_deleted").exists()


def test_an_email_unverified_owner_may_delete():
    owner = make_account(random_email(), state="unverified")
    view = services.create_draft(user(owner), *HOUSE)
    services.delete_draft(user(owner), view.id)
    assert not PropertyListing.objects.filter(id=view.id).exists()


def test_a_published_listing_is_a_conflict_and_nothing_changes(media_root):
    owner, view = full_draft(images=1)
    set_listing_visibility(view.id, "published")
    assert code_of(services.delete_draft, user(owner), view.id) == "conflict"
    assert PropertyListing.objects.get(id=view.id).status == "published"
    assert ListingImage.objects.count() == 1 and len(stored_files(media_root)) == 1
    assert StorageUsage.objects.get(pk=owner.id).bytes_used > 0
    assert all(model.objects.filter(listing_id=view.id).exists() for model in (ListingTerms,))
    assert not AuditRecord.objects.filter(action="listing.draft_deleted").exists()


def test_deleting_frees_an_open_draft_slot():
    seed_cap(2)
    owner = make_account(random_email())
    first = services.create_draft(user(owner), *HOUSE)
    services.create_draft(user(owner), *HOUSE)
    assert code_of(services.create_draft, user(owner), *HOUSE) == "throttled"
    services.delete_draft(user(owner), first.id)
    services.create_draft(user(owner), *HOUSE)


def test_a_failure_after_the_deletes_rolls_everything_back(media_root, monkeypatch):
    from apps.kernel import audit

    owner, view = full_draft(images=1)

    def boom(*args, **kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(audit, "record", boom)
    with pytest.raises(RuntimeError):
        services.delete_draft(user(owner), view.id)
    assert PropertyListing.objects.filter(id=view.id).exists()
    assert ListingImage.objects.count() == 1 and MediaAsset.objects.count() == 1
    assert StorageUsage.objects.get(pk=owner.id).bytes_used > 0
    assert len(stored_files(media_root)) == 1  # files are only deleted after a commit
    assert ListingRevision.objects.filter(listing_id=view.id).exists()


def test_an_unrelated_draft_of_the_same_account_is_untouched(media_root):
    owner, view = full_draft(images=1)
    sibling = services.create_draft(user(owner), *HOUSE)
    services.upload_image(user(owner), sibling.id, JPEG)
    services.delete_draft(user(owner), view.id)
    assert PropertyListing.objects.filter(id=sibling.id).exists()
    assert ListingImage.objects.filter(listing_id=sibling.id).count() == 1
    assert StorageUsage.objects.get(pk=owner.id).bytes_used == MediaAsset.objects.get().bytes


# --- races -------------------------------------------------------------------------------------


def run_parallel(*works):
    barrier, outcomes = threading.Barrier(len(works)), []

    def run(work):
        try:
            barrier.wait()
            work()
            outcomes.append("ok")
        except DomainError as exc:
            outcomes.append(exc.code)
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(w,)) for w in works]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return outcomes


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("round_", range(3))
def test_a_save_racing_the_delete_ends_cleanly(round_, media_root):
    seed_cap()
    seed_media_policies()
    owner, view = full_draft(images=1)
    revision = PropertyListing.objects.get(id=view.id).revision
    outcomes = run_parallel(
        lambda: services.delete_draft(user(owner), view.id),
        lambda: services.apply_updates(user(owner), view.id, revision, {"bedrooms": 5}),
    )
    # the edit either lands first (then the delete removes it too) or finds nothing
    assert sorted(outcomes) in (["not_found", "ok"], ["ok", "ok"]), outcomes
    assert not PropertyListing.objects.filter(id=view.id).exists()
    assert not ListingImage.objects.exists() and not MediaAsset.objects.exists()
    assert stored_files(media_root) == []
    assert StorageUsage.objects.get(pk=owner.id).bytes_used == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("round_", range(3))
def test_an_upload_racing_the_delete_leaves_no_orphan(round_, media_root):
    seed_cap()
    seed_media_policies()
    owner, view = full_draft(images=1)
    outcomes = run_parallel(
        lambda: services.delete_draft(user(owner), view.id),
        lambda: services.upload_image(user(owner), view.id, JPEG),
    )
    assert "ok" in outcomes and set(outcomes) <= {"ok", "not_found"}, outcomes
    assert not PropertyListing.objects.filter(id=view.id).exists()
    assert not ListingImage.objects.exists() and not MediaAsset.objects.exists()
    assert stored_files(media_root) == [] and StorageUsage.objects.get(pk=owner.id).bytes_used == 0


@pytest.mark.django_db(transaction=True)
def test_two_parallel_deletes_one_wins_and_the_other_is_not_found(media_root):
    seed_cap()
    seed_media_policies()
    owner, view = full_draft(images=1)
    outcomes = run_parallel(
        lambda: services.delete_draft(user(owner), view.id),
        lambda: services.delete_draft(user(owner), view.id),
    )
    assert sorted(outcomes) == ["not_found", "ok"], outcomes
    assert AuditRecord.objects.filter(action="listing.draft_deleted").count() == 1
    assert stored_files(media_root) == [] and StorageUsage.objects.get(pk=owner.id).bytes_used == 0


@pytest.mark.django_db(transaction=True)
def test_a_remove_image_racing_the_delete_ends_cleanly(media_root):
    seed_cap()
    seed_media_policies()
    owner, view = full_draft(images=1)
    image = ListingImage.objects.get()
    outcomes = run_parallel(
        lambda: services.delete_draft(user(owner), view.id),
        lambda: services.remove_image(user(owner), view.id, image.id),
    )
    assert "ok" in outcomes and set(outcomes) <= {"ok", "not_found"}, outcomes
    assert not PropertyListing.objects.filter(id=view.id).exists()
    assert stored_files(media_root) == [] and StorageUsage.objects.get(pk=owner.id).bytes_used == 0


# --- the locks, deterministically -----------------------------------------------------------------


def hold_the_listing_lock_while(view, call):
    """Hold the Listing row lock in this connection, start `call` in a thread and show it waits
    for the lock (still running after half a second, the Listing still there), then release."""
    from django.db import transaction as tx

    outcome = []

    def work():
        try:
            outcome.append(call())
        except DomainError as exc:
            outcome.append(exc.code)
        finally:
            connection.close()

    thread = threading.Thread(target=work)
    with tx.atomic():
        PropertyListing.objects.select_for_update().get(id=view.id)
        thread.start()
        thread.join(0.5)
        assert thread.is_alive(), outcome  # blocked on the row lock
        assert PropertyListing.objects.filter(id=view.id).exists()
    thread.join(10)
    assert not thread.is_alive()
    return outcome


@pytest.mark.django_db(transaction=True)
def test_delete_draft_waits_for_the_listing_lock(media_root):
    seed_cap()
    seed_media_policies()
    owner, view = full_draft(images=1)
    outcome = hold_the_listing_lock_while(view, lambda: services.delete_draft(user(owner), view.id))
    assert outcome == [None]
    assert not PropertyListing.objects.filter(id=view.id).exists()


@pytest.mark.django_db(transaction=True)
def test_expire_idle_draft_waits_for_the_listing_lock(media_root):
    from datetime import timedelta

    from django.utils import timezone

    from apps.kernel.models import Notification

    seed_cap()
    seed_media_policies()
    owner, view = full_draft(images=0)
    now = timezone.now()
    PropertyListing.objects.filter(id=view.id).update(
        expiry_notice_sent_at=now - timedelta(days=30), delete_after=now - timedelta(days=1)
    )
    Notification.objects.create(
        account_id=owner.id,
        kind="listing.draft_expiring",
        payload={"listing_id": str(view.id), "days_left": 30},
        created_at=now,
    )
    PropertyListing.objects.filter(id=view.id).update(
        created_at=now - timedelta(days=300), updated_at=now - timedelta(days=300)
    )
    outcome = hold_the_listing_lock_while(
        view, lambda: services.expire_idle_draft(Actor.system("r"), view.id, now)
    )
    assert outcome == ["deleted"]
    assert not PropertyListing.objects.filter(id=view.id).exists()


def test_both_deleting_services_lock_the_account_then_the_listing(db):
    from django.db import connection as conn
    from django.test.utils import CaptureQueriesContext

    owner, view = full_draft(images=0)
    with CaptureQueriesContext(conn) as queries:
        services.delete_draft(user(owner), view.id)
    locks = [q["sql"] for q in queries.captured_queries if "FOR UPDATE" in q["sql"]]
    tables = [
        "identity_account" if "account" in sql.split("FROM")[1].split()[0] else "listing"
        for sql in locks
        if "FROM" in sql
    ]
    assert tables[0] == "identity_account" and "listing" in tables[1:]
