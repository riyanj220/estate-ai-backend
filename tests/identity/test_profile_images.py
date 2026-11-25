"""Profile pictures and Agency logos at service and selector level (Story 2.11)."""

import threading
import uuid

import pytest
from django.db import connection
from django.utils import timezone

from apps.identity import selectors, services
from apps.identity.models import Account, AgencyProfile, IndividualProfile
from apps.kernel import audit, media, runtime, storage
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.images import ImageRejected
from apps.kernel.models import AuditRecord, MediaAsset, RateLimitPolicy, StorageUsage
from tests.imaging import make_image
from tests.listings.support import SYSTEM, make_account, seed_cap, seed_media_policies, user

PNG = make_image("PNG", (500, 400))
JPEG = make_image("JPEG", (1200, 800))
ADMIN = Actor.admin(uuid.UUID("33333333-3333-4333-8333-333333333333"), "r")
KINDS = ["individual", "agency"]


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


def owner(kind="individual", email=None, state="active"):
    """An account of this type in this state with its profile row."""
    view = make_account(email or f"{kind}.owner@example.com", kind, state)
    if kind == "individual":
        IndividualProfile.objects.create(
            account_id=view.id, full_name="Jane Doe", phone="+923001234567"
        )
    else:
        AgencyProfile.objects.create(
            account_id=view.id,
            agency_name="Skyline Estates",
            business_phone="+923215550123",
            office_address="Suite 4, Harbour Tower",
            city_id=uuid.uuid4(),
        )
    return view


def profile_of(view):
    model = IndividualProfile if view.type == "individual" else AgencyProfile
    return model.objects.get(account_id=view.id)


def put(view, data=PNG, kind=None):
    return services.set_profile_image(user(view), data, kind=kind or view.type)


def stored_files(media_root):
    return sorted(p.name for p in media_root.rglob("*") if p.is_file())


def code_of(call, *args, **kwargs):
    with pytest.raises(DomainError) as exc:
        call(*args, **kwargs)
    return exc.value.code


# --- set, replace, remove ----------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_set_stores_attaches_references_and_audits_once(kind, media_root):
    view = owner(kind)
    image = put(view)
    asset = MediaAsset.objects.get()
    assert profile_of(view).image_asset_id == asset.id
    assert asset.owner_account_id == view.id and asset.attached_at is not None
    assert StorageUsage.objects.get().bytes_used == asset.bytes == image.bytes
    assert (image.content_type, image.width, image.height) == ("image/png", 500, 400)
    expected = (
        "/api/v1/me/profile/image/file"
        if kind == "individual"
        else ("/api/v1/me/agency-profile/image/file")
    )
    assert image.url == expected
    assert stored_files(media_root) == [asset.storage_key]
    record = AuditRecord.objects.get(action="account.profile_image_changed")
    assert record.metadata == {} and str(record.target_id) == str(view.id)


def test_replace_points_at_the_new_asset_and_removes_the_old_one_once(
    media_root, django_capture_on_commit_callbacks
):
    view = owner()
    put(view, PNG)
    old = MediaAsset.objects.get()
    with django_capture_on_commit_callbacks(execute=True):
        new = put(view, JPEG)
    assets = list(MediaAsset.objects.all())
    assert len(assets) == 1 and assets[0].id != old.id
    assert profile_of(view).image_asset_id == assets[0].id
    assert StorageUsage.objects.get().bytes_used == assets[0].bytes == new.bytes
    assert stored_files(media_root) == [assets[0].storage_key]
    assert not storage.get_storage().exists(old.storage_key)
    assert AuditRecord.objects.filter(action="account.profile_image_changed").count() == 2


@pytest.mark.parametrize("kind", KINDS)
def test_remove_clears_everything_and_a_repeat_changes_nothing(
    kind, media_root, django_capture_on_commit_callbacks
):
    view = owner(kind)
    put(view)
    with django_capture_on_commit_callbacks(execute=True):
        assert services.remove_profile_image(user(view), kind=kind) is None
    assert profile_of(view).image_asset_id is None
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []
    assert StorageUsage.objects.get().bytes_used == 0
    audits = AuditRecord.objects.filter(action="account.profile_image_changed").count()
    assert services.remove_profile_image(user(view), kind=kind) is None  # harmless
    assert StorageUsage.objects.get().bytes_used == 0
    assert AuditRecord.objects.filter(action="account.profile_image_changed").count() == audits


