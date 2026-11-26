"""Write path for Listings. Only this module creates or changes Listing rows (AD-3)."""

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.catalog import registry
from apps.catalog import selectors as catalog
from apps.identity.capabilities import Access, Capability, matrix_access
from apps.identity.policy import can
from apps.identity.selectors import ADMIN_ACCOUNT_TYPE, is_sample_account, lock_account
from apps.identity.state import AccountState
from apps.kernel import audit, media, notifications, ratelimit
from apps.kernel.actor import Actor, ActorKind, require_signed_in
from apps.kernel.errors import DomainError
from apps.kernel.events import emit
from apps.listings import lifecycle, validation
from apps.listings.area import normalised_sqft
from apps.listings.completeness import problems_of
from apps.listings.events import ListingModelInputsChanged
from apps.listings.fields import (
    ADDRESS,
    ADDRESS_FIELDS,
    AMENITIES,
    AMENITIES_FIELD,
    AREA,
    AREA_FIELDS,
    COMMERCIAL,
    COMMERCIAL_FIELDS,
    DERIVED,
    EDITABLE_FIELDS,
    LISTING,
    LOCATION,
    LOCATION_FIELDS,
    PLOT,
    PLOT_FIELDS,
    RESIDENTIAL,
    RESIDENTIAL_FIELDS,
    STORAGE,
    TERMS,
    TERMS_FIELDS,
)
from apps.listings.lifecycle import ListingStatus, Origin
from apps.listings.models import (
    MAX_IMAGES,
    ListingAmenity,
    ListingArea,
    ListingCommercial,
    ListingImage,
    ListingLocation,
    ListingPlot,
    ListingPrivateAddress,
    ListingResidential,
    ListingRevision,
    ListingTerms,
    PropertyListing,
)
from apps.listings.selectors import (
    DRAFT_EXPIRING,
    GRACE_DAYS,
    IDLE_DAYS,
    LATE_AFTER_DAYS,
    REGRACE_DAYS,
    ImageView,
    ListingView,
    activity_of,
    address_of,
    amenities_of,
    area_of,
    canonical_uuid,
    clean_provenance,
    commercial_of,
    field_text,
    image_url,
    live_image_count,
    location_of,
    notice_stored,
    plot_of,
    residential_of,
    terms_of,
    view_of,
)

OPEN_DRAFTS = "open_drafts"
OPEN_THROTTLE = timedelta(hours=1)  # `last_opened_at` is written at most this often


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
    return view_of(row, with_private=True)


def _stored(name: str, owner) -> object:
    """A stored column as the validated value type: the marla basis as its enum string."""
    return field_text(name, getattr(owner, name)) if name == "marla_basis" else getattr(owner, name)


def _column(name: str, value) -> object:
    """A validated value as the column type (the marla basis string is NUMERIC(6,2))."""
    return Decimal(value) if name == "marla_basis" and value is not None else value


def _resolve_reference(kind: str, key: uuid.UUID) -> uuid.UUID | None:
    """The catalog's answer for a chosen city or locality: the city's own id, a locality's city id,
    None when it is unknown or inactive (validation turns that into `invalid`)."""
    if kind == "city":
        city = catalog.get_city(key)
        return city.id if city else None
    locality = catalog.get_locality(key)
    return locality.city_id if locality else None


def _revision_changes(changed: Mapping[str, object]) -> dict[str, object]:
    """What a revision row records: public values as the wire shows them, and for a field of class
    `private` (or one the registry does not define: it is treated as private) only `true` (set) or
    `null` (cleared), never the value."""
    recorded: dict[str, object] = {}
    for name, value in changed.items():
        definition = registry.field(name)  # looked up once
        if definition is None or definition.field_class != "public":
            recorded[name] = None if value is None else True
        else:
            recorded[name] = field_text(name, value)
    return recorded


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


@dataclass(frozen=True)
class Written:
    """What `write_locked` did: the resulting view, the names of the fields whose VALUE changed
    (derived ones and confirmation-only writes left out) and whether a `valuation_input` field is
    among all changed values."""

    view: ListingView
    changed: frozenset[str]
    model_inputs_changed: bool


# (current stored values, normalised new values, field errors so far) -> more field errors
Judge = Callable[[Mapping, Mapping, Mapping], Mapping[str, str]]


