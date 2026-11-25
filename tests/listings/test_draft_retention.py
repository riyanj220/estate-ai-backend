"""Draft retention (Story 2.13): the notice after 180 idle days and the deletion 30 days later.

Time is always explicit: every job gets a `now`, and the stored times are set relative to it."""

import logging
import threading
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from apps.kernel import audit, notifications, retention, runtime
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord, MediaAsset, Notification, StorageUsage
from apps.listings import retention as draft_retention
from apps.listings import selectors, services
from apps.listings.models import (
    ListingAmenity,
    ListingImage,
    ListingPrivateAddress,
    ListingRevision,
    ListingTerms,
    PropertyListing,
)
from tests.imaging import make_image
from tests.listings.support import (
    HOUSE,
    SYSTEM,
    make_account,
    random_email,
    seed_cap,
    seed_media_policies,
    set_account_state,
    set_listing_visibility,
    user,
)

NOW = timezone.now().replace(microsecond=0)
IDLE = timedelta(days=180)
GRACE = timedelta(days=30)
JPEG = make_image("JPEG", (400, 300))
TITLE = "Quiet corner villa"  # a canary: never in a notification, an audit row or a log
STREET = "Mango Lane"


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


def stored_files(media_root):
    return sorted(p.name for p in media_root.rglob("*") if p.is_file())


def draft(account=None, *, idle=IDLE, images=0, content=False):
    """A Draft whose last activity was `idle` before NOW (the stored times are set directly)."""
    account = account or make_account(random_email())
    view = services.create_draft(user(account), *HOUSE)
    if content:
        services.apply_updates(
            user(account),
            view.id,
            view.revision,
            {"title": TITLE, "bedrooms": 3, "street": STREET, "amenities": {"elevator": "present"}},
        )
    for _ in range(images):
        services.upload_image(user(account), view.id, JPEG)
    age(view.id, idle)
    return account, view


def age(listing_id, idle):
    then = NOW - idle
    PropertyListing.objects.filter(id=listing_id).update(
        created_at=then, updated_at=then, last_opened_at=None
    )


def store_notice(owner, view, days_left=30):
    """The notification a notice job would have stored (for a Draft set up by a direct update)."""
    Notification.objects.create(
        account_id=owner.id,
        kind="listing.draft_expiring",
        payload={"listing_id": str(view.id), "days_left": days_left},
        created_at=NOW,
    )


def row(view):
    return PropertyListing.objects.get(id=view.id)


def exists(view):
    return PropertyListing.objects.filter(id=view.id).exists()


def run_job(name, now=NOW, dry_run=False, capture=None):
    jobs = {
        "notice": draft_retention.draft_notice,
        "expiry": draft_retention.draft_expiry,
    }
    if capture is None:
        return jobs[name](now, dry_run)
    with capture(execute=True):
        return jobs[name](now, dry_run)


# --- registration ------------------------------------------------------------------------------


def test_the_jobs_the_kind_and_the_action_are_registered():
    assert {"listings.draft_notice", "listings.draft_expiry"} <= set(retention.registered())
    assert notifications.registered_kinds()["listing.draft_expiring"] == {
        "listing_id": notifications.UUID_TEXT,
        "days_left": notifications.int_range(0, 365),
    }
    assert audit.registered_actions()["listing.draft_deleted"] == {
        "reason": frozenset({"owner", "retention"})
    }


def test_the_constants_are_the_specified_ones():
    assert (selectors.IDLE_DAYS, selectors.GRACE_DAYS) == (180, 30)


# --- the notice --------------------------------------------------------------------------------


def test_exactly_180_days_is_noticed_and_one_second_less_is_not(django_capture_on_commit_callbacks):
    _, edge = draft(idle=IDLE)
    _, just_under = draft(idle=IDLE - timedelta(seconds=1))
    _, fresh = draft(idle=timedelta(days=179))
    assert run_job("notice", capture=django_capture_on_commit_callbacks) == 1
    assert row(edge).expiry_notice_sent_at == NOW
    assert row(edge).delete_after == NOW + GRACE
    for untouched in (just_under, fresh):
        assert row(untouched).expiry_notice_sent_at is None and row(untouched).delete_after is None
    assert Notification.objects.count() == 1