def test_the_profile_views_carry_the_image_and_the_logo_url():
    person, agency = owner("individual"), owner("agency")
    assert selectors.get_profile(person.id).image is None
    assert selectors.get_agency_profile(agency.id).logo_url is None
    put(person)
    put(agency)
    shown = selectors.get_profile(person.id).image
    assert shown.url == "/api/v1/me/profile/image/file" and shown.bytes > 0
    full = selectors.get_agency_profile(agency.id)
    profile = profile_of(agency)
    assert full.agency_id == profile.id and full.agency_id != agency.id
    assert full.logo_url == f"/api/v1/agencies/{profile.id}/logo"
    assert "agency_id" not in repr(shown) and str(person.id) not in repr(shown)


@pytest.mark.parametrize("data", [b"garbage", b"x" * (5 * 1024 * 1024 + 1)])
def test_a_rejected_image_stores_nothing_and_keeps_the_old_one(data, media_root):
    view = owner()
    put(view)
    before = (profile_of(view).image_asset_id, stored_files(media_root))
    with pytest.raises(ImageRejected):
        put(view, data)
    assert (profile_of(view).image_asset_id, stored_files(media_root)) == before
    assert MediaAsset.objects.count() == 1


# --- who may (the gate is the profile PATCH's own) ---------------------------------------------


def test_visitors_administrators_and_the_system_are_refused_before_anything_is_read(media_root):
    view = owner()
    assert code_of(services.set_profile_image, Actor.visitor("r"), PNG, kind="individual") == (
        "unauthenticated"
    )
    assert code_of(services.remove_profile_image, Actor.visitor("r"), kind="individual") == (
        "unauthenticated"
    )
    assert code_of(services.set_profile_image, ADMIN, PNG, kind="individual") == "forbidden"
    assert code_of(services.set_profile_image, SYSTEM, PNG, kind="individual") == "forbidden"
    assert code_of(services.remove_profile_image, ADMIN, kind="individual") == "forbidden"
    assert code_of(services.require_profile_image_access, user(view), kind="agency") == "forbidden"
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []


def test_the_other_account_type_and_a_missing_profile_are_forbidden():
    person, agency = owner("individual"), owner("agency")
    assert code_of(services.set_profile_image, user(person), PNG, kind="agency") == "forbidden"
    assert code_of(services.set_profile_image, user(agency), PNG, kind="individual") == "forbidden"
    bare = make_account("bare@example.com", "individual")  # no profile row
    assert code_of(services.set_profile_image, user(bare), PNG, kind="individual") == "forbidden"
    admin_account = make_account("root@example.com", "admin")
    assert code_of(services.set_profile_image, user(admin_account), PNG, kind="individual") == (
        "forbidden"
    )
    with pytest.raises(ValueError):
        services.require_profile_image_access(user(person), kind="admin")
    assert not MediaAsset.objects.exists()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("state", "allowed"),
    [("active", True), ("unverified", True), ("suspended", True), ("deactivated", False)],
)
def test_the_account_state_gate_is_the_profile_update_gate(kind, state, allowed):
    view = owner(kind, state=state)
    if allowed:
        assert put(view).bytes > 0
        assert services.remove_profile_image(user(view), kind=kind) is None
    else:
        assert code_of(services.set_profile_image, user(view), PNG, kind=kind) == (
            "unauthenticated"
        )
        assert code_of(services.remove_profile_image, user(view), kind=kind) == "unauthenticated"
        assert not MediaAsset.objects.exists()


def test_the_gate_matches_the_profile_update_for_every_state():
    """Pin the claim: wherever `update_profile` is refused, the image write is refused too."""
    for state in ("active", "unverified", "suspended", "deactivated"):
        view = owner("individual", email=f"{state}@example.com", state=state)
        try:
            services.update_profile(user(view), {"full_name": "Jane Roe"})
            profile_ok = True
        except DomainError:
            profile_ok = False
        try:
            put(view)
            image_ok = True
        except DomainError:
            image_ok = False
        assert profile_ok == image_ok, state


# --- the storage cap ---------------------------------------------------------------------------