def apply_updates(
    actor: Actor,
    listing_id,
    base_revision,
    updates: Mapping[str, object],
    origin: Origin | str = Origin.MANUAL,
) -> ListingView:
    """The function that changes Listing fields after creation (AD-10, AD-22); the write itself is
    `write_locked`, also used by the one-time `publication.correct`.

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
            raise StaleRevision(view_of(row, with_private=True))
        if not lifecycle.owner_may_edit(row):
            raise DomainError("forbidden")
        written = write_locked(actor, row, updates, origin)
        if written.model_inputs_changed:
            emit(ListingModelInputsChanged(listing_id=row.id))  # last: after-commit
    return written.view


def write_locked(
    actor: Actor,
    row: PropertyListing,
    updates: Mapping[str, object],
    origin: Origin,
    *,
    unlocked: frozenset[str] = frozenset(),
    judge: Judge | None = None,
) -> Written:
    """The one write path of Listing fields, for a caller that already holds the Listing row
    lock inside a transaction and has decided who may write (`apply_updates`, and
    `publication.correct`; an architecture guard pins the callers). It checks no actor, revision
    or status, and emits nothing: the caller emits `listing.model_inputs_changed` last.

    `unlocked` names identity fields that may change although the Listing is Published (the
    one-time correction only). `judge` adds the caller's own field errors, reported with the
    rest. Everything else is as described on `apply_updates`."""
    with transaction.atomic():  # joins the caller's transaction
        base_revision = row.revision
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
        plot = plot_of(row)
        stored_plot = plot is not None
        if plot is None:
            plot = ListingPlot(listing=row)  # saved only on a first non-null value
        commercial = commercial_of(row)
        stored_commercial = commercial is not None
        if commercial is None:
            commercial = ListingCommercial(listing=row)  # saved only on a first non-null value
        location = location_of(row)
        stored_location = location is not None
        if location is None:
            location = ListingLocation(listing=row)  # saved only on a first non-null value
        address = address_of(row)
        stored_address = address is not None
        if address is None:
            address = ListingPrivateAddress(listing=row)  # saved only on a first non-null value
        stored_amenities = amenities_of(row)
        owners = {
            LISTING: row,
            TERMS: terms,
            AREA: area,
            RESIDENTIAL: residential,
            PLOT: plot,
            COMMERCIAL: commercial,
            LOCATION: location,
            ADDRESS: address,
        }
        current = {
            name: (stored_amenities or None)
            if STORAGE[name] == AMENITIES
            else _stored(name, owners[STORAGE[name]])
            for name in EDITABLE_FIELDS
        }
        current.update({name: getattr(owners[table], name) for name, table in DERIVED.items()})
        values, errors = validation.evaluate(
            row.purpose,
            row.category,
            row.subtype,
            current,
            updates,
            _resolve_reference,
            catalog.amenity_codes() if AMENITIES_FIELD in updates else None,
        )
        if lifecycle.is_published(row):
            # The identity fields (registry flag `quota_identity`) are locked while Published: a
            # value that differs from the stored one is refused, all failures reported at once.
            for name in updates:
                definition = registry.field(name)
                if (
                    definition is not None
                    and definition.quota_identity
                    and name not in unlocked
                    and name not in errors  # a more specific code (invalid, ...) stays
                    and not (name in values and values[name] == current[name])
                ):
                    errors[name] = "not_editable"
        if judge is not None:
            for name, code in judge(current, values, errors).items():
                errors.setdefault(name, code)
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
            unchanged_view = view_of(
                row,
                terms if stored_terms else None,
                area if stored_area else None,
                residential if stored_residential else None,
                plot if stored_plot else None,
                commercial if stored_commercial else None,
                location if stored_location else None,
                address if stored_address else None,
                amenities=stored_amenities,
                with_private=True,
            )
            return Written(unchanged_view, frozenset(), False)
        now = timezone.now()
        for name, value in changed.items():
            table = DERIVED[name] if name in DERIVED else STORAGE[name]
            if table != AMENITIES:  # the map is written as rows below
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
        if any(name in PLOT_FIELDS for name in changed):
            plot.updated_at = now
            plot.save()
        if any(name in COMMERCIAL_FIELDS for name in changed):
            commercial.updated_at = now
            commercial.save()
        if any(name in LOCATION_FIELDS for name in changed):
            location.updated_at = now
            location.save()
        if any(name in ADDRESS_FIELDS for name in changed):
            address.updated_at = now
            address.save()
        if "amenities" in changed:  # the map replaces the stored rows, under the listing lock
            new_map = changed["amenities"] or {}
            ListingAmenity.objects.filter(listing=row).delete()
            ListingAmenity.objects.bulk_create(
                ListingAmenity(listing=row, amenity_code=code, state=state)
                for code, state in new_map.items()
            )
            stored_amenities = dict(new_map)
        ListingRevision.objects.create(
            listing=row,
            revision=row.revision,
            origin=origin.value,
            actor_account_id=actor.account_id,
            changes=_revision_changes(changed),
            created_at=now,
        )
        view = view_of(
            row,
            terms,
            area if area.pk else None,
            residential if residential.pk else None,
            plot if plot.pk else None,
            commercial if commercial.pk else None,
            location if location.pk else None,
            address if address.pk else None,
            amenities=stored_amenities,
            with_private=True,
        )
        if lifecycle.is_published(row):
            # A Published Listing stays complete: the same rules as publication, on the result.
            # Raising here rolls the whole edit back (nothing written, revision unchanged).
            problems = problems_of(
                row, view, live_image_count(row.id) > 0, touched=frozenset(changed)
            )
            if problems:
                raise FieldsInvalid(problems)
        different = frozenset(n for n in changed if changed[n] != current[n])
    return Written(
        view,
        different - frozenset(DERIVED),
        any(registry.field(n).valuation_input for n in different),
    )


def _owned_editable(actor: Actor, listing_id) -> uuid.UUID:
    """The id of a Listing the actor's account owns, after the shared refusals: Visitor
    `unauthenticated`, not owned (or malformed or unknown) `not_found`, no `edit_drafts`
    `forbidden`."""
    require_signed_in(actor)
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
    return key


def require_image_access(actor: Actor, listing_id) -> None:
    """The refusals of `upload_image` that need no body: Visitor `unauthenticated`, not owned
    `not_found`, no `edit_drafts` `forbidden`. The API runs it BEFORE it looks at the upload, so
    a non-owner or a Suspended owner gets 404 or 403 whatever the body is."""
    _owned_editable(actor, listing_id)


def _lock_for_images(actor: Actor, key: uuid.UUID, check_status: bool = True):
    """Inside a transaction: the account (the state is decided again from the locked snapshot),
    then the Listing row, which must still be an editable Draft (unless the caller decides the
    status itself, after its revision check). Returns both."""
    account = lock_account(actor.account_id)
    if (
        account.type == ADMIN_ACCOUNT_TYPE
        or matrix_access(account.state, Capability.EDIT_DRAFTS) is not Access.ALLOWED
    ):
        raise DomainError("forbidden")
    row = _locked_owned(key, actor)
    if check_status and not lifecycle.owner_may_edit(row):
        raise DomainError("forbidden")
    return account, row


def _bump(row: PropertyListing, actor: Actor, count: int, **flags: bool) -> None:
    """One revision for an image change; the revision row records only the image count (and, for
    a reorder, whether the order and the cover changed): never an id."""
    now = timezone.now()
    row.revision += 1
    row.updated_at = now
    row.save(update_fields=["revision", "updated_at"])
    ListingRevision.objects.create(
        listing=row,
        revision=row.revision,
        origin=Origin.MANUAL.value,
        actor_account_id=actor.account_id,
        changes={"images": count, **flags},
        created_at=now,
    )


def _normalise_images(row: PropertyListing) -> bool:
    """Under the Listing lock: delete ghost links (no asset row), renumber the positions
    contiguously 0..n-1 and make sure exactly one image is the cover while any remain. Returns
    whether anything was changed. Every mutating image service calls it first, so a lost asset
    row or a stale position never makes the set unmanageable; the repair rides on that call's
    own revision write."""
    links = sorted(ListingImage.objects.select_for_update().filter(listing=row), key=_position)
    live = media.assets_by_id(link.asset_id for link in links)
    ghosts = [link for link in links if link.asset_id not in live]
    kept = [link for link in links if link.asset_id in live]
    changed = bool(ghosts)
    if ghosts:
        ListingImage.objects.filter(pk__in=[link.pk for link in ghosts]).delete()
    for index, link in enumerate(kept):  # the unique check is deferred to commit
        if link.position != index:
            ListingImage.objects.filter(pk=link.pk).update(position=index)
            changed = True
    if kept and not any(link.is_cover for link in kept):
        ListingImage.objects.filter(pk=kept[0].pk).update(is_cover=True)
        changed = True
    return changed


def upload_image(actor: Actor, listing_id, data: bytes) -> ImageView:
    """Add an image to one of the owner's Drafts (Story 2.9).

    Refusals, in order: Visitor `unauthenticated`; a Listing the actor's account does not own
    (another owner, an administrator, an unknown or malformed id) `not_found`; no `edit_drafts`
    capability `forbidden`; a status that cannot be edited `forbidden`; 10 images already
    `too_many_images`. Then the upload is validated and re-encoded OUTSIDE any lock
    (`kernel.images`: `ImageRejected` with a reason code, or `Busy`), and in one transaction that
    locks the account and the Listing: the account, status and image count are decided again, the
    storage cap is enforced atomically (`storage_cap_exceeded`), the file and asset row are written,
    the `ListingImage` is attached at the end (position = the number of images already there;
    the first image of a Listing becomes its cover, a later one never changes it), and the
    Listing `revision` rises by one with a `ListingRevision` whose `changes` is
    `{"images": count}`. If the transaction fails after the
    file was written, the file is removed again. No `base_revision` is needed.
    """
    key = _owned_editable(actor, listing_id)
    listing = PropertyListing.objects.filter(id=key).first()
    if listing is None or not lifecycle.owner_may_edit(listing):
        raise DomainError("forbidden")
    if live_image_count(key) >= MAX_IMAGES:  # ghosts (lost asset rows) are not counted
        raise DomainError("too_many_images")
    processed = media.process_upload(actor, data)
    asset = None
    try:
        with transaction.atomic():
            account, row = _lock_for_images(actor, key)
            _normalise_images(row)  # ghosts out, positions contiguous, one cover
            existing = list(
                ListingImage.objects.filter(listing=row).values_list("position", flat=True)
            )
            if len(existing) >= MAX_IMAGES:
                raise DomainError("too_many_images")
            asset = media.store_processed(actor, processed, account_type=account.type, attach=True)
            image = ListingImage.objects.create(
                listing=row,
                asset_id=asset.id,
                position=max(existing, default=-1) + 1,
                is_cover=not existing,
                created_at=timezone.now(),
            )
            _bump(row, actor, len(existing) + 1)
    except BaseException:
        if asset is not None:
            media.discard_file(asset.storage_key)  # the row rolled back; no file may outlive it
        raise
    return ImageView(
        id=image.id,
        position=image.position,
        is_cover=image.is_cover,
        content_type=asset.content_type,
        width=asset.width,
        height=asset.height,
        bytes=asset.bytes,
        url=image_url(key, image.id),
    )


def remove_image(actor: Actor, listing_id, image_id) -> None:
    """Remove an image from one of the owner's Drafts: the `ListingImage` and its asset go in one
    transaction (the storage usage is given back once), the file after commit, and the Listing
    `revision` rises by one. Same refusals as `upload_image`; an unknown image, another Listing's
    image and a repeat are `not_found`. The positions of the rest are renumbered 0..n-1, and
    removing the cover makes the image with the lowest remaining position the cover (none left:
    no cover). Ghost links (no asset row) are removed in the same transaction. A Published
    Listing can never lose its last image: `FieldsInvalid` `images: images_required`, nothing
    changed."""
    key = _owned_editable(actor, listing_id)
    image_key = canonical_uuid(image_id)
    if image_key is None:
        raise DomainError("not_found")
    with transaction.atomic():
        _, row = _lock_for_images(actor, key)
        image = ListingImage.objects.select_for_update().filter(id=image_key, listing=row).first()
        if image is None:
            raise DomainError("not_found")
        if lifecycle.is_published(row):
            # Decided BEFORE anything is deleted (the Listing lock serialises racing removals): a
            # Published Listing keeps at least one live image.
            others = list(
                ListingImage.objects.filter(listing=row)
                .exclude(id=image.id)
                .values_list("asset_id", flat=True)
            )
            if not media.assets_by_id(others):
                raise FieldsInvalid({"images": "images_required"})
        asset_id = image.asset_id
        image.delete()
        media.delete_asset(actor, asset_id, missing_ok=True)  # a lost asset row is no obstacle
        _normalise_images(row)  # renumbered; the lowest position is the cover if none is left
        _bump(row, actor, ListingImage.objects.filter(listing=row).count())


def set_image_order(actor: Actor, listing_id, base_revision, order, cover_id=None) -> ListingView:
    """Reorder a Draft's images and/or choose its cover in one revision-checked step (Story 2.10).

    `order` is the full list of the Listing's image ids in the wanted order; `cover_id` is
    optional (None keeps the current cover). Refusals, in order: Visitor `unauthenticated`; not
    owned (another owner, an administrator, unknown or malformed id) `not_found`; no `edit_drafts`
    `forbidden`; a bad shape (`base_revision` not a positive integer, `order` not a list of ids)
    `validation_error` (`FieldsInvalid`, codes only). In one transaction that locks the account
    and the Listing row: a `base_revision` that is not the stored one raises `StaleRevision` with
    the current view and nothing is written; a status outside `lifecycle.EDITABLE_STATUSES` is
    `forbidden`; `order` that is not exactly the current set of ids (a missing, duplicate, unknown
    or foreign id) is `invalid` on `order`, a `cover_id` that is not one of the images `invalid`
    on `cover_id` (both reported together). A request that changes nothing returns the current
    view: no revision bump, no revision row. Otherwise positions become 0..n-1 in the given order,
    exactly one image is the cover, the revision rises by one and one `ListingRevision` records
    `{"images": n, "order": bool, "cover": bool}` (counts and flags, never ids; plus
    `"repaired": true` when ghost links, positions or the cover were repaired, which also counts
    as a change). Files are never touched.
    """
    key = _owned_editable(actor, listing_id)
    problems: dict[str, str] = {}
    if base_revision is None:
        problems["base_revision"] = "required"
    elif isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 1:
        problems["base_revision"] = "invalid"
    wanted: list[uuid.UUID] = []
    if order is None:
        problems["order"] = "required"
    elif (
        not isinstance(order, list)
        or len(order) > MAX_IMAGES  # refused before anything else is looked at
        or any(canonical_uuid(item) is None for item in order)
    ):
        problems["order"] = "invalid"
    else:
        wanted = [canonical_uuid(item) for item in order]
    if cover_id is not None and canonical_uuid(cover_id) is None:
        problems["cover_id"] = "invalid"
    if problems:
        raise FieldsInvalid(problems)
    with transaction.atomic():
        _, row = _lock_for_images(actor, key, check_status=False)
        if row.revision != base_revision:
            raise StaleRevision(view_of(row, with_private=True))
        if not lifecycle.owner_may_edit(row):
            raise DomainError("forbidden")
        repaired = _normalise_images(row)  # ghosts out, contiguous, one cover (counts as a change)
        links = sorted(ListingImage.objects.select_for_update().filter(listing=row), key=_position)
        if not links and lifecycle.is_published(row):
            # ghost links were just repaired away: raising rolls that back too
            raise FieldsInvalid({"images": "images_required"})
        current = [link.id for link in links]
        cover = canonical_uuid(cover_id)
        if len(set(wanted)) != len(wanted) or set(wanted) != set(current):
            problems["order"] = "invalid"
        if cover is not None and cover not in set(current):
            problems["cover_id"] = "invalid"
        if problems:
            raise FieldsInvalid(problems)
        old_cover = next((link.id for link in links if link.is_cover), None)
        new_cover = cover or old_cover or (wanted[0] if wanted else None)
        reordered, recovered = wanted != current, new_cover != old_cover
        if not reordered and not recovered and not repaired:
            return view_of(row, with_private=True)
        by_id = {link.id: link for link in links}
        for index, image_id in enumerate(wanted):
            if by_id[image_id].position != index:  # the unique check waits for the commit
                ListingImage.objects.filter(pk=image_id).update(position=index)
        if recovered:
            ListingImage.objects.filter(listing=row, is_cover=True).update(is_cover=False)
            ListingImage.objects.filter(pk=new_cover).update(is_cover=True)
        flags = {"repaired": True} if repaired else {}
        _bump(row, actor, len(current), order=reordered, cover=recovered, **flags)
        return view_of(row, with_private=True)


def _position(link) -> int:
    return link.position


# --- opening, deleting and retention of Drafts (Story 2.13) --------------------------------------


def record_open(actor: Actor, listing_id, now: datetime | None = None) -> bool:
    """Note that the owner opened or previewed their Draft: set `last_opened_at`, at most once an
    hour (when it is missing or older than an hour), and never touch `revision`, `updated_at` or
    the revision rows. Returns whether it wrote.
    Not the owner's Draft, a Visitor or an actor without `view_drafts` is a quiet `False` (the
    read that precedes this call has already answered with `not_found`)."""
    if actor.kind is not ActorKind.USER or not can(actor, Capability.VIEW_DRAFTS):
        return False
    key = canonical_uuid(listing_id)
    if key is None:
        return False
    now = now or timezone.now()
    due = Q(last_opened_at__isnull=True) | Q(last_opened_at__lt=now - OPEN_THROTTLE)
    written = (
        PropertyListing.objects.filter(
            due, id=key, owner_account_id=actor.account_id, status=ListingStatus.DRAFT.value
        ).update(last_opened_at=now)
        == 1
    )
    return written


def _remove_rows(actor: Actor, row: PropertyListing) -> uuid.UUID:
    """Under the account and Listing locks: give every image back through `kernel.media` (usage
    returned, files deleted after commit; a lost asset row is no obstacle) and delete the Listing
    and every row that belongs to it (the foreign keys cascade). Returns the Listing id; the
    caller writes the audit row (the id and a reason only, never content)."""
    asset_ids = list(ListingImage.objects.filter(listing=row).values_list("asset_id", flat=True))
    for asset_id in asset_ids:
        if actor.kind is ActorKind.USER:
            media.delete_asset(actor, asset_id, missing_ok=True)
        else:
            media.purge_owned_asset(actor, row.owner_account_id, asset_id)
    listing_id = row.id
    row.delete()
    return listing_id


def _remove_draft(actor: Actor, row: PropertyListing, reason: str) -> None:
    """`_remove_rows` and the `listing.draft_deleted` audit row (reason `owner` or `retention`)."""
    listing_id = _remove_rows(actor, row)
    audit.record(actor, "listing.draft_deleted", "listing", listing_id, {"reason": reason})


def delete_draft(actor: Actor, listing_id) -> None:
    """Delete one of the owner's Drafts with its images (Story 2.13).

    Refusals, in order: Visitor `unauthenticated`; not owned (another account, an administrator,
    an unknown or malformed id, and a repeat) `not_found`; no `edit_drafts` capability (Suspended,
    Deactivated) `forbidden`; a Listing that is not a `draft` `conflict`, nothing changed. In one
    transaction that locks the account, then the Listing row (the order used everywhere), and
    decides again from the locked rows: every image goes through `media.delete_asset` (usage given
    back, files deleted after commit), then the Listing and every row of it (location, private
    address, amenities, details, image links, revisions) and one `listing.draft_deleted` audit row
    with the Listing id and the reason `owner`. An edit or upload racing the delete ends in
    `not_found` or `conflict`, never an orphan."""
    key = _owned_editable(actor, listing_id)
    with transaction.atomic():
        _, row = _lock_for_images(actor, key, check_status=False)
        if row.status != ListingStatus.DRAFT.value:
            raise DomainError("conflict")
        _remove_draft(actor, row, "owner")


def _require_system(actor: Actor) -> None:
    if actor.kind is not ActorKind.SYSTEM:
        raise DomainError("forbidden")


def _locked_for_retention(key, owner_id):
    """The owner's account and then the Listing row, locked (the usual order), as they are now.
    None when the Listing is gone or no longer a Draft of that owner, or its owner is Suspended or
    Deactivated."""
    account = lock_account(owner_id)
    row = PropertyListing.objects.select_for_update().filter(id=key).first()
    if (
        row is None
        or row.owner_account_id != owner_id
        or row.status != ListingStatus.DRAFT.value
        or account.state in (AccountState.SUSPENDED, AccountState.DEACTIVATED)
    ):
        return None
    return row


def _owner_of(key) -> uuid.UUID | None:
    return PropertyListing.objects.filter(id=key).values_list("owner_account_id", flat=True).first()


def notice_idle_draft(actor: Actor, listing_id, now: datetime) -> bool:
    """Retention (system actor only, `forbidden` otherwise): tell the owner a Draft with 180 days
    of no activity will be deleted in 30 days. Decided again under the locks: it must still be a
    Draft of a non-Suspended, non-Deactivated owner, without a notice, with activity at or before
    `now - 180 days`.
    Sets `expiry_notice_sent_at = now` and `delete_after = now + 30 days` and raises
    `listing.draft_expiring` (the Listing id and the days left, nothing else) after commit as the
    last step. Returns whether it noticed."""
    _require_system(actor)
    key = canonical_uuid(listing_id)
    owner = _owner_of(key) if key else None
    if owner is None:
        return False
    with transaction.atomic():
        row = _locked_for_retention(key, owner)
        if (
            row is None
            or row.expiry_notice_sent_at is not None
            or activity_of(row) > now - timedelta(days=IDLE_DAYS)
        ):
            return False
        row.expiry_notice_sent_at = now
        row.delete_after = now + timedelta(days=GRACE_DAYS)
        row.save(update_fields=["expiry_notice_sent_at", "delete_after"])
        notifications.notify(owner, DRAFT_EXPIRING, {"listing_id": row.id, "days_left": GRACE_DAYS})
    return True


def expire_idle_draft(actor: Actor, listing_id, now: datetime) -> str:
    """Retention (system actor only, `forbidden` otherwise): act on a Draft whose `delete_after`
    has come. Decided again under the locks, in this order:

    - gone, not a Draft, owner Suspended or Deactivated, no deletion date, or not yet due:
      `skipped`;
    - opened or edited since the notice (activity later than `expiry_notice_sent_at`): both
      columns cleared, the Draft stays: `cleared`;
    - no stored `listing.draft_expiring` notification for it (the notice may never have reached
      the owner): the notice is issued again with the full 30 days (`delete_after = now + 30 days`),
      nothing is deleted: `extended`;
    - the deletion date is more than 2 days past (the owner was Suspended or the job did not run):
      the owner gets a fresh 7 days, `delete_after = now + 7 days`, a new notice with
      `days_left: 7`, nothing is deleted: `extended`;
    - otherwise it is deleted exactly like an owner delete (images, rows, audit reason
      `retention`): `deleted`."""
    _require_system(actor)
    key = canonical_uuid(listing_id)
    owner = _owner_of(key) if key else None
    if owner is None:
        return "skipped"
    with transaction.atomic():
        row = _locked_for_retention(key, owner)
        if row is None or row.delete_after is None or row.delete_after > now:
            return "skipped"
        if activity_of(row) > row.expiry_notice_sent_at:
            row.expiry_notice_sent_at = None
            row.delete_after = None
            row.save(update_fields=["expiry_notice_sent_at", "delete_after"])
            return "cleared"
        if not notice_stored(row):
            days = GRACE_DAYS
        elif now - row.delete_after > timedelta(days=LATE_AFTER_DAYS):
            days = REGRACE_DAYS
        else:
            _remove_draft(actor, row, "retention")
            return "deleted"
        row.expiry_notice_sent_at = now
        row.delete_after = now + timedelta(days=days)
        row.save(update_fields=["expiry_notice_sent_at", "delete_after"])
        notifications.notify(owner, DRAFT_EXPIRING, {"listing_id": row.id, "days_left": days})
        return "extended"


def remove_sample_listing(actor: Actor, listing_id) -> bool:
    """Sample data only (system actor, `forbidden` otherwise): delete a Listing of any status with
    its images (usage given back, files deleted after commit) and every row of it, and write the
    audit row `listing.sample_removed` (the id only). A Listing whose owner is not a sample
    account, and an unknown id, are left alone and give False.
    Locks the account, then the Listing."""
    _require_system(actor)
    key = canonical_uuid(listing_id)
    owner = _owner_of(key) if key else None
    if owner is None or not is_sample_account(owner):
        return False
    with transaction.atomic():
        lock_account(owner)
        row = PropertyListing.objects.select_for_update().filter(id=key).first()
        if row is None or row.owner_account_id != owner:
            return False
        audit.record(actor, "listing.sample_removed", "listing", _remove_rows(actor, row), {})
    return True