def test_the_notice_goes_to_the_owner_only_with_nothing_but_the_id_and_the_days(
    django_capture_on_commit_callbacks,
):
    owner, view = draft(content=True)
    other = make_account(random_email())
    run_job("notice", capture=django_capture_on_commit_callbacks)
    note = Notification.objects.get()
    assert (note.account_id, note.kind) == (owner.id, "listing.draft_expiring")
    assert note.payload == {"listing_id": str(view.id), "days_left": 30}
    assert not Notification.objects.filter(account_id=other.id).exists()
    text = f"{note.kind}{note.payload}"
    assert TITLE not in text and STREET not in text


def test_a_notice_is_sent_once_and_a_second_run_does_nothing(django_capture_on_commit_callbacks):
    _, view = draft()
    assert run_job("notice", capture=django_capture_on_commit_callbacks) == 1
    stamp = row(view).expiry_notice_sent_at
    assert run_job("notice", NOW, capture=django_capture_on_commit_callbacks) == 0
    assert (
        run_job("notice", NOW + timedelta(days=5), capture=django_capture_on_commit_callbacks) == 0
    )
    assert Notification.objects.count() == 1
    assert row(view).expiry_notice_sent_at == stamp and row(view).delete_after == NOW + GRACE


def test_a_suspended_owner_is_skipped_until_the_account_is_active_again(
    django_capture_on_commit_callbacks,
):
    owner, view = draft()
    set_account_state(owner.id, "suspended")
    assert run_job("notice", capture=django_capture_on_commit_callbacks) == 0
    assert row(view).expiry_notice_sent_at is None and not Notification.objects.exists()
    set_account_state(owner.id, "active")
    assert run_job("notice", capture=django_capture_on_commit_callbacks) == 1


def test_the_activity_time_is_the_latest_of_edit_open_and_creation(
    django_capture_on_commit_callbacks,
):
    _, opened = draft()
    _, created_late = draft()
    _, edited_late = draft()
    PropertyListing.objects.filter(id=opened.id).update(last_opened_at=NOW - timedelta(days=10))
    PropertyListing.objects.filter(id=created_late.id).update(created_at=NOW - timedelta(days=10))
    PropertyListing.objects.filter(id=edited_late.id).update(updated_at=NOW - timedelta(days=10))
    assert run_job("notice", capture=django_capture_on_commit_callbacks) == 0
    PropertyListing.objects.filter(id=opened.id).update(last_opened_at=NOW - IDLE)
    assert run_job("notice", capture=django_capture_on_commit_callbacks) == 1  # only `opened`


def test_published_and_other_non_drafts_are_never_noticed_or_deleted(
    django_capture_on_commit_callbacks,
):
    _, published = draft(images=1)
    set_listing_visibility(published.id, "published")
    PropertyListing.objects.filter(id=published.id).update(
        expiry_notice_sent_at=NOW - GRACE, delete_after=NOW - timedelta(days=1)
    )
    assert run_job("notice", capture=django_capture_on_commit_callbacks) == 0
    assert (
        run_job("expiry", NOW + 400 * timedelta(days=1), capture=django_capture_on_commit_callbacks)
        == 0
    )
    assert exists(published) and ListingImage.objects.filter(listing_id=published.id).exists()
    assert not Notification.objects.exists()
    assert not AuditRecord.objects.filter(action="listing.draft_deleted").exists()


def test_a_dry_run_changes_and_notifies_nothing_and_reports_the_count(
    django_capture_on_commit_callbacks,
):
    draft()
    draft()
    draft(idle=timedelta(days=1))
    assert run_job("notice", dry_run=True, capture=django_capture_on_commit_callbacks) == 2
    assert not PropertyListing.objects.filter(expiry_notice_sent_at__isnull=False).exists()
    assert not Notification.objects.exists()
    assert run_job("notice", capture=django_capture_on_commit_callbacks) == 2


def test_a_failing_notice_is_isolated_and_logged_by_class_only(
    monkeypatch, caplog, django_capture_on_commit_callbacks
):
    _, bad = draft(content=True)
    _, good = draft()
    real = notifications.notify

    def flaky(account_id, kind, payload=None):
        if payload["listing_id"] == bad.id or payload["listing_id"] == str(bad.id):
            raise RuntimeError(f"boom {TITLE} {STREET}")
        return real(account_id, kind, payload)

    monkeypatch.setattr(services.notifications, "notify", flaky)
    with caplog.at_level(logging.ERROR, logger="estate.retention"):
        assert run_job("notice", capture=django_capture_on_commit_callbacks) == 1
    assert row(bad).expiry_notice_sent_at is None and row(bad).delete_after is None  # rolled back
    assert row(good).expiry_notice_sent_at == NOW
    assert Notification.objects.count() == 1
    [record] = [r for r in caplog.records if r.name == "estate.retention"]
    assert record.exception_class == "RuntimeError"
    shown = f"{record.getMessage()} {record.__dict__}"
    assert TITLE not in shown and STREET not in shown and str(bad.id) not in shown


