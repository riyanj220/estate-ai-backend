"""Restrict, remove, restore and archive Listings (Story 9.4): the administrator's decision,
its record and the owner's notification, in one transaction. The Listing itself is written by
`listings.moderation` and `listings.publication`. Reports are untouched.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from apps.governance.models import ModerationAction
from apps.governance.services import FieldsInvalid
from apps.kernel import notifications
from apps.kernel.actor import Actor, require_admin
from apps.kernel.privacy.patterns import LONG_DIGIT_RUN, reject_contact_details
from apps.listings import moderation as listing_moderation
from apps.listings import publication

REASON_MIN, REASON_MAX = 5, notifications.SHORT_TEXT_MAX

# action -> (how the Listing is changed, the notification the owner gets)
_ACTIONS = {
    "restrict": (listing_moderation.restrict, "listing.restricted"),
    "remove": (listing_moderation.remove, "listing.removed"),
    "lift_restriction": (listing_moderation.lift_restriction, "listing.restriction_lifted"),
    "restore": (publication.restore_after_moderation, "listing.restored"),
    "archive_removed": (listing_moderation.archive_removed, "listing.removal_archived"),
}
_TAKE_REASON = frozenset({"restrict", "remove"})  # the reason is also stored on the Listing
NOTIFICATION_KINDS = tuple(kind for _, kind in _ACTIONS.values())


@dataclass(frozen=True)
class ActionRecord:
    action: str
    reason: str
    status_before: str
    status_after: str
    restriction_after: bool
    at: datetime
    by_account_id: uuid.UUID


def _clean_reason(reason) -> str:
    """One line the owner will read, in the notification too: 5 to 120 characters, no contact
    details or links."""
    if not isinstance(reason, str) or not " ".join(reason.split()):
        raise FieldsInvalid({"reason": "required"})
    value = " ".join(reason.split())
    if len(value) < REASON_MIN:
        raise FieldsInvalid({"reason": "too_short"})
    if len(value) > REASON_MAX:
        raise FieldsInvalid({"reason": "too_long"})
    if not value.isprintable():
        raise FieldsInvalid({"reason": "invalid"})
    lowered = value.lower()
    if (
        LONG_DIGIT_RUN.search(value)
        or reject_contact_details(value) is not None
        or any(hint in lowered for hint in ("@", "://", "www."))
        or sum(c.isdigit() for c in value) > notifications.MAX_DIGITS
    ):
        raise FieldsInvalid({"reason": "contains_contact_details"})
    return value


def _record(row: ModerationAction) -> ActionRecord:
    return ActionRecord(
        row.action,
        row.reason,
        row.status_before,
        row.status_after,
        row.restriction_after,
        row.created_at,
        row.actor_account_id,
    )


def moderate(actor: Actor, listing_id, action: str, reason) -> ActionRecord:
    """Apply one action. Visitor `unauthenticated`; non-administrator or unknown Listing
    `not_found`; bad reason `FieldsInvalid`; not open for this Listing now `conflict`."""
    require_admin(actor)
    change, kind = _ACTIONS[action]
    reason = _clean_reason(reason)
    with transaction.atomic():
        if action in _TAKE_REASON:
            result = change(actor, listing_id, reason)
        else:
            result = change(actor, listing_id)
        row = ModerationAction.objects.create(
            listing_id=result.listing_id,
            action=action,
            actor_account_id=actor.account_id,
            reason=reason,
            status_before=result.status_before,
            status_after=result.status_after,
            restriction_after=result.restriction_in_force,
            created_at=timezone.now(),
        )
        notifications.notify(  # last
            result.owner_account_id, kind, {"listing_id": result.listing_id, "reason": reason}
        )
    return _record(row)


def history(actor: Actor, listing_id) -> list[ActionRecord]:
    require_admin(actor)
    rows = ModerationAction.objects.filter(listing_id=listing_id).order_by("-created_at", "-id")
    return [_record(row) for row in rows]
