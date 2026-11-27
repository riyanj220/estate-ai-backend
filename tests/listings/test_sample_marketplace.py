"""Story 11.1: the full sample marketplace (`apps.governance.sample_data`): the activity around
the sample Listings, its safety to repeat, and its complete removal."""

import uuid

import pytest
from django.utils import timezone

from apps.governance import sample_data as marketplace
from apps.governance.models import (
    ClosureRequest,
    ListingFlag,
    ModerationAction,
    Report,
    ReportTransition,
    SupportRequest,
    VerificationDecision,
    VerificationRequest,
)
from apps.identity import selectors as identity
from apps.identity.models import Account, AgencyProfile
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import MediaAsset, Notification, StorageUsage
from apps.listings import quota, sample_data, services
from apps.listings.models import (
    ListingViewCount,
    PropertyListing,
    PublicationEvent,
    SavedListing,
)
from apps.messaging import sample_data as sample_conversations
from apps.messaging import services as messaging
from apps.messaging.models import Conversation, Message
from tests.listings.support import PASSWORD, make_account, ready_draft, seed_cap, user

RECORDS = (
    PropertyListing,
    Conversation,
    Message,
    Report,
    ReportTransition,
    ModerationAction,
    ListingFlag,
    VerificationRequest,
    VerificationDecision,
    SupportRequest,
    ClosureRequest,
    SavedListing,
    ListingViewCount,
    Notification,
    MediaAsset,
)
LISTINGS = sample_data.DEFAULT_LISTINGS


@pytest.fixture(autouse=True)
def setup(db):
    seed_cap()


def counts() -> dict:
    return {model.__name__: model.objects.count() for model in RECORDS}


def values(model, field: str) -> set:
    return set(model.objects.values_list(field, flat=True))


def test_the_marketplace_fills_every_screen_repeats_safely_and_is_removed_completely(
    django_capture_on_commit_callbacks,
):
    # a real account with its own Listing, saved by nobody: it must come through untouched
    real = make_account("real.person@example.com")
    mine = ready_draft(real)

    with django_capture_on_commit_callbacks(execute=True):
        report = marketplace.seed(LISTINGS, PASSWORD)
    assert report.listings_created == LISTINGS

    sample = PropertyListing.objects.exclude(id=mine.id)
    assert values(PropertyListing, "status") == {
        "draft",
        "published",
        "archived",
        "sold",
        "rented",
        "removed",
    }
    assert sample.filter(restricted=True).exists()
    assert {r.category for r in sample} == {"house", "apartment", "plot", "commercial"}
    assert {r.purpose for r in sample} == {"sale", "rent"}
    assert values(VerificationRequest, "status") == {"verified", "pending", "rejected", "revoked"}
    assert AgencyProfile.objects.filter(verification_status="verified").count() == 2
    assert values(Report, "target_type") == {"listing", "conversation"}
    assert values(Report, "status") == {"pending", "reviewing", "resolved", "dismissed"}
    assert values(ListingFlag, "kind") == {"duplicate", "suspicious"}
    assert values(SupportRequest, "status") == {"open", "closed"}
    assert values(ClosureRequest, "status") == {"pending"}
    assert Account.objects.filter(suspended_at__isnull=False).count() == 1
    assert SavedListing.objects.exists() and ListingViewCount.objects.exists()
    unread = [c for c in Conversation.objects.all() if c.last_seq > c.owner_read_seq]
    assert unread and len(unread) < Conversation.objects.count()
    assert Notification.objects.filter(read_at__isnull=True).exists()
    events = PublicationEvent.objects.all()
    assert events.count() == LISTINGS and values(PublicationEvent, "kind") == {"seed"}
    for account in Account.objects.exclude(id=real.id):
        assert quota.allowance_for(account.id, account.account_type, timezone.now()).used == 0

    before = counts()
    with django_capture_on_commit_callbacks(execute=True):
        again = marketplace.seed(LISTINGS)
    assert (again.listings_created, again.activity) == (0, {})
    assert counts() == before

    preview = marketplace.preview_reset()
    assert preview.activity["conversations"] == Conversation.objects.count()
    assert counts() == before
    with django_capture_on_commit_callbacks(execute=True):
        removed = marketplace.reset()
    assert removed.activity == preview.activity
    left = {name for name, count in counts().items() if count}
    assert left <= {"PropertyListing", "MediaAsset"}  # the real account's Draft and its pictures
    assert [p.id for p in PropertyListing.objects.all()] == [mine.id]
    assert {m.owner_account_id for m in MediaAsset.objects.all()} <= {real.id}
    assert [a.id for a in Account.objects.all()] == [real.id]
    assert {u.account_id for u in StorageUsage.objects.all()} <= {real.id}


def test_records_of_a_real_account_that_involve_no_sample_account_survive_a_reset():
    seller, buyer = make_account("a.person@example.com"), make_account("b.person@example.com")
    theirs = ready_draft(seller)
    PropertyListing.objects.filter(id=theirs.id).update(status="published")
    messaging.start_conversation(user(buyer), theirs.id, "Is it available?", str(uuid.uuid4()))
    services.save_favourite(user(buyer), theirs.id)
    marketplace.seed(3, PASSWORD)
    marketplace.reset()
    assert Conversation.objects.count() == 1 and SavedListing.objects.count() == 1
    assert identity.sample_account_ids() == {}


def test_a_small_run_works_and_reseeds():
    marketplace.seed(1, PASSWORD)
    report = marketplace.reseed(1, PASSWORD)
    assert (report.listings_created, report.accounts_created) == (1, len(sample_data.ACCOUNTS))


def test_the_removal_services_are_system_only_and_sample_only():
    real = make_account("real.person@example.com")
    with pytest.raises(DomainError):
        sample_conversations.remove_conversations(user(real), [real.id])
    with pytest.raises(DomainError):
        services.remove_sample_favourites(user(real), real.id)
    system = Actor.system("t")
    assert sample_conversations.remove_conversations(system, [real.id]) == 0
    assert services.remove_sample_favourites(system, real.id) == 0


@pytest.mark.parametrize("call", ["seed", "reset", "reseed"])
def test_the_marketplace_refuses_under_the_production_settings(call, settings):
    settings.SETTINGS_MODULE = "config.settings.prod"
    with pytest.raises(marketplace.SeedRefused, match="production"):
        getattr(marketplace, call)()
    assert Account.objects.count() == 0