def test_a_notice_that_rolls_back_leaves_no_notification(django_capture_on_commit_callbacks):
    _, view = draft()
    with pytest.raises(RuntimeError):
        with django_capture_on_commit_callbacks(execute=True):
            from django.db import transaction

            with transaction.atomic():
                services.notice_idle_draft(Actor.system("r"), view.id, NOW)
                raise RuntimeError("later failure")
    assert not Notification.objects.exists()
    assert row(view).expiry_notice_sent_at is None


def test_the_service_refuses_every_actor_but_the_system(db):
    owner, view = draft()
    for actor in (user(owner), Actor.visitor("r")):
        for call in (services.notice_idle_draft, services.expire_idle_draft):
            with pytest.raises(DomainError) as caught:
                call(actor, view.id, NOW)
            assert caught.value.code == "forbidden"
    assert row(view).expiry_notice_sent_at is None


def test_the_service_decides_again_under_the_lock(db):
    """A Draft that is no longer idle by the time its turn comes is left alone."""
    _, view = draft()
    PropertyListing.objects.filter(id=view.id).update(updated_at=NOW - timedelta(days=1))
    assert services.notice_idle_draft(Actor.system("r"), view.id, NOW) is False
    assert row(view).expiry_notice_sent_at is None


# --- opening a Draft ---------------------------------------------------------------------------


def test_opening_sets_last_opened_at_at_most_hourly_and_changes_nothing_else(db):
    owner, view = draft()
    before = row(view)
    revisions = ListingRevision.objects.filter(listing_id=view.id).count()
    assert services.record_open(user(owner), view.id, NOW) is True
    assert row(view).last_opened_at == NOW
    assert services.record_open(user(owner), view.id, NOW + timedelta(minutes=59)) is False
    assert row(view).last_opened_at == NOW
    assert services.record_open(user(owner), view.id, NOW + timedelta(hours=1, seconds=1)) is True
    assert row(view).last_opened_at == NOW + timedelta(hours=1, seconds=1)
    after = row(view)
    assert (after.revision, after.updated_at) == (before.revision, before.updated_at)
    assert ListingRevision.objects.filter(listing_id=view.id).count() == revisions


def test_only_the_owner_of_a_draft_records_an_open(db):
    owner, view = draft()
    other = make_account(random_email())
    set_account_state(owner.id, "suspended")
    assert services.record_open(user(other), view.id, NOW) is False
    assert services.record_open(Actor.visitor("r"), view.id, NOW) is False
    assert services.record_open(Actor.system("r"), view.id, NOW) is False
    assert services.record_open(user(owner), "not-a-uuid", NOW) is False
    set_account_state(owner.id, "deactivated")
    assert services.record_open(user(owner), view.id, NOW) is False
    set_account_state(owner.id, "active")
    set_listing_visibility(view.id, "published")
    assert services.record_open(user(owner), view.id, NOW) is False
    assert row(view).last_opened_at is None


# --- the deletion ------------------------------------------------------------------------------


def notice_and_pass(django_capture_on_commit_callbacks, *views):
    run_job("notice", capture=django_capture_on_commit_callbacks)
    for view in views:
        assert row(view).delete_after == NOW + GRACE


def test_a_noticed_draft_is_deleted_exactly_at_delete_after_with_everything_of_it(
    media_root, django_capture_on_commit_callbacks
):
    owner, view = draft(content=True, images=2)
    other, bystander = draft(content=True, images=1)
    PropertyListing.objects.filter(id=bystander.id).update(updated_at=NOW)
    notice_and_pass(django_capture_on_commit_callbacks, view)
    just_before = NOW + GRACE - timedelta(seconds=1)
    assert run_job("expiry", just_before, capture=django_capture_on_commit_callbacks) == 0
    assert exists(view) and len(stored_files(media_root)) == 3
    assert run_job("expiry", NOW + GRACE, capture=django_capture_on_commit_callbacks) == 1
    assert not exists(view)
    for model in (
        ListingImage,
        ListingRevision,
        ListingTerms,
        ListingPrivateAddress,
        ListingAmenity,
    ):
        assert not model.objects.filter(listing_id=view.id).exists(), model
    assert ListingImage.objects.filter(listing_id=bystander.id).count() == 1
    assert exists(bystander)
    assert MediaAsset.objects.count() == 1 and len(stored_files(media_root)) == 1
    assert StorageUsage.objects.get(pk=owner.id).bytes_used == 0
    assert StorageUsage.objects.get(pk=other.id).bytes_used > 0  # the bystander's is untouched


