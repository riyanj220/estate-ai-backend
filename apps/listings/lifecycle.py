"""Code-defined Listing enums. Later stories extend them (Stories 2.15, 2.17)."""

from enum import StrEnum


class ListingStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"  # set by the publication story (2.15); fixtures only until then


class Visibility(StrEnum):
    PRIVATE = "private"
    PUBLIC = "public"


class Origin(StrEnum):
    MANUAL = "manual"
    AI = "ai"  # written by the Assistant call path (Epic 3); never chosen by a client


# Statuses whose fields `listings.services.apply_updates` may change. Story 2.15 (publication
# and editing of Published Listings) extends this set deliberately; a Listing in any other
# status is refused with `forbidden`.
EDITABLE_STATUSES = frozenset({ListingStatus.DRAFT.value})


def visibility_of(status: ListingStatus, *, public: bool) -> Visibility:
    """The owner-facing `visibility` label: `public` only for a Listing that IS public right now
    (`public` comes from `listings.policy`, which also looks at `restricted` and the owner's
    account state), else `private`. This function decides nothing about who may see what."""
    return Visibility.PUBLIC if public and status is ListingStatus.PUBLISHED else Visibility.PRIVATE
