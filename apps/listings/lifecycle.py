"""Code-defined Listing enums. Later stories extend them (Stories 2.12, 2.15, 2.17)."""

from enum import StrEnum


class ListingStatus(StrEnum):
    DRAFT = "draft"


class Visibility(StrEnum):
    PRIVATE = "private"


class Origin(StrEnum):
    MANUAL = "manual"
    AI = "ai"  # written by the Assistant call path (Epic 3); never chosen by a client


# Statuses whose fields `listings.services.apply_updates` may change. Story 2.15 (publication
# and editing of Published Listings) extends this set deliberately; a Listing in any other
# status is refused with `forbidden`.
EDITABLE_STATUSES = frozenset({ListingStatus.DRAFT.value})


def visibility_of(status: ListingStatus) -> Visibility:
    """Drafts are private; Story 2.12 adds the public states."""
    return Visibility.PRIVATE