def test_deleted_by_retention_gives_the_usage_back(media_root, django_capture_on_commit_callbacks):
    owner, view = draft(images=2)
    assert StorageUsage.objects.get(pk=owner.id).bytes_used > 0
    notice_and_pass(django_capture_on_commit_callbacks, view)
    run_job("expiry", NOW + GRACE, capture=django_capture_on_commit_callbacks)
    assert StorageUsage.objects.get(pk=owner.id).bytes_used == 0
    assert not MediaAsset.objects.exists() and stored_files(media_root) == []


def test_the_audit_row_has_the_id_and_the_reason_and_no_content(
    django_capture_on_commit_callbacks,
):
    owner, view = draft(content=True, images=1)
    notice_and_pass(django_capture_on_commit_callbacks, view)
    run_job("expiry", NOW + GRACE, capture=django_capture_on_commit_callbacks)
    record = AuditRecord.objects.get(action="listing.draft_deleted")
    assert (record.target_type, record.target_id) == ("listing", view.id)
    assert (record.actor_kind, record.actor_id) == ("system", None)
    assert record.metadata == {"reason": "retention"} and record.reason == ""
    dumped = f"{record.metadata}{record.reason}{record.target_type}{record.request_id}"
    assert TITLE not in dumped and STREET not in dumped


def test_opened_after_the_notice_cancels_and_the_clock_restarts(
    django_capture_on_commit_callbacks,
):
    owner, view = draft(images=1)
    notice_and_pass(django_capture_on_commit_callbacks, view)
    opened = NOW + timedelta(days=5)
    assert services.record_open(user(owner), view.id, opened) is True
    assert run_job("expiry", NOW + GRACE, capture=django_capture_on_commit_callbacks) == 0
    kept = row(view)
    assert (kept.expiry_notice_sent_at, kept.delete_after) == (None, None)
    assert ListingImage.objects.filter(listing_id=view.id).exists()
    assert not AuditRecord.objects.filter(action="listing.draft_deleted").exists()
    # not noticed again until 180 days after the open
    assert (
        run_job(
            "notice",
            opened + IDLE - timedelta(seconds=1),
            capture=django_capture_on_commit_callbacks,
        )
        == 0
    )
    assert run_job("notice", opened + IDLE, capture=django_capture_on_commit_callbacks) == 1
    assert row(view).delete_after == opened + IDLE + GRACE


def test_edited_after_the_notice_cancels(django_capture_on_commit_callbacks):
    owner, view = draft()
    notice_and_pass(django_capture_on_commit_callbacks, view)
    current = row(view)
    services.apply_updates(user(owner), view.id, current.revision, {"bedrooms": 4})
    assert row(view).updated_at >= timezone.now() - timedelta(minutes=5)
    assert run_job("expiry", NOW + GRACE, capture=django_capture_on_commit_callbacks) == 0
    assert (
        exists(view) and row(view).delete_after is None and row(view).expiry_notice_sent_at is None
    )


def test_activity_at_the_same_instant_as_the_notice_does_not_cancel(
    django_capture_on_commit_callbacks,
):
    _, view = draft()
    notice_and_pass(django_capture_on_commit_callbacks, view)
    PropertyListing.objects.filter(id=view.id).update(updated_at=NOW)  # equal, not later
    assert run_job("expiry", NOW + GRACE, capture=django_capture_on_commit_callbacks) == 1


def test_a_suspended_owner_is_skipped_at_expiry_too(django_capture_on_commit_callbacks):
    owner, view = draft()
    notice_and_pass(django_capture_on_commit_callbacks, view)
    set_account_state(owner.id, "suspended")
    assert run_job("expiry", NOW + GRACE, capture=django_capture_on_commit_callbacks) == 0
    assert exists(view) and row(view).delete_after == NOW + GRACE
    set_account_state(owner.id, "active")
    assert run_job("expiry", NOW + GRACE, capture=django_capture_on_commit_callbacks) == 1


def test_a_draft_that_was_never_noticed_is_never_deleted(django_capture_on_commit_callbacks):
    _, view = draft(idle=timedelta(days=2000))
    assert run_job("expiry", NOW + 5 * IDLE, capture=django_capture_on_commit_callbacks) == 0
    assert exists(view)


