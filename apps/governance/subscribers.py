"""Event subscribers of `governance`."""

from apps.governance import flags, reports
from apps.identity.events import AccountSessionEnded
from apps.listings.events import ListingDeleted, ListingPublished


def on_session_ended(event: AccountSessionEnded) -> None:
    if event.reason == "deactivated":
        reports.anonymise_participant(event.account_id)


def on_listing_published(event: ListingPublished) -> None:
    flags.check_duplicates(event.listing_id)


def on_listing_deleted(event: ListingDeleted) -> None:
    flags.delete_for_listing(event.listing_id)
