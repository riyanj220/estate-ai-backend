"""Write path for Listings. Only this module creates or changes Listing rows (AD-3)."""

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from apps.catalog import registry
from apps.identity.capabilities import Access, Capability, matrix_access
from apps.identity.policy import can
from apps.identity.selectors import ADMIN_ACCOUNT_TYPE, lock_account
from apps.kernel import audit, ratelimit
from apps.kernel.actor import Actor, ActorKind, require_signed_in
from apps.kernel.errors import DomainError
from apps.listings.lifecycle import ListingStatus, Origin
from apps.listings.models import ListingRevision, PropertyListing
from apps.listings.selectors import ListingView, view_of

OPEN_DRAFTS = "open_drafts"


class FieldsInvalid(DomainError):
    """Draft fields failed. `fields` maps a field name to a stable code; values are never kept."""

    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("validation_error")
        self.fields = dict(fields)


def _validate(purpose, category, subtype) -> dict[str, str]:
    """Every failing field at once, as codes: required, invalid, invalid_choice,
    subtype_not_in_category."""
    errors: dict[str, str] = {}
    choices = {
        "purpose": registry.purposes(),
        "category": tuple(c.id for c in registry.categories()),
        "subtype": registry.all_subtypes(),
    }
    values = {"purpose": purpose, "category": category, "subtype": subtype}
    for name, value in values.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            errors[name] = "required"
        elif not isinstance(value, str):
            errors[name] = "invalid"
        elif value not in choices[name]:
            errors[name] = "invalid_choice"
    if "category" not in errors and "subtype" not in errors:
        if not registry.valid_subtype(category, subtype):
            errors["subtype"] = "subtype_not_in_category"
    return errors


def _open_draft_limit() -> int:
    policy = ratelimit.limit_for(OPEN_DRAFTS)
    if policy.window_seconds is not None:
        raise ImproperlyConfigured(f"Scope {OPEN_DRAFTS} must be a cap (no window)")
    return policy.limit


def create_draft(actor: Actor, purpose, category, subtype) -> ListingView:
    """Start a Draft owned by the actor's account (an Agency's belongs to the Agency).

    Refusals, in order: Visitor `unauthenticated`; no `edit_drafts` capability (Suspended,
    Deactivated, administrators) `forbidden`; invalid fields `validation_error` (all at once);
    20 open Drafts (the `open_drafts` cap policy) `throttled`, without `Retry-After`: a cap
    clears when the owner removes a Draft, not after a time.
    """
    require_signed_in(actor)
    if actor.kind is not ActorKind.USER or not can(actor, Capability.EDIT_DRAFTS):
        raise DomainError("forbidden")
    errors = _validate(purpose, category, subtype)
    if errors:
        raise FieldsInvalid(errors)
    with transaction.atomic():
        account = lock_account(actor.account_id)  # serialises this owner's creations
        # The state may have changed between the first check and the lock: decide again from
        # the locked snapshot (administrators are never owners).
        if (
            account.type == ADMIN_ACCOUNT_TYPE
            or matrix_access(account.state, Capability.EDIT_DRAFTS) is not Access.ALLOWED
        ):
            raise DomainError("forbidden")
        open_drafts = PropertyListing.objects.filter(
            owner_account_id=actor.account_id, status=ListingStatus.DRAFT.value
        ).count()
        if open_drafts >= _open_draft_limit():
            raise DomainError("throttled")
        now = timezone.now()
        row = PropertyListing.objects.create(
            owner_account_id=actor.account_id,
            status=ListingStatus.DRAFT.value,
            revision=1,
            registry_version=registry.registry_version(),
            purpose=purpose,
            category=category,
            subtype=subtype,
            created_at=now,
            updated_at=now,
        )
        ListingRevision.objects.create(
            listing=row,
            revision=1,
            origin=Origin.MANUAL.value,
            actor_account_id=actor.account_id,
            changes={"purpose": purpose, "category": category, "subtype": subtype},
            created_at=now,
        )
        audit.record(
            actor,
            "listing.draft_created",
            "listing",
            row.id,
            {"purpose": purpose, "category": category, "subtype": subtype},
        )
    return view_of(row)