def test_a_second_expiry_run_and_a_second_notice_run_change_nothing(
    media_root, django_capture_on_commit_callbacks
):
    _, view = draft(images=1)
    notice_and_pass(django_capture_on_commit_callbacks, view)
    assert run_job("expiry", NOW + GRACE, capture=django_capture_on_commit_callbacks) == 1
    audits = AuditRecord.objects.filter(action="listing.draft_deleted").count()
    assert run_job("expiry", NOW + GRACE, capture=django_capture_on_commit_callbacks) == 0
    assert run_job("notice", NOW + GRACE, capture=django_capture_on_commit_callbacks) == 0
    assert AuditRecord.objects.filter(action="listing.draft_deleted").count() == audits == 1
    assert Notification.objects.count() == 1


def test_the_expiry_dry_run_counts_only_what_would_be_deleted(
    media_root, django_capture_on_commit_callbacks
):
    owner_a, deleted = draft(images=1)
    owner_b, cancelled = draft()
    notice_and_pass(django_capture_on_commit_callbacks, deleted, cancelled)
    services.record_open(user(owner_b), cancelled.id, NOW + timedelta(days=2))
    assert (
        run_job("expiry", NOW + GRACE, dry_run=True, capture=django_capture_on_commit_callbacks)
        == 1
    )
    assert exists(deleted) and exists(cancelled) and len(stored_files(media_root)) == 1
    assert row(cancelled).delete_after == NOW + GRACE  # a dry run does not even clear
    assert not AuditRecord.objects.filter(action="listing.draft_deleted").exists()
    assert run_job("expiry", NOW + GRACE, capture=django_capture_on_commit_callbacks) == 1


def test_a_failing_deletion_is_isolated_and_keeps_its_images(
    media_root, monkeypatch, caplog, django_capture_on_commit_callbacks
):
    owner_a, bad = draft(content=True, images=1)
    owner_b, good = draft(images=1)
    notice_and_pass(django_capture_on_commit_callbacks, bad, good)
    real = audit.record

    def flaky(actor, action, target_type, target_id, metadata=None):
        if target_id == bad.id:
            raise RuntimeError(f"boom {TITLE} {STREET}")
        return real(actor, action, target_type, target_id, metadata)

    monkeypatch.setattr(services.audit, "record", flaky)
    with caplog.at_level(logging.ERROR, logger="estate.retention"):
        assert run_job("expiry", NOW + GRACE, capture=django_capture_on_commit_callbacks) == 1
    assert exists(bad) and not exists(good)
    assert (
        ListingImage.objects.filter(listing_id=bad.id).count() == 1
    )  # whole savepoint rolled back
    assert MediaAsset.objects.count() == 1 and len(stored_files(media_root)) == 1
    assert StorageUsage.objects.get(pk=owner_a.id).bytes_used > 0
    [record] = [r for r in caplog.records if r.name == "estate.retention"]
    assert record.exception_class == "RuntimeError"
    shown = f"{record.getMessage()} {record.__dict__}"
    assert TITLE not in shown and STREET not in shown and str(bad.id) not in shown
    monkeypatch.undo()
    assert (
        run_job("expiry", NOW + GRACE, capture=django_capture_on_commit_callbacks) == 1
    )  # retried


def test_a_batch_is_limited_and_the_next_run_does_the_rest(
    monkeypatch, django_capture_on_commit_callbacks
):
    monkeypatch.setattr(selectors, "RETENTION_BATCH", 2)
    monkeypatch.setattr(selectors, "RETENTION_MAX_PAGES", 1)
    for _ in range(3):
        draft()
    assert run_job("notice", capture=django_capture_on_commit_callbacks) == 2
    assert run_job("notice", capture=django_capture_on_commit_callbacks) == 1
    assert run_job("notice", capture=django_capture_on_commit_callbacks) == 0


# --- through the framework and the command ------------------------------------------------------


def test_run_all_runs_both_jobs_in_order_and_the_command_reports_them(
    capsys, django_capture_on_commit_callbacks
):
    _, view = draft()
    names = retention.registered()
    assert names.index("listings.draft_notice") < names.index("listings.draft_expiry")
    with django_capture_on_commit_callbacks(execute=True):
        results = {r.name: r.count for r in retention.run_all(NOW)}
    assert results["listings.draft_notice"] == 1 and results["listings.draft_expiry"] == 0
    assert row(view).delete_after == NOW + GRACE
    with django_capture_on_commit_callbacks(execute=True):
        results = {r.name: r.count for r in retention.run_all(NOW + GRACE)}
    assert results["listings.draft_expiry"] == 1 and not exists(view)
    call_command("run_retention", "--dry-run")
    out = capsys.readouterr().out
    assert "listings.draft_notice: would delete 0" in out
    assert "listings.draft_expiry: would delete 0" in out


