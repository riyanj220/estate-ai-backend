"""Story 9.6: the advisory duplicate and suspicious flags."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from apps.governance import flags
from apps.governance.models import ListingFlag, Report
from apps.kernel.models import RateLimitPolicy
from apps.listings.models import ListingTerms, PropertyListing
from apps.listings.selectors import DuplicateCandidate
from tests.api.test_listings_api import signed_in
from tests.api.test_public_images_api import admin_client
from tests.listings.support import publish_ready, seed_cap, seed_media_policies

FLAGS = "/api/v1/admin/flags"


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    RateLimitPolicy.objects.update_or_create(
        scope="report_new",
        defaults={"limit": 10, "window_seconds": 86400, "key_kind": "account", "note": "t"},
    )


def candidate(**changes) -> DuplicateCandidate:
    values = dict(
        id=1,
        owner_account_id=1,
        purpose="sale",
        category="house",
        locality=1,
        area_sqft=Decimal("1125"),
        price=Decimal("1000000"),
        title="A bright house near the park",
    )
    return DuplicateCandidate(**{**values, **changes})


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, (True, True)),
        ({"price": Decimal("1020000"), "title": "Something else entirely"}, (True, False)),
        ({"price": Decimal("1100000"), "title": "A BRIGHT house, near the park!"}, (False, True)),
        ({"price": None, "title": None}, (False, False)),
        ({"owner_account_id": 2}, (False, False)),
        ({"locality": 2}, (False, False)),
        ({"area_sqft": Decimal("2250")}, (False, False)),
        ({"purpose": "rent"}, (False, False)),
    ],
)
def test_the_duplicate_rule(changes, expected):
    assert flags.match(candidate(), candidate(**changes)) == expected


def test_publishing_a_second_copy_flags_it_and_changes_nothing(
    django_capture_on_commit_callbacks,
):
    seller = signed_in("seller@example.com")
    with django_capture_on_commit_callbacks(execute=True):
        first = publish_ready(seller.account_id)
    assert not ListingFlag.objects.exists()
    other = signed_in("other@example.com")
    with django_capture_on_commit_callbacks(execute=True):
        publish_ready(other.account_id)  # the same home, another owner
    assert not ListingFlag.objects.exists()
    with django_capture_on_commit_callbacks(execute=True):
        second = publish_ready(seller.account_id)

    row = ListingFlag.objects.get()
    assert (row.kind, row.listing_id, row.other_listing_id) == ("duplicate", second, first)
    assert set(PropertyListing.objects.values_list("status", "restricted")) == {
        ("published", False)
    }
    page = admin_client().get(FLAGS).json()
    shown = page["results"][0]
    assert (shown["listing"]["id"], shown["other_listing"]["id"]) == (str(second), str(first))
    assert shown["matched"] == ["price", "title"] and shown["listing"]["title"]


def test_the_run_adds_once_and_removes_what_no_longer_holds():
    seller = signed_in("seller@example.com")
    first, second = publish_ready(seller.account_id), publish_ready(seller.account_id)
    call_command("run_flags")
    call_command("run_flags")
    assert ListingFlag.objects.count() == 1
    ListingTerms.objects.filter(listing_id=second).update(asking_price=Decimal("999999999"))
    PropertyListing.objects.filter(id=second).update(title="A completely different property")
    call_command("run_flags")
    assert not ListingFlag.objects.exists()
    assert PropertyListing.objects.get(id=first).status == "published"


def _report(listing_id, owner_id, reporter_id, days_ago, status="pending"):
    at = timezone.now() - timedelta(days=days_ago)
    return Report.objects.create(
        target_id=listing_id,
        reporter_account_id=reporter_id,
        subject_account_id=owner_id,
        reason="other",
        snapshot={"title": "t"},
        status=status,
        created_at=at,
        updated_at=at,
    )


def test_suspicious_needs_two_reporters_within_seven_days():
    seller = signed_in("seller@example.com")
    listing_id = publish_ready(seller.account_id)
    a, b = (signed_in(f"{n}@example.com").account_id for n in ("a", "b"))
    old = _report(listing_id, seller.account_id, a, 20)
    _report(listing_id, seller.account_id, b, 1)
    call_command("run_flags")
    assert not ListingFlag.objects.exists()  # too far apart
    Report.objects.filter(id=old.id).update(status="dismissed")
    _report(listing_id, seller.account_id, a, 1, status="dismissed")
    call_command("run_flags")
    assert not ListingFlag.objects.exists()  # not open
    third = _report(listing_id, seller.account_id, a, 3)
    call_command("run_flags")
    row = ListingFlag.objects.get()
    assert (row.kind, row.reporter_count) == ("suspicious", 2)
    assert PropertyListing.objects.get(id=listing_id).restricted is False
    admin = admin_client()
    assert admin.get(f"{FLAGS}?kind=duplicate").json()["results"] == []
    assert admin.get(f"{FLAGS}?kind=suspicious").json()["results"][0]["reporter_count"] == 2
    Report.objects.filter(id=third.id).update(status="resolved")
    call_command("run_flags")
    assert not ListingFlag.objects.exists()


def test_the_flag_list_is_administrator_only():
    seller = signed_in("seller@example.com")
    assert Client().get(FLAGS).status_code == 401
    assert seller.get(FLAGS).status_code == 404
    assert admin_client().get(f"{FLAGS}?kind=nonsense").status_code == 400