def test_over_the_cap_is_refused_and_the_old_image_survives(media_root):
    view = owner()
    first = put(view)
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(
        limit=first.bytes + 1
    )
    keep = (profile_of(view).image_asset_id, stored_files(media_root))
    assert code_of(put, view, make_image("PNG", (1600, 1200), color=(1, 200, 3))) == (
        "storage_cap_exceeded"
    )
    assert (profile_of(view).image_asset_id, stored_files(media_root)) == keep
    assert StorageUsage.objects.get().bytes_used == first.bytes
    assert MediaAsset.objects.count() == 1


def test_a_replacement_counts_only_its_net_size_against_the_cap(media_root):
    view = owner()
    first = put(view)
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(limit=first.bytes)
    assert put(view).bytes == first.bytes  # same size replaces: it fits only because the old goes
    assert MediaAsset.objects.count() == 1 and StorageUsage.objects.get().bytes_used == first.bytes


def test_the_profile_image_counts_towards_the_owners_cap_with_listing_images():
    view = owner()
    first = put(view)
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(
        limit=first.bytes + 10
    )
    with pytest.raises(DomainError) as exc:
        media.store_image(user(view), PNG, account_type="individual")
    assert exc.value.code == "storage_cap_exceeded"


# --- rollback ----------------------------------------------------------------------------------


def _boom(*args, **kwargs):
    raise RuntimeError("database failed")


def test_a_failure_after_the_file_write_leaves_no_file_and_changes_nothing(media_root, monkeypatch):
    view = owner()
    written = []
    real_put = storage.FilesystemStorage.put

    def spy(self, key, data):
        written.append(key)
        real_put(self, key, data)

    monkeypatch.setattr(storage.FilesystemStorage, "put", spy)
    monkeypatch.setattr(IndividualProfile, "save", _boom)  # after the file and the row exist
    with pytest.raises(RuntimeError):
        put(view)
    assert len(written) == 1  # the file really was written first
    assert stored_files(media_root) == []
    assert not MediaAsset.objects.exists() and not StorageUsage.objects.exists()
    assert profile_of(view).image_asset_id is None


def test_a_failure_after_the_reference_update_restores_the_old_image(
    media_root, monkeypatch, django_capture_on_commit_callbacks
):
    view = owner()
    put(view, PNG)
    old = MediaAsset.objects.get()
    usage = StorageUsage.objects.get().bytes_used
    monkeypatch.setattr(audit, "record", _boom)  # the last statement of the transaction
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError):
            put(view, JPEG)
    assert profile_of(view).image_asset_id == old.id
    assert list(MediaAsset.objects.values_list("id", flat=True)) == [old.id]
    assert stored_files(media_root) == [old.storage_key]  # the old file was NOT deleted
    assert StorageUsage.objects.get().bytes_used == usage


def test_a_failing_remove_keeps_the_picture_the_file_and_the_usage(
    media_root, monkeypatch, django_capture_on_commit_callbacks
):
    view = owner()
    put(view)
    asset = MediaAsset.objects.get()
    monkeypatch.setattr(audit, "record", _boom)
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError):
            services.remove_profile_image(user(view), kind="individual")
    assert profile_of(view).image_asset_id == asset.id
    assert stored_files(media_root) == [asset.storage_key]
    assert StorageUsage.objects.get().bytes_used == asset.bytes


# --- ghosts ------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_a_reference_to_a_lost_asset_reads_as_no_image_and_the_next_write_cleans_it(kind):
    view = owner(kind)
    put(view)
    MediaAsset.objects.all().delete()  # the row is gone, the reference remains
    read = selectors.get_profile if kind == "individual" else selectors.get_agency_profile
    assert read(view.id).image is None
    if kind == "agency":
        assert selectors.get_agency_profile(view.id).logo_url is None
    with pytest.raises(DomainError):
        selectors.own_image_for_serving(view.id, kind)
    replaced = put(view)
    assert replaced.bytes > 0 and MediaAsset.objects.count() == 1
    assert profile_of(view).image_asset_id == MediaAsset.objects.get().id


def test_removing_a_ghost_clears_the_reference():
    view = owner()
    put(view)
    MediaAsset.objects.all().delete()
    services.remove_profile_image(user(view), kind="individual")
    assert profile_of(view).image_asset_id is None


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
def test_parallel_replaces_leave_exactly_one_reference_and_one_asset(media_root):
    seed_cap()
    seed_media_policies()
    view = owner()
    put(view)
    outcomes = run_parallel(4, lambda i: put(view, PNG if i % 2 else JPEG))
    assert set(outcomes) <= {"ok", "busy"} and "ok" in outcomes  # busy: the heavy slots are few
    asset = MediaAsset.objects.get()
    assert profile_of(view).image_asset_id == asset.id
    assert StorageUsage.objects.get().bytes_used == asset.bytes
    assert stored_files(media_root) == [asset.storage_key]