# --- a race with the owner ----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_an_edit_racing_the_expiry_ends_cleanly_and_never_orphans_an_image(media_root):
    seed_cap()
    seed_media_policies()
    owner, view = draft(images=1)
    run_job("notice")
    barrier, outcomes = threading.Barrier(2), []

    def expire():
        try:
            barrier.wait()
            outcomes.append(services.expire_idle_draft(Actor.system("r"), view.id, NOW + GRACE))
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)
        finally:
            connection.close()

    def edit():
        try:
            barrier.wait()
            services.apply_updates(user(owner), view.id, row(view).revision, {"bedrooms": 4})
            outcomes.append("edited")
        except DomainError as exc:
            outcomes.append(exc.code)
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)
        finally:
            connection.close()

    threads = [threading.Thread(target=expire), threading.Thread(target=edit)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) in (["cleared", "edited"], ["deleted", "not_found"]), outcomes
    if "deleted" in outcomes:
        assert not exists(view) and not MediaAsset.objects.exists()
        assert stored_files(media_root) == [] and not ListingImage.objects.exists()
    else:
        assert exists(view) and MediaAsset.objects.count() == 1
        assert row(view).delete_after is None


def test_the_system_actor_is_the_only_one_that_can_expire_and_the_owner_still_deletes(db):
    owner, view = draft()
    services.delete_draft(user(owner), view.id)
    assert not exists(view)
    assert SYSTEM.kind.value == "system"  # the shared test actor is the same kind as retention's


# --- candidates and the dry run see the same rules as the real run -------------------------------


def test_the_dry_runs_count_only_real_candidates(django_capture_on_commit_callbacks):
    owner, suspended = draft()
    set_account_state(owner.id, "suspended")
    _, published = draft()
    set_listing_visibility(published.id, "published")
    _, candidate = draft()
    assert run_job("notice", dry_run=True, capture=django_capture_on_commit_callbacks) == 1
    # the same three, noticed and due: the dry run of the deletion skips the suspended owner and
    # the published Listing exactly as the real run does
    PropertyListing.objects.filter(id__in=[suspended.id, published.id, candidate.id]).update(
        expiry_notice_sent_at=NOW - GRACE, delete_after=NOW
    )
    for listing in (suspended, candidate):
        listing_owner = row(listing).owner_account_id
        Notification.objects.create(
            account_id=listing_owner,
            kind="listing.draft_expiring",
            payload={"listing_id": str(listing.id), "days_left": 30},
            created_at=NOW,
        )
    assert run_job("expiry", NOW, dry_run=True, capture=django_capture_on_commit_callbacks) == 1
    assert run_job("expiry", NOW, capture=django_capture_on_commit_callbacks) == 1
    assert exists(suspended) and exists(published) and not exists(candidate)


def test_the_services_decide_again_for_a_suspended_owner_under_the_lock(db):
    owner, view = draft()
    set_account_state(owner.id, "suspended")
    system = Actor.system("r")
    assert services.notice_idle_draft(system, view.id, NOW) is False
    PropertyListing.objects.filter(id=view.id).update(
        expiry_notice_sent_at=NOW - GRACE, delete_after=NOW
    )
    assert services.expire_idle_draft(system, view.id, NOW) == "skipped"
    assert exists(view) and row(view).delete_after == NOW


def test_the_services_never_touch_a_published_listing_even_when_called_directly(db):
    _, view = draft()
    set_listing_visibility(view.id, "published")
    system = Actor.system("r")
    assert services.notice_idle_draft(system, view.id, NOW) is False
    PropertyListing.objects.filter(id=view.id).update(
        expiry_notice_sent_at=NOW - GRACE, delete_after=NOW
    )
    assert services.expire_idle_draft(system, view.id, NOW) == "skipped"
    assert exists(view)


# --- a notice that may never have arrived; a grace period eaten by suspension or an outage -------


