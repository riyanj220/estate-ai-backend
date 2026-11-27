"""Listings' share of an account erasure (AD-23, FR-8). Statuses are written by their single
writers. Drafts are kept (retention skips a Deactivated owner) and nobody can reach them as an
owner; publication and moderation history is untouched.
"""

from apps.kernel import audit
from apps.kernel.actor import Actor, require_admin
from apps.listings import moderation, status
from apps.listings.models import ListingPrivateAddress, SavedListing


def erase_account_data(actor: Actor, account_id) -> None:
    """Archive all the account's Listings, delete its private addresses and its favourites."""
    require_admin(actor)
    status.archive_for_closed_account(actor, account_id)
    moderation.archive_removed_of(actor, account_id)
    ListingPrivateAddress.objects.filter(listing__owner_account_id=account_id).delete()
    SavedListing.objects.filter(account_id=account_id).delete()
    audit.record(actor, "listings.account_erased", "account", account_id)
