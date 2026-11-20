"""Code-defined Listing enums. Later stories extend them (Stories 2.12, 2.15, 2.17)."""

from enum import StrEnum


class ListingStatus(StrEnum):
    DRAFT = "draft"


class Visibility(StrEnum):
    PRIVATE = "private"


class Origin(StrEnum):
    MANUAL = "manual"


def visibility_of(status: ListingStatus) -> Visibility:
    """Drafts are private; Story 2.12 adds the public states."""
    return Visibility.PRIVATE