def test_a_notice_that_was_never_stored_is_issued_again_and_nothing_is_deleted(
    media_root, django_capture_on_commit_callbacks
):
    owner, view = draft(images=1)
    PropertyListing.objects.filter(id=view.id).update(
        expiry_notice_sent_at=NOW, delete_after=NOW + GRACE
    )  # the job committed its columns but its notification was never stored
    assert not Notification.objects.exists()
    assert run_job("expiry", NOW + GRACE, capture=django_capture_on_commit_callbacks) == 0
    kept = row(view)
    assert exists(view) and len(stored_files(media_root)) == 1
    assert kept.expiry_notice_sent_at == NOW + GRACE and kept.delete_after == NOW + 2 * GRACE
    note = Notification.objects.get()
    assert note.account_id == owner.id
    assert note.payload == {"listing_id": str(view.id), "days_left": 30}
    assert not AuditRecord.objects.filter(action="listing.draft_deleted").exists()
    # now the notice is on the list: the next due run deletes
    assert run_job("expiry", NOW + 2 * GRACE, capture=django_capture_on_commit_callbacks) == 1
    assert not exists(view)


def test_a_notification_for_another_listing_or_account_is_not_this_notice(
    django_capture_on_commit_callbacks,
):
    owner, view = draft()
    other_owner, other = draft()
    PropertyListing.objects.filter(id__in=[view.id, other.id]).update(
        expiry_notice_sent_at=NOW, delete_after=NOW + GRACE
    )
    store_notice(owner, other)  # the right kind, the wrong Listing
    store_notice(other_owner, view)  # the right Listing, the wrong account
    assert run_job("expiry", NOW + GRACE, capture=django_capture_on_commit_callbacks) == 0
    assert exists(view) and exists(other)


def test_a_date_more_than_two_days_past_gets_a_fresh_week_not_a_deletion(
    django_capture_on_commit_callbacks,
):
    """The job did not run for a while (an outage): the owner still gets a last week."""
    owner, view = draft()
    notice_and_pass(django_capture_on_commit_callbacks, view)
    late = NOW + GRACE + timedelta(days=2, seconds=1)
    assert run_job("expiry", late, capture=django_capture_on_commit_callbacks) == 0
    kept = row(view)
    assert kept.expiry_notice_sent_at == late and kept.delete_after == late + timedelta(days=7)
    payloads = [n.payload["days_left"] for n in Notification.objects.order_by("seq")]
    assert payloads == [30, 7]
    assert (
        run_job("expiry", late + timedelta(days=7), capture=django_capture_on_commit_callbacks) == 1
    )
    assert not exists(view)


def test_exactly_two_days_past_is_still_deleted(django_capture_on_commit_callbacks):
    _, view = draft()
    notice_and_pass(django_capture_on_commit_callbacks, view)
    assert (
        run_job(
            "expiry", NOW + GRACE + timedelta(days=2), capture=django_capture_on_commit_callbacks
        )
        == 1
    )


def test_a_suspension_that_outlasts_the_grace_gives_a_fresh_week_when_it_ends(
    django_capture_on_commit_callbacks,
):
    owner, view = draft()
    notice_and_pass(django_capture_on_commit_callbacks, view)
    set_account_state(owner.id, "suspended")
    for day in (0, 5, 40):  # every run while suspended leaves it alone
        when = NOW + GRACE + timedelta(days=day)
        assert run_job("expiry", when, capture=django_capture_on_commit_callbacks) == 0
        assert row(view).delete_after == NOW + GRACE
    set_account_state(owner.id, "active")
    back = NOW + GRACE + timedelta(days=41)
    assert run_job("expiry", back, capture=django_capture_on_commit_callbacks) == 0
    assert exists(view) and row(view).delete_after == back + timedelta(days=7)
    assert Notification.objects.order_by("seq").last().payload["days_left"] == 7
    assert (
        run_job("expiry", back + timedelta(days=7), capture=django_capture_on_commit_callbacks) == 1
    )


def test_the_dry_run_does_not_count_a_draft_the_real_run_would_extend(
    django_capture_on_commit_callbacks,
):
    _, view = draft()
    notice_and_pass(django_capture_on_commit_callbacks, view)
    late = NOW + GRACE + timedelta(days=10)
    assert run_job("expiry", late, dry_run=True, capture=django_capture_on_commit_callbacks) == 0
    assert row(view).delete_after == NOW + GRACE  # a dry run changes nothing


# --- a Deactivated owner is left alone, like a Suspended one -------------------------------------


def test_a_deactivated_owner_is_skipped_by_both_jobs_and_both_dry_runs(
    django_capture_on_commit_callbacks,
):
    owner, view = draft()
    set_account_state(owner.id, "deactivated")
    assert run_job("notice", dry_run=True, capture=django_capture_on_commit_callbacks) == 0
    assert run_job("notice", capture=django_capture_on_commit_callbacks) == 0
    assert services.notice_idle_draft(Actor.system("r"), view.id, NOW) is False
    PropertyListing.objects.filter(id=view.id).update(
        expiry_notice_sent_at=NOW - GRACE, delete_after=NOW
    )
    store_notice(owner, view)
    assert run_job("expiry", NOW, dry_run=True, capture=django_capture_on_commit_callbacks) == 0
    assert run_job("expiry", NOW, capture=django_capture_on_commit_callbacks) == 0
    assert services.expire_idle_draft(Actor.system("r"), view.id, NOW) == "skipped"
    assert exists(view)