@pytest.mark.django_db(transaction=True)
def test_a_profile_upload_racing_a_listing_upload_at_the_cap_admits_exactly_one(media_root):
    seed_cap()
    seed_media_policies()
    view = owner()
    size = len(media.process_upload(user(view), PNG).data)
    RateLimitPolicy.objects.filter(scope="storage_cap_bytes_individual").update(limit=size)

    def work(i):
        if i == 0:
            put(view, PNG)
        else:
            media.store_image(user(view), PNG, account_type="individual")

    outcomes = run_parallel(2, work)
    assert sorted(outcomes) == ["ok", "storage_cap_exceeded"]
    assert StorageUsage.objects.get().bytes_used == size == MediaAsset.objects.get().bytes
    assert len(stored_files(media_root)) == 1


# --- erasure -----------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_purge_removes_the_asset_the_usage_the_file_and_the_reference(
    kind, media_root, django_capture_on_commit_callbacks
):
    view = owner(kind)
    put(view)
    with django_capture_on_commit_callbacks(execute=True):
        services.purge_profile_image(SYSTEM, view.id)
    assert profile_of(view).image_asset_id is None
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []
    assert StorageUsage.objects.get().bytes_used == 0
    services.purge_profile_image(SYSTEM, view.id)  # nothing left: a no-op
    services.purge_profile_image(SYSTEM, uuid.uuid4())  # unknown account: a no-op


def test_purge_is_for_the_system_and_administrators_only():
    view = owner()
    put(view)
    assert code_of(services.purge_profile_image, user(view), view.id) == "forbidden"
    assert code_of(services.purge_profile_image, Actor.visitor("r"), view.id) == "unauthenticated"
    assert code_of(services.purge_profile_image, SYSTEM, "not-a-uuid") == "not_found"
    assert MediaAsset.objects.count() == 1


@pytest.mark.parametrize("kind", KINDS)
def test_deactivation_removes_the_picture_in_the_same_transaction(
    kind, media_root, django_capture_on_commit_callbacks
):
    view = owner(kind)
    put(view)
    with django_capture_on_commit_callbacks(execute=True):
        services.deactivate(ADMIN, view.id)
    assert Account.objects.get(id=view.id).deactivated_at is not None
    assert profile_of(view).image_asset_id is None
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []
    assert StorageUsage.objects.get().bytes_used == 0


def test_a_failing_deactivation_keeps_the_picture(media_root, monkeypatch):
    view = owner()
    put(view)
    asset = MediaAsset.objects.get()
    monkeypatch.setattr(audit, "record", _boom)
    with pytest.raises(RuntimeError):
        services.deactivate(ADMIN, view.id)
    assert Account.objects.get(id=view.id).deactivated_at is None
    assert profile_of(view).image_asset_id == asset.id
    assert stored_files(media_root) == [asset.storage_key]


def test_suspension_keeps_the_asset_and_reactivation_shows_the_logo_again():
    agency = owner("agency")
    put(agency)
    profile_id = profile_of(agency).id
    services.suspend(ADMIN, agency.id, "policy breach")
    assert MediaAsset.objects.count() == 1
    with pytest.raises(DomainError):
        selectors.logo_for_serving(profile_id)
    services.reactivate(ADMIN, agency.id)
    assert selectors.logo_for_serving(profile_id).size > 0


# --- the access contract of the two serving selectors -----------------------------------------


@pytest.mark.parametrize("with_bytes", [True, False])
@pytest.mark.parametrize(
    ("state", "visible"),
    [("active", True), ("unverified", False), ("suspended", False), ("deactivated", False)],
)
def test_the_public_logo_is_served_only_while_the_agency_is_active(state, visible, with_bytes):
    agency = owner("agency", state="active")
    put(agency)
    profile_id = profile_of(agency).id
    Account.objects.filter(id=agency.id).update(
        **{
            "active": {},
            "unverified": {"email_verified_at": None},
            "suspended": {"suspended_at": timezone.now(), "suspension_reason": "r"},
            "deactivated": {"deactivated_at": timezone.now()},
        }[state]
    )
    if visible:
        served = selectors.logo_for_serving(profile_id, with_bytes=with_bytes)
        assert served.content_type == "image/png" and served.size > 0
        assert bool(served.data) is with_bytes
    else:
        assert code_of(selectors.logo_for_serving, profile_id, with_bytes=with_bytes) == "not_found"


