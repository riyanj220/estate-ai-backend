"""Domain events emitted by `listings` (AD-18). Subscribers register in their own app's `ready`."""

import uuid
from dataclasses import dataclass

from apps.kernel.events import DomainEvent


@dataclass(frozen=True)
class ListingModelInputsChanged(DomainEvent):
    """A field flagged `valuation_input` in the registry changed value (derived, repairable)."""

    name = "listing.model_inputs_changed"
    listing_id: uuid.UUID


@dataclass(frozen=True)
class ListingPublished(DomainEvent):
    """A Draft became Published (after commit). Ids only; nothing subscribes yet."""

    name = "listing.published"
    listing_id: uuid.UUID
    account_id: uuid.UUID


@dataclass(frozen=True)
class ListingStatusChanged(DomainEvent):
    """A Published (or Sold or Rented) Listing was archived, sold or rented (after commit).
    Ids and the new status only; nothing subscribes yet."""

    name = "listing.status_changed"
    listing_id: uuid.UUID
    account_id: uuid.UUID
    status: str
