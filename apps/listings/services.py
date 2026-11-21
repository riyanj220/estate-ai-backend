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
from apps.listings.area import normalised_sqft
from apps.listings.events import ListingModelInputsChanged
from apps.listings.fields import (
    AREA,
    AREA_FIELDS,
    DERIVED,
    EDITABLE_FIELDS,
    LISTING,
    RESIDENTIAL,
    RESIDENTIAL_FIELDS,
    STORAGE,
    TERMS,
    TERMS_FIELDS,
)
from apps.listings.lifecycle import ListingStatus, Origin
from apps.listings.models import (
    ListingArea,
    ListingResidential,
    ListingRevision,
    ListingTerms,
    PropertyListing,
)
from apps.listings.selectors import (
    ListingView,
    area_of,
    canonical_uuid,
    clean_provenance,
    field_text,
    residential_of,
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


def _stored(name: str, owner) -> object:
    """A stored column as the validated value type: the marla basis as its enum string."""
    return field_text(name, getattr(owner, name)) if name == "marla_basis" else getattr(owner, name)


def _column(name: str, value) -> object:
    """A validated value as the column type (the marla basis string is NUMERIC(6,2))."""
    return Decimal(value) if name == "marla_basis" and value is not None else value


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
        area = area_of(row)
        stored_area = area is not None
        if area is None:
            area = ListingArea(listing=row)
        residential = residential_of(row)
        stored_residential = residential is not None
        if residential is None:
            residential = ListingResidential(listing=row)  # saved only on a first non-null value
        owners = {LISTING: row, TERMS: terms, AREA: area, RESIDENTIAL: residential}
        current = {name: _stored(name, owners[STORAGE[name]]) for name in EDITABLE_FIELDS}
        current.update({name: getattr(owners[table], name) for name, table in DERIVED.items()})
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
        if stored_area or any(name in AREA_FIELDS for name in changed):
            # Derived, recomputed from the RESULTING value, unit and basis in this transaction;
            # a stored figure that differs (stale or repaired data) is a change too.
            resulting = {name: values.get(name, current[name]) for name in AREA_FIELDS}
            sqft = normalised_sqft(
                resulting["area_value"],
                resulting["area_unit"],
                _column("marla_basis", resulting["marla_basis"]),
            )
            if sqft != current["area_sqft"]:
                changed["area_sqft"] = sqft
        if not changed:
            return view_of(
                row,
                terms if stored_terms else None,
                area if stored_area else None,
                residential if stored_residential else None,
            )
        now = timezone.now()
        for name, value in changed.items():
            table = DERIVED[name] if name in DERIVED else STORAGE[name]
            setattr(owners[table], name, _column(name, value))
            if name in DERIVED:
                continue
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
        if any(name in AREA_FIELDS or name in DERIVED for name in changed):
            area.updated_at = now
            area.save()
        # The row exists only once a value is first saved: a clear (null) never creates it, and
        # `changed` holds a null only for a column that already holds a value.
        if any(name in RESIDENTIAL_FIELDS for name in changed):
            residential.updated_at = now
            residential.save()
        ListingRevision.objects.create(
            listing=row,
            revision=row.revision,
            origin=origin.value,
            actor_account_id=actor.account_id,
            changes={name: field_text(name, value) for name, value in changed.items()},
            created_at=now,
        )
        view = view_of(
            row, terms, area if area.pk else None, residential if residential.pk else None
        )
        if any(registry.field(n).valuation_input for n in changed if changed[n] != current[n]):
            emit(ListingModelInputsChanged(listing_id=row.id))  # last: after-commit
    return view
