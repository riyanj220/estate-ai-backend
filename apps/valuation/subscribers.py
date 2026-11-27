"""Event subscribers of `valuation`."""

from apps.kernel.actor import Actor
from apps.listings.events import ListingDeleted, ListingModelInputsChanged, ListingPublished
from apps.valuation import retention, services

_SYSTEM = Actor.system("valuation")


def on_listing_published(event: ListingPublished) -> None:
    services.schedule_listing_estimate(_SYSTEM, event.listing_id)


def on_model_inputs_changed(event: ListingModelInputsChanged) -> None:
    services.schedule_listing_estimate(
        _SYSTEM, event.listing_id
    )  # a Draft is not public: nothing runs


def on_listing_deleted(event: ListingDeleted) -> None:
    retention.delete_for_listing(event.listing_id)