# --- candidates are paged: a poison pill cannot starve the rest ----------------------------------


def test_two_hundred_always_failing_candidates_do_not_block_a_good_one(
    monkeypatch, django_capture_on_commit_callbacks
):
    seed_cap(1000)
    account = make_account(random_email())
    views = [draft(account)[1] for _ in range(206)]
    ordered = sorted(v.id for v in views)
    poison, good = set(ordered[:205]), ordered[205:]
    real = services.notice_idle_draft

    def flaky(actor, listing_id, now):
        if listing_id in poison:
            raise RuntimeError("always fails")
        return real(actor, listing_id, now)

    monkeypatch.setattr(services, "notice_idle_draft", flaky)
    assert run_job("notice", capture=django_capture_on_commit_callbacks) == 1
    assert PropertyListing.objects.get(id=good[0]).expiry_notice_sent_at == NOW
    assert not PropertyListing.objects.filter(id__in=poison, expiry_notice_sent_at__isnull=False)


def test_an_always_skipped_expiry_candidate_does_not_block_later_ones(
    monkeypatch, django_capture_on_commit_callbacks
):
    monkeypatch.setattr(selectors, "RETENTION_BATCH", 2)
    seed_cap(100)
    account = make_account(random_email())
    views = sorted((draft(account)[1] for _ in range(5)), key=lambda v: v.id)
    run_job("notice", capture=django_capture_on_commit_callbacks)
    real = services.expire_idle_draft

    def flaky(actor, listing_id, now):
        if listing_id in {v.id for v in views[:3]}:
            raise RuntimeError("always fails")
        return real(actor, listing_id, now)

    monkeypatch.setattr(services, "expire_idle_draft", flaky)
    assert run_job("expiry", NOW + GRACE, capture=django_capture_on_commit_callbacks) == 2
    assert [exists(v) for v in views] == [True, True, True, False, False]


def test_the_pages_are_bounded(monkeypatch, django_capture_on_commit_callbacks):
    monkeypatch.setattr(selectors, "RETENTION_BATCH", 1)
    monkeypatch.setattr(selectors, "RETENTION_MAX_PAGES", 2)
    for _ in range(4):
        draft()
    assert run_job("notice", capture=django_capture_on_commit_callbacks) == 2
    assert run_job("notice", capture=django_capture_on_commit_callbacks) == 2


# --- the three rechecks, called directly ----------------------------------------------------------


def test_a_direct_notice_is_refused_when_already_noticed_not_idle_or_recently_opened(db):
    system = Actor.system("r")
    _, noticed = draft()
    PropertyListing.objects.filter(id=noticed.id).update(
        expiry_notice_sent_at=NOW - timedelta(days=1), delete_after=NOW + GRACE
    )
    assert services.notice_idle_draft(system, noticed.id, NOW) is False
    assert row(noticed).expiry_notice_sent_at == NOW - timedelta(days=1)
    _, opened = draft()
    PropertyListing.objects.filter(id=opened.id).update(last_opened_at=NOW - timedelta(days=3))
    assert services.notice_idle_draft(system, opened.id, NOW) is False  # an open is activity
    assert row(opened).expiry_notice_sent_at is None


def test_a_direct_expiry_is_skipped_when_not_yet_due(db):
    _, view = draft()
    store = Actor.system("r")
    PropertyListing.objects.filter(id=view.id).update(
        expiry_notice_sent_at=NOW, delete_after=NOW + GRACE
    )
    assert (
        services.expire_idle_draft(store, view.id, NOW + GRACE - timedelta(seconds=1)) == "skipped"
    )
    assert services.expire_idle_draft(store, view.id, NOW) == "skipped"
    assert exists(view)


def test_an_email_unverified_and_a_suspended_owner_do_record_an_open(db):
    for state in ("unverified", "suspended"):
        owner = make_account(random_email())
        view = services.create_draft(user(owner), *HOUSE)
        set_account_state(owner.id, state)
        assert services.record_open(user(owner), view.id, NOW) is True, state
        assert row(view).last_opened_at == NOW
