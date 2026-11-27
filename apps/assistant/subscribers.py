"""Event subscribers of `assistant`."""

from apps.assistant import retention
from apps.listings.events import ListingDeleted


def on_listing_deleted(event: ListingDeleted) -> None:
    retention.delete_for_listing(event.listing_id)
