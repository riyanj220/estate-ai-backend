"""Administrator quota policy management (Story 2.20): service rules."""

import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection

from apps.identity import selectors as identity_selectors
from apps.identity import services as identity_services
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord
from apps.listings import quota, quota_admin, selectors, services
from apps.listings.models import ListingQuotaPolicy, PublicationEvent
from tests.listings.support import PASSWORD, SYSTEM, make_account, user

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
MONTH_START = datetime(2026, 10, 31, 19, 0, tzinfo=UTC)  # 2026-11-01 00:00 Karachi
SECOND = timedelta(seconds=1)


def make_admin() -> Actor:
    view = identity_services.provision_admin(SYSTEM, "root@example.com", PASSWORD + " 42")
    return Actor.admin(view.id, "req")


def event(account_id, created_at, kind="initial", counted=True):
    PublicationEvent.objects.create(
        account_id=account_id,
        listing_id=uuid.uuid4(),
        kind=kind,
        counted=counted,
        created_at=created_at,
    )


def audits():
    return AuditRecord.objects.filter(action="quota.policy_changed")


def seed_again():
    """A transactional test may have flushed the seed rows."""
    for account_type, limit in (("individual", 2), ("agency", 10)):
        ListingQuotaPolicy.objects.get_or_create(
            account_type=account_type,
            effective_to=None,
            defaults={"monthly_limit": limit, "effective_from": datetime(2000, 1, 1, tzinfo=UTC)},
        )


def test_current_policies_are_the_rows_in_force(db):
    admin = make_admin()
    views = quota_admin.current_policies(admin)
    assert [(v.account_type, v.monthly_limit) for v in views] == [
        ("individual", 2),
        ("agency", 10),
    ]
    assert views[0].effective_from.utcoffset() == timedelta(hours=5)


def test_a_change_is_effective_dated_audited_and_leaves_history(db):
    admin = make_admin()
    account = make_account()
    event(account.id, NOW - timedelta(days=1))
    old = ListingQuotaPolicy.objects.get(account_type="individual")
    view = quota_admin.set_quota_limit(admin, "individual", 5, now=NOW)
    assert (view.account_type, view.monthly_limit, view.effective_from) == ("individual", 5, NOW)

    old.refresh_from_db()
    assert (old.monthly_limit, old.effective_to) == (2, NOW)
    new = ListingQuotaPolicy.objects.get(account_type="individual", effective_to=None)
    assert (new.monthly_limit, new.effective_from) == (5, NOW)
    assert quota.quota_limit_at("individual", NOW - SECOND) == 2
    assert quota.quota_limit_at("individual", NOW) == 5
    assert quota.allowance_for(account.id, "individual", NOW - SECOND).remaining == 1
    assert quota.allowance_for(account.id, "individual", NOW).remaining == 4
    assert quota.quota_limit_at("agency", NOW) == 10

    record = audits().get()
    assert (record.actor_id, record.target_type, record.target_id) == (
        admin.account_id,
        "quota_policy",
        new.id,
    )
    assert record.metadata == {"account_type": "individual", "old_limit": 2, "new_limit": 5}


def test_the_same_limit_changes_nothing(db):
    admin = make_admin()
    view = quota_admin.set_quota_limit(admin, "agency", 10, now=NOW)
    assert view.effective_from == datetime(2000, 1, 1, tzinfo=UTC)
    assert ListingQuotaPolicy.objects.count() == 2
    assert not audits().exists()


@pytest.mark.parametrize("value", [0, 1000])
def test_the_bounds_are_allowed(db, value):
    assert quota_admin.set_quota_limit(make_admin(), "agency", value).monthly_limit == value


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (None, "required"),
        (True, "invalid"),
        (2.0, "invalid"),
        ("3", "invalid"),
        (-1, "min_value"),
        (1001, "max_value"),
    ],
)
def test_invalid_limits_write_nothing(db, value, code):
    with pytest.raises(services.FieldsInvalid) as caught:
        quota_admin.set_quota_limit(make_admin(), "individual", value)
    assert caught.value.fields == {"monthly_limit": code}
    assert ListingQuotaPolicy.objects.count() == 2
    assert not audits().exists()


def test_an_unknown_account_type_and_an_earlier_clock_are_refused(db):
    admin = make_admin()
    with pytest.raises(DomainError) as caught:
        quota_admin.set_quota_limit(admin, "admin", 3)
    assert caught.value.code == "not_found"
    with pytest.raises(DomainError) as caught:
        quota_admin.set_quota_limit(admin, "individual", 3, now=datetime(1999, 1, 1, tzinfo=UTC))
    assert caught.value.code == "conflict"
    assert ListingQuotaPolicy.objects.count() == 2


