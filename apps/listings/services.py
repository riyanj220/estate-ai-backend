"""Write path for Listings. Only this module creates or changes Listing rows (AD-3)."""

from collections.abc import Mapping
from decimal import Decimal

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
from apps.kernel.events import emit
from apps.listings import lifecycle, validation
from apps.listings.events import ListingModelInputsChanged
from apps.listings.fields import EDITABLE_FIELDS, LISTING, STORAGE, TERMS_FIELDS
from apps.listings.lifecycle import ListingStatus, Origin
from apps.listings.models import ListingRevision, ListingTerms, PropertyListing
from apps.listings.selectors import (
    MONEY_FORMAT,
    ListingView,
    canonical_uuid,
    clean_provenance,
    terms_of,
    view_of,
)

OPEN_DRAFTS = "open_drafts"


class FieldsInvalid(DomainError):
    """Draft fields failed. `fields` maps a field name to a stable code; values are never kept."""

    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("validation_error")
        self.fields = dict(fields)


class StaleRevision(DomainError):
    """`base_revision` is not the stored revision. Carries the current view so the client can
    reload and reapply; nothing was written."""

    def __init__(self, current: ListingView) -> None:
        super().__init__("stale_revision")
        self.current = current


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
        ListingTerms.objects.create(listing=row, updated_at=now)
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


def _recorded(value) -> str | None:
    """A field value as a string (money with two decimals) or None, for revision rows."""
    if value is None:
        return None
    return MONEY_FORMAT.format(value) if isinstance(value, Decimal) else str(value)


def _locked_owned(key, actor) -> PropertyListing:
    # Locks only the listing row; the terms row is protected by it.
    row = (
        PropertyListing.objects.select_for_update()
        .filter(id=key, owner_account_id=actor.account_id)
        .first()
    )
    if row is None:
        raise DomainError("not_found")
    return row


def apply_updates(
    actor: Actor,
    listing_id,
    base_revision,
    updates: Mapping[str, object],
    origin: Origin | str = Origin.MANUAL,
) -> ListingView:
    """The only function that changes Listing fields after creation (AD-10, AD-22).

    Refusals, in order: Visitor `unauthenticated`; a listing id that is not a canonical UUID of
    a Listing owned by the actor's account `not_found` (another owner, an administrator, a system
    actor and an unknown id are indistinguishable); no `edit_drafts` capability `forbidden`;
    a bad `base_revision` or `updates` shape, unknown or not editable field ids
    `validation_error` (all at once, codes only). Then in one transaction that locks the listing
    row: a `base_revision` that is not the stored revision raises `StaleRevision` before any
    other work on the data; a status outside `lifecycle.EDITABLE_STATUSES` is `forbidden`; every
    update is validated against the current registry (`FieldsInvalid`, all failing fields at
    once); an update that changes nothing writes nothing (saving a stored value manually
    confirms it when its provenance is not yet manual and confirmed, which is a change);
    otherwise the fields, provenance,
    `registry_version`, `revision + 1` and one `ListingRevision` are written and, when a
    `valuation_input` field changed, `listing.model_inputs_changed` is emitted after commit.

    `origin` is chosen by the calling layer (the owner API passes manual, the Assistant ai),
    never read from a client payload. Manual writes are stored confirmed, ai writes unconfirmed.
    """
    require_signed_in(actor)
    try:
        origin = Origin(origin)
    except ValueError:
        raise DomainError("validation_error") from None  # a programming error, never a 500
    key = canonical_uuid(listing_id)
    owned = (
        key is not None
        and actor.kind is ActorKind.USER
        and PropertyListing.objects.filter(id=key, owner_account_id=actor.account_id).exists()
    )
    if not owned:
        raise DomainError("not_found")
    if not can(actor, Capability.EDIT_DRAFTS):
        raise DomainError("forbidden")
    problems = validation.check_shape(base_revision, updates)
    if problems:
        raise FieldsInvalid(problems)
    with transaction.atomic():
        row = _locked_owned(key, actor)
        if row.revision != base_revision:
            raise StaleRevision(view_of(row))
        if row.status not in lifecycle.EDITABLE_STATUSES:
            raise DomainError("forbidden")
        terms = terms_of(row)
        stored_terms = terms is not None
        if terms is None:
            terms = ListingTerms(listing=row)
        owners = {LISTING: row, "terms": terms}
        current = {name: getattr(owners[STORAGE[name]], name) for name in EDITABLE_FIELDS}
        values, errors = validation.evaluate(
            row.purpose, row.category, row.subtype, current, updates
        )
        if errors:
            raise FieldsInvalid(errors)
        provenance = clean_provenance(row.provenance)
        stamp = {"origin": origin.value, "confirmed": origin is Origin.MANUAL}
        # A field changes when its value does, or when the owner saves the stored value of a
        # field that is not yet manual and confirmed (that confirms it).
        changed = {
            name: value
            for name, value in values.items()
            if value != current[name]
            or (value is not None and origin is Origin.MANUAL and provenance.get(name) != stamp)
        }
        if not changed:
            return view_of(row, terms if stored_terms else None)
        now = timezone.now()
        for name, value in changed.items():
            setattr(owners[STORAGE[name]], name, value)
            if value is None:
                provenance.pop(name, None)
            else:
                provenance[name] = dict(stamp)
        row.provenance = provenance
        row.revision = base_revision + 1
        row.registry_version = registry.registry_version()
        row.updated_at = now
        row.save(
            update_fields=[
                *(name for name in EDITABLE_FIELDS if STORAGE[name] == LISTING),
                "provenance",
                "revision",
                "registry_version",
                "updated_at",
            ]
        )
        if not stored_terms or any(name in TERMS_FIELDS for name in changed):
            terms.updated_at = now
            terms.save()
        ListingRevision.objects.create(
            listing=row,
            revision=row.revision,
            origin=origin.value,
            actor_account_id=actor.account_id,
            changes={name: _recorded(value) for name, value in changed.items()},
            created_at=now,
        )
        view = view_of(row, terms)
        if any(registry.field(n).valuation_input for n in changed if changed[n] != current[n]):
            emit(ListingModelInputsChanged(listing_id=row.id))  # last: after-commit
    return view
