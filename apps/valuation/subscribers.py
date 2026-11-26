"""Event subscribers of `valuation`."""

from apps.listings.events import ListingDeleted
from apps.valuation import retention


def on_listing_deleted(event: ListingDeleted) -> None:
    retention.delete_for_listing(event.listing_id)
