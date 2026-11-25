"""The ONE visibility policy of Listings (AD-5): who may see which Listing.

Two implementations share one predicate and nothing else decides visibility:

- `public_q()` / `selectors.public_listings()`: the rows anyone may see (lists, search, counts).
- `can_view(viewer, listing)`: the per-object answer (detail, media, favourites, chat).

A Listing is public iff it is Published AND not restricted AND its owner is an Active eligible
owner (`identity.selectors.is_active_owner`). Besides that, the owning account sees its own
Listing in any status (not when Deactivated). Everyone else, an administrator included, gets
`False` for a non-public Listing; callers answer that with `not_found`, never `forbidden`.
"""

from django.db.models import Q

from apps.identity import selectors as identity
from apps.identity.capabilities import Capability
from apps.identity.policy import can
from apps.kernel.actor import Actor, ActorKind
from apps.listings.lifecycle import ListingStatus
from apps.listings.models import PropertyListing


def public_q() -> Q:
    """The single predicate of public visibility, as a filter on `PropertyListing`."""
    return Q(
        status=ListingStatus.PUBLISHED.value,
        restricted=False,
        owner_account_id__in=identity.active_owner_account_ids(),
    )


def is_public(listing: PropertyListing) -> bool:
    """Whether the Listing is public right now: the same predicate as `public_listings()`,
    evaluated against the current rows (never against a possibly stale instance)."""
    return PropertyListing.objects.filter(public_q(), id=listing.id).exists()


# The statuses an owner may see their own Listing in. Explicit on purpose: a status added later
# is invisible to everyone, the owner included, until its story adds it here deliberately. Since
# Story 2.17 that is every status, restricted and removed ones included (read only for the owner).
OWNER_VISIBLE_STATUSES = frozenset(
    {
        ListingStatus.DRAFT.value,
        ListingStatus.PUBLISHED.value,
        ListingStatus.ARCHIVED.value,
        ListingStatus.SOLD.value,
        ListingStatus.RENTED.value,
        ListingStatus.REMOVED.value,
    }
)


def is_owner(viewer: Actor, listing: PropertyListing) -> bool:
    """The owning account itself, for a status the owner may see: a signed-in, non-administrator
    account actor that may view its drafts (Suspended and Email-unverified owners may; a
    Deactivated one may not)."""
    return (
        listing.status in OWNER_VISIBLE_STATUSES
        and viewer.kind is ActorKind.USER
        and listing.owner_account_id == viewer.account_id
        and can(viewer, Capability.VIEW_DRAFTS)
    )


def public_ids(listing_ids) -> set:
    """Which of these Listing ids are public right now (one query, the same predicate)."""
    return set(
        PropertyListing.objects.filter(public_q(), id__in=list(listing_ids)).values_list(
            "id", flat=True
        )
    )


def can_view(viewer: Actor, listing: PropertyListing) -> bool:
    """May `viewer` see `listing`? Public Listings: everyone. Others: only the owner."""
    return is_public(listing) or is_owner(viewer, listing)
