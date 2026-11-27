"""Administrator moderation writes on a Listing (Story 9.4, AD-3).

The ONLY code that sets `restricted`, `restriction_reason`, `removal_reason` and the `removed`
status, and the only one that archives a Removed Listing (architecture guards pin this).
Restoring to Published belongs to `publication.restore_after_moderation` (AD-7). Callers go
through `governance.moderation`, which records the decision and notifies the owner.

Each function runs one transaction: the owner account is locked, then the Listing row; the action
must be in `lifecycle.moderation_actions` (`conflict` otherwise); the revision is bumped (a stale
owner edit is refused) and the audit record written. No quota is read or used.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.identity.selectors import lock_account
from apps.kernel import audit
from apps.kernel.actor import Actor, require_admin
from apps.kernel.errors import DomainError
from apps.listings import lifecycle
from apps.listings.lifecycle import ListingStatus, Origin
from apps.listings.models import ListingRevision, PropertyListing
from apps.listings.selectors import canonical_uuid


@dataclass(frozen=True)
class Moderated:
    """What a moderation action did."""

    listing_id: uuid.UUID
    owner_account_id: uuid.UUID
    status_before: str
    status_after: str
    restriction_in_force: bool


def locked_for_moderation(actor: Actor, listing_id, action: str) -> PropertyListing:
    """Inside a transaction: the owner account, then the Listing, locked and open to `action`."""
    require_admin(actor)
    key = canonical_uuid(listing_id)
    owner = (
        PropertyListing.objects.filter(id=key).values_list("owner_account_id", flat=True).first()
        if key is not None
        else None
    )
    if owner is None:
        raise DomainError("not_found")
    lock_account(owner)
    row = PropertyListing.objects.select_for_update().filter(id=key).first()
    if row is None:
        raise DomainError("not_found")
    if not lifecycle.moderation_allowed(row, action):
        raise DomainError("conflict")
    return row


def _apply(actor: Actor, listing_id, action: str, audit_action: str, change: Callable) -> Moderated:
    require_admin(actor)
    with transaction.atomic():
        row = locked_for_moderation(actor, listing_id, action)
        before = row.status
        now = timezone.now()
        fields, changes = change(row)
        row.revision += 1
        row.updated_at = now
        row.save(update_fields=[*fields, "revision", "updated_at"])
        ListingRevision.objects.create(
            listing=row,
            revision=row.revision,
            origin=Origin.MANUAL.value,
            actor_account_id=actor.account_id,
            changes=changes,
            created_at=now,
        )
        audit.record(actor, audit_action, "listing", row.id)
    return Moderated(row.id, row.owner_account_id, before, row.status, row.restricted)


def restrict(actor: Actor, listing_id, reason: str) -> Moderated:
    """Hide a Published or Archived Listing until the restriction is lifted."""

    def change(row):
        row.restricted = True
        row.restriction_reason = reason
        return ["restricted", "restriction_reason"], {"restricted": True}

    return _apply(actor, listing_id, lifecycle.RESTRICT, "listing.restricted", change)


def lift_restriction(actor: Actor, listing_id) -> Moderated:
    """Visibility only: the status stays what it is."""

    def change(row):
        row.restricted = False
        row.restriction_reason = None
        return ["restricted", "restriction_reason"], {"restricted": False}

    return _apply(actor, listing_id, lifecycle.LIFT, "listing.restriction_lifted", change)


def remove(actor: Actor, listing_id, reason: str) -> Moderated:
    """Published to Removed."""

    def change(row):
        row.status = ListingStatus.REMOVED.value
        row.removal_reason = reason
        return ["status", "removal_reason"], {"status": ListingStatus.REMOVED.value}

    return _apply(actor, listing_id, lifecycle.REMOVE, "listing.removed", change)


def archive_removed(actor: Actor, listing_id) -> Moderated:
    """Removed to Archived; a restriction stays in force."""

    def change(row):
        row.status = ListingStatus.ARCHIVED.value
        row.removal_reason = None
        return ["status", "removal_reason"], {"status": ListingStatus.ARCHIVED.value}

    return _apply(actor, listing_id, lifecycle.ARCHIVE_REMOVED, "listing.removal_archived", change)