@pytest.mark.parametrize(
    "bad", ["", "nope", "ABCDEF", str(uuid.UUID(int=7)), None, 5, uuid.UUID(int=8).hex]
)
def test_unknown_or_malformed_logo_ids_are_not_found(bad):
    agency = owner("agency")
    put(agency)
    assert code_of(selectors.logo_for_serving, bad) == "not_found"


def test_the_account_id_is_not_the_public_agency_id_and_no_logo_is_not_found():
    agency = owner("agency")
    assert code_of(selectors.logo_for_serving, profile_of(agency).id) == "not_found"  # no logo
    put(agency)
    assert code_of(selectors.logo_for_serving, agency.id) == "not_found"
    assert code_of(selectors.logo_for_serving, str(agency.id)) == "not_found"
    assert selectors.logo_for_serving(str(profile_of(agency).id)).size > 0


def test_a_lost_logo_file_or_row_is_not_found(media_root):
    agency = owner("agency")
    put(agency)
    profile_id = profile_of(agency).id
    asset = MediaAsset.objects.get()
    storage.get_storage().delete(asset.storage_key)
    assert code_of(selectors.logo_for_serving, profile_id) == "not_found"
    assert code_of(selectors.logo_for_serving, profile_id, with_bytes=False) == "not_found"  # HEAD
    MediaAsset.objects.all().delete()
    assert code_of(selectors.logo_for_serving, profile_id, with_bytes=False) == "not_found"


def test_the_individuals_picture_is_served_only_to_its_own_individual_account():
    person, other, agency = (
        owner("individual"),
        owner("individual", "o@example.com"),
        owner("agency"),
    )
    put(person)
    put(agency)
    assert selectors.own_image_for_serving(person.id, "individual").size > 0
    assert selectors.own_image_for_serving(str(person.id), "individual", with_bytes=False).size > 0
    for who, kind in (
        (other.id, "individual"),  # another account
        (agency.id, "individual"),  # the other account type
        (person.id, "agency"),
        (None, "individual"),  # a Visitor has no account id
        ("nope", "individual"),
        (uuid.uuid4(), "individual"),
    ):
        assert code_of(selectors.own_image_for_serving, who, kind) == "not_found"
    assert selectors.own_image_for_serving(agency.id, "agency").size > 0
    assert code_of(selectors.own_image_for_serving, other.id, "individual") == "not_found"  # none


def test_a_deactivated_owner_cannot_read_their_own_image():
    person = owner("individual")
    put(person)
    Account.objects.filter(id=person.id).update(deactivated_at=timezone.now())
    assert code_of(selectors.own_image_for_serving, person.id, "individual") == "not_found"


# --- review patches ----------------------------------------------------------------------------


def test_the_answer_is_built_from_the_stored_asset_without_a_re_read(monkeypatch):
    view = owner()
    monkeypatch.setattr(selectors, "image_view", _boom)  # a re-read would blow up
    monkeypatch.setattr(media, "assets_by_id", _boom)
    image = put(view)
    assert image.bytes == MediaAsset.objects.get().bytes and image.width == 500


def test_an_account_deactivated_during_processing_gets_unauthenticated_not_a_500(
    monkeypatch, media_root
):
    view = owner()
    real = media.process_upload

    def slow(actor, data):
        processed = real(actor, data)
        Account.objects.filter(id=view.id).update(deactivated_at=timezone.now())  # the race
        return processed

    monkeypatch.setattr(media, "process_upload", slow)
    assert code_of(put, view) == "unauthenticated"
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []


def test_a_remove_landing_between_gate_and_lock_does_not_break_the_put(monkeypatch):
    view = owner()
    put(view)
    real = media.process_upload

    def slow(actor, data):
        processed = real(actor, data)
        services.remove_profile_image(actor, kind="individual")  # the racing DELETE
        return processed

    monkeypatch.setattr(media, "process_upload", slow)
    image = put(view, JPEG)
    assert image.content_type == "image/jpeg"
    assert profile_of(view).image_asset_id == MediaAsset.objects.get().id
