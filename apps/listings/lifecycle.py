"""Code-defined Listing enums. Later stories extend them (Story 2.17)."""

from enum import StrEnum


class ListingStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"  # set only by `listings.publication.publish` (AD-7)
    # Set by `listings.status.change_status` (archived, sold, rented, by the owner). `removed` is
    # written only by `listings.moderation` (an administrator), which may also archive it.
    ARCHIVED = "archived"
    SOLD = "sold"
    RENTED = "rented"
    REMOVED = "removed"


class Visibility(StrEnum):
    PRIVATE = "private"
    PUBLIC = "public"


class Origin(StrEnum):
    MANUAL = "manual"
    AI = "ai"  # written by the Assistant call path (Epic 3); never chosen by a client


# Statuses whose fields and images the owner may change (Story 2.17). A Listing in any other
# status (sold, rented, removed) is refused with `forbidden`: view only. A restricted Listing is
# refused whatever its status (`PropertyListing.restricted`).
EDITABLE_STATUSES = frozenset(
    {ListingStatus.DRAFT.value, ListingStatus.PUBLISHED.value, ListingStatus.ARCHIVED.value}
)


def visibility_of(status: ListingStatus, *, public: bool) -> Visibility:
    """The owner-facing `visibility` label: `public` only for a Listing that IS public right now
    (`public` comes from `listings.policy`, which also looks at `restricted` and the owner's
    account state), else `private`. This function decides nothing about who may see what."""
    return Visibility.PUBLIC if public and status is ListingStatus.PUBLISHED else Visibility.PRIVATE


def owner_may_edit(row) -> bool:
    """May the owner change this Listing's fields and images? Its status must be editable and it
    must not be restricted (a restriction, set by moderation, freezes it whatever its status).
    Takes the Listing row; the status and restriction names stay in this module (AD-5 guard)."""
    return row.status in EDITABLE_STATUSES and not row.restricted


def is_published(row) -> bool:
    """Whether the Listing is in the Published status: it is live, so its identity fields are
    locked, an edit must leave it complete and it keeps at least one image (Story 2.17)."""
    return row.status == ListingStatus.PUBLISHED.value


_P, _A, _S, _R = (
    ListingStatus.PUBLISHED.value,
    ListingStatus.ARCHIVED.value,
    ListingStatus.SOLD.value,
    ListingStatus.RENTED.value,
)

# source status -> the statuses the owner may move it to (`listings.status.change_status`).
# Everything else is `conflict`: a Draft, an Archived or a Removed Listing has no move, a move to
# `published` or `removed` never exists here (publication and, later, moderation own those), and
# a move to the same status is refused.
TRANSITIONS: dict[str, frozenset[str]] = {
    _P: frozenset({_A, _S, _R}),
    _S: frozenset({_A}),
    _R: frozenset({_A}),
}
# `sold` is only valid for purpose `sale`, `rented` only for purpose `rent` (the registry's two
# purposes); archive has no purpose rule.
OUTCOME_PURPOSE = {_S: "sale", _R: "rent"}
MOVE_SOURCES = tuple(TRANSITIONS)  # for the audit schema: where a move may start
MOVE_TARGETS = (_A, _S, _R)  # what a client may ask for, and the audit schema's `to`


def move_refusal(row, target: str) -> str | None:
    """The code with which the owner is refused moving this Listing to `target`, or None when
    the move is allowed (account state is decided by the caller). A Removed Listing is
    moderation's (`forbidden`); a restricted one may only be archived (`forbidden`); a move
    outside `TRANSITIONS` is `conflict`."""
    if row.status == ListingStatus.REMOVED.value:
        return "forbidden"
    if row.restricted and target != ListingStatus.ARCHIVED.value:
        return "forbidden"
    if target not in TRANSITIONS.get(row.status, frozenset()):
        return "conflict"
    wanted = OUTCOME_PURPOSE.get(target)
    if wanted is not None and row.purpose != wanted:
        return "conflict"  # a sale is sold, a rental is rented; never the other way round
    return None


# --- moderation (Story 9.4, the administrator column of FR-75) ------------------------------------

RESTRICT, LIFT, REMOVE = "restrict", "lift_restriction", "remove"
RESTORE, ARCHIVE_REMOVED = "restore", "archive_removed"
MODERATION_ACTIONS = (RESTRICT, REMOVE, LIFT, RESTORE, ARCHIVE_REMOVED)


def moderation_actions(row) -> tuple[str, ...]:
    """What an administrator may do to this Listing now. Drafts: nothing (an account is suspended
    instead). Sold and rented: view only. A restriction persists across status changes and is
    lifted only by `lift_restriction`, never by restoring or archiving."""
    flag = (LIFT,) if row.restricted else (RESTRICT,)
    if row.status == ListingStatus.PUBLISHED.value:
        return (*flag, REMOVE)
    if row.status == ListingStatus.ARCHIVED.value:
        return flag
    if row.status == ListingStatus.REMOVED.value:
        return (RESTORE, ARCHIVE_REMOVED, *((LIFT,) if row.restricted else ()))
    return ()


def moderation_allowed(row, action: str) -> bool:
    return action in moderation_actions(row)


def shown_reason(row) -> str | None:
    """The moderation reason the owner sees: why it was removed while it is Removed, else why it
    is restricted."""
    if row.status == ListingStatus.REMOVED.value and row.removal_reason:
        return row.removal_reason
    return row.restriction_reason