def test_only_an_administrator_may_read_or_change(db):
    account = make_account()
    refusals = [
        (Actor.visitor("r"), "unauthenticated"),
        (user(account), "not_found"),
        (SYSTEM, "not_found"),
        (Actor.admin(uuid.uuid4()), "unauthenticated"),
        (Actor.admin(account.id), "unauthenticated"),
    ]
    calls = [
        lambda actor: quota_admin.current_policies(actor),
        lambda actor: quota_admin.set_quota_limit(actor, "individual", 9),
        lambda actor: quota_admin.usage_by_account(actor),
    ]
    for actor, code in refusals:
        for call in calls:
            with pytest.raises(DomainError) as caught:
                call(actor)
            assert caught.value.code == code
    assert ListingQuotaPolicy.objects.count() == 2


@pytest.mark.parametrize(
    ("email", "masked"),
    [
        ("jane.doe@example.com", "j***@example.com"),
        ("ab@example.com", "a***@example.com"),
        ("a@example.com", "***@example.com"),
    ],
)
def test_the_email_mask(email, masked):
    assert identity_selectors.mask_email(email) == masked


def test_usage_counts_only_counted_events_of_the_karachi_month(db):
    admin = make_admin()
    one = make_account("one@example.com")
    two = make_account("two@example.com", "agency", "suspended")
    none = make_account("none@example.com")
    event(one.id, MONTH_START)
    event(one.id, NOW - timedelta(days=2), kind="republish_counted")
    event(one.id, NOW, kind="republish_free", counted=False)  # not counted, not "newest usage"
    event(two.id, NOW - timedelta(days=1))
    event(two.id, MONTH_START - SECOND)  # last month
    event(two.id, datetime(2026, 11, 30, 19, 0, tzinfo=UTC))  # next month
    event(none.id, MONTH_START - SECOND)
    event(none.id, NOW, kind="seed", counted=False)
    event(uuid.uuid4(), NOW)  # no such account

    page = quota_admin.usage_by_account(admin, now=NOW)
    assert [(r.email, r.account_type, r.used, r.limit) for r in page.items] == [
        ("t***@example.com", "agency", 1, 10),
        ("o***@example.com", "individual", 2, 2),
    ]
    assert page.next_cursor is None
    assert (page.period_start, page.resets_at) == quota.month_bounds(NOW)


def test_usage_pages_without_gaps_or_repeats_even_with_equal_times(db):
    admin = make_admin()
    accounts = [make_account(f"a{n}@example.com") for n in range(5)]
    for n, account in enumerate(accounts):
        event(account.id, NOW - timedelta(hours=n // 3))  # two groups of equal times
    seen, cursor = [], None
    while True:
        page = quota_admin.usage_by_account(admin, cursor, limit=2, now=NOW)
        seen += [row.account_id for row in page.items]
        cursor = page.next_cursor
        if cursor is None:
            break
    expected = sorted((a.id for a in accounts[:3]), reverse=True) + sorted(
        (a.id for a in accounts[3:]), reverse=True
    )
    assert seen == expected

    first = quota_admin.usage_by_account(admin, limit=2, now=NOW).next_cursor
    for bad, when in (("nonsense", NOW), ("!", NOW), (first, NOW + timedelta(days=31))):
        with pytest.raises(selectors.InvalidCursor):
            quota_admin.usage_by_account(admin, bad, now=when)


@pytest.mark.django_db(transaction=True)
def test_concurrent_changes_serialise_to_one_open_row():
    seed_again()
    admin = make_admin()
    count = 6
    barrier, errors = threading.Barrier(count), []

    def work(limit):
        try:
            barrier.wait(timeout=30)
            quota_admin.set_quota_limit(admin, "individual", limit)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))
        finally:
            connection.close()

    threads = [threading.Thread(target=work, args=(20 + n,)) for n in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors and not any(thread.is_alive() for thread in threads)

    rows = list(
        ListingQuotaPolicy.objects.filter(account_type="individual").order_by("effective_from")
    )
    assert len(rows) == count + 1
    assert [r.effective_to for r in rows] == [r.effective_from for r in rows[1:]] + [None]
    chain = [(a.metadata["old_limit"], a.metadata["new_limit"]) for a in audits()]
    limits = [r.monthly_limit for r in rows]
    assert sorted(chain) == sorted(zip(limits, limits[1:], strict=False))
