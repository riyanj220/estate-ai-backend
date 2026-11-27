"""Moving a Listing out of Published: archive, mark sold, mark rented (Story 2.17).

`change_status` is the only code that writes `archived`, `sold` and `rented` (the AST guard in
`tests/architecture/test_publication_single_writer.py` pins that this module may write exactly
those three and never `published` or `removed`). It consumes no quota, writes no
`PublicationEvent` and needs no idempotency key: a repeat is a clean `409 conflict`.

One transaction, in the usual lock order: the account, then the Listing row. Inside it the
revision is compared, the refusals are decided from the locked rows, the status and the revision
are written (with a revision row that records the status change and no content), the audit record
is written and `listing.status_changed` is emitted last (after commit).
"""

from django.db import transaction
from django.utils import timezone

from apps.identity.capabilities import Access, Capability, matrix_access
from apps.identity.policy import can
from apps.identity.selectors import ADMIN_ACCOUNT_TYPE, lock_account
from apps.kernel import audit
from apps.kernel.actor import Actor, ActorKind, require_admin, require_signed_in
from apps.kernel.errors import DomainError
from apps.kernel.events import emit
from apps.listings import lifecycle
from apps.listings.events import ListingStatusChanged
from apps.listings.lifecycle import ListingStatus, Origin
from apps.listings.models import ListingRevision, PropertyListing
from apps.listings.selectors import canonical_uuid, view_of
from apps.listings.services import FieldsInvalid, StaleRevision

# What a client may ask for. The values written are looked up here, never taken from the request.
TARGETS = lifecycle.MOVE_TARGETS
_WRITABLE = {
    "archived": ListingStatus.ARCHIVED.value,
    "sold": ListingStatus.SOLD.value,
    "rented": ListingStatus.RENTED.value,
}


def _check_shape(base_revision, target) -> dict[str, str]:
    errors: dict[str, str] = {}
    if base_revision is None:
        errors["base_revision"] = "required"
    elif isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 1:
        errors["base_revision"] = "invalid"
    if target is None:
        errors["status"] = "required"
    elif not isinstance(target, str):
        errors["status"] = "invalid"
    elif target not in _WRITABLE:
        errors["status"] = "invalid_choice"
    return errors


def _require_account(account, target: str) -> None:
    """The account state decides what the owner may do (`docs/account-states.md`): Active and
    Email-unverified may archive, mark sold and mark rented; a Suspended owner may only archive
    (it withdraws a Listing, which is never refused to someone who can still see it); a
    Deactivated one has no session. Read from the capability matrix, never from a state list."""
    if account.type == ADMIN_ACCOUNT_TYPE:
        raise DomainError("not_found")
    full = matrix_access(account.state, Capability.EDIT_DRAFTS) is Access.ALLOWED
    seeing = matrix_access(account.state, Capability.VIEW_DRAFTS) is Access.ALLOWED
    if not (full or (seeing and target == ListingStatus.ARCHIVED.value)):
        raise DomainError("forbidden")


def change_status(actor: Actor, listing_id, base_revision, target):
    """Archive, mark sold or mark rented one of the actor's own Listings.

    Refusals, in order: Visitor `unauthenticated`; a Listing the actor's account does not own
    (another owner, an administrator, a system actor, an unknown or malformed id) `not_found`;
    no `view_drafts` capability (Deactivated) `forbidden`; a bad `base_revision` or a `target`
    that is not archived, sold or rented `FieldsInvalid` (codes only). Inside the transaction
    (account lock, then Listing lock): a `base_revision` that is not the stored one raises
    `StaleRevision` before anything else; a Removed Listing `forbidden` (moderation owns it); a
    Suspended owner asking for sold or rented `forbidden`; a restricted Listing may only be
    archived (`forbidden` for sold and rented): a restriction never traps an owner's withdrawal,
    and it stays in force; a move outside `lifecycle.TRANSITIONS` (a Draft, an Archived
    Listing, the same status, `published`, `removed`) `conflict`. Nothing is written on a refusal.
    """
    require_signed_in(actor)
    key = canonical_uuid(listing_id)
    owned = (
        key is not None
        and actor.kind is ActorKind.USER
        and PropertyListing.objects.filter(id=key, owner_account_id=actor.account_id).exists()
    )
    if not owned:
        raise DomainError("not_found")
    if not can(actor, Capability.VIEW_DRAFTS):
        raise DomainError("forbidden")
    problems = _check_shape(base_revision, target)
    if problems:
        raise FieldsInvalid(problems)
    with transaction.atomic():
        account = lock_account(actor.account_id)
        row = (
            PropertyListing.objects.select_for_update()
            .filter(id=key, owner_account_id=actor.account_id)
            .first()
        )
        if row is None:
            raise DomainError("not_found")
        if row.revision != base_revision:
            raise StaleRevision(view_of(row, with_private=True))
        _require_account(account, target)
        refusal = lifecycle.move_refusal(row, target)
        if refusal is not None:
            raise DomainError(refusal)
        previous = row.status
        now = timezone.now()
        row.status = _WRITABLE[target]
        row.revision += 1
        row.updated_at = now
        row.save(update_fields=["status", "revision", "updated_at"])
        ListingRevision.objects.create(
            listing=row,
            revision=row.revision,
            origin=Origin.MANUAL.value,
            actor_account_id=actor.account_id,
            changes={"status": _WRITABLE[target]},
            created_at=now,
        )
        audit.record(
            actor, "listing.status_changed", "listing", row.id, {"from": previous, "to": row.status}
        )
        view = view_of(row, with_private=True)
        emit(ListingStatusChanged(listing_id=row.id, account_id=account.id, status=row.status))
    return view


def archive_for_closed_account(actor: Actor, account_id) -> None:
    """Account closure (FR-8, FR-75): the system archives every Published, Sold and Rented
    Listing of the account, restricted ones included (a restriction stays in force). Inside the
    closure's transaction, which already holds the account lock."""
    require_admin(actor)
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("archive_for_closed_account must run inside the closure's transaction")
    rows = (
        PropertyListing.objects.select_for_update()
        .filter(owner_account_id=account_id, status__in=lifecycle.MOVE_SOURCES)
        .order_by("id")
    )
    for row in rows:
        previous = row.status
        now = timezone.now()
        row.status = _WRITABLE["archived"]
        row.revision += 1
        row.updated_at = now
        row.save(update_fields=["status", "revision", "updated_at"])
        ListingRevision.objects.create(
            listing=row,
            revision=row.revision,
            origin=Origin.MANUAL.value,
            actor_account_id=actor.account_id,
            changes={"status": _WRITABLE["archived"]},
            created_at=now,
        )
        audit.record(
            actor, "listing.status_changed", "listing", row.id, {"from": previous, "to": row.status}
        )
        emit(ListingStatusChanged(listing_id=row.id, account_id=account_id, status=row.status))
