"""Publishing a Draft and republishing an Archived Listing (AD-7, Stories 2.15 and 2.18). The ONLY
code that sets `ListingStatus.PUBLISHED` and the only writer of `PublicationEvent` (an architecture
guard enforces both).

`publish` and `republish` share one transaction (`_run`, parameterised by `_Mode`), in this
order: claim the idempotency key; lock the account and then the Listing row; compare
`base_revision`; require the source status (a Draft, or an Archived Listing); refuse a restricted
Listing; check the account state; validate the whole Listing; decide the event (`initial`
always counted; `republish_counted` or `republish_free` by the rule at `_republish_kind`) and
check the monthly allowance for a counted one; write the event with the identity fingerprint; set
the status and bump the revision; write the audit record; complete the claim; emit
`listing.published` (last, after commit). Any refusal or error rolls everything back, the claim
included, so nothing is consumed and the same key may be retried. No AI call, upload, mail or
notification happens here.
"""

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import DEFAULT_DB_ALIAS, transaction
from django.utils import timezone

from apps.catalog import registry
from apps.identity import selectors as identity
from apps.identity.selectors import ADMIN_ACCOUNT_TYPE, lock_account
from apps.identity.state import AccountState
from apps.kernel import audit, idempotency
from apps.kernel.actor import Actor, ActorKind, require_signed_in
from apps.kernel.errors import DomainError
from apps.kernel.events import emit
from apps.listings import quota
from apps.listings.completeness import problems_of
from apps.listings.events import ListingPublished
from apps.listings.lifecycle import ListingStatus, Origin
from apps.listings.models import (
    COUNTED_KINDS,
    QUOTA_ACCOUNT_TYPES,
    ListingRevision,
    PropertyListing,
    PublicationEvent,
)
from apps.listings.selectors import (
    ListingView,
    canonical_uuid,
    get_owned_listing,
    live_image_count,
    view_of,
)
from apps.listings.services import FieldsInvalid, StaleRevision

SCOPE = "listings.publish"
KEY_FIELD = "idempotency_key"  # the name a header problem is reported under
KIND_INITIAL = "initial"
KIND_SEED = "seed"  # the sample-data command only: uncounted, system actor only (Story 2.16)
_KINDS = (KIND_INITIAL, KIND_SEED)
REPUBLISH_SCOPE = "listings.republish"
KIND_REPUBLISH_COUNTED = "republish_counted"
KIND_REPUBLISH_FREE = "republish_free"
_REPUBLISH_KINDS = (KIND_REPUBLISH_COUNTED, KIND_REPUBLISH_FREE)
_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class QuotaExhausted(DomainError):
    """The monthly allowance is used up. Carries the caller's own numbers; nothing was written."""

    def __init__(self, allowance: quota.Allowance) -> None:
        super().__init__("quota_exhausted")
        self.limit = allowance.limit
        self.used = allowance.used
        self.resets_at = allowance.resets_at


def _number(value) -> str:
    """A finite number as one canonical text: no exponent, no trailing zeros, `-0` is `0`."""
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if not number.is_finite():
        raise ValueError("NaN and Infinity cannot be fingerprinted")
    if number == 0:
        return "0"
    return format(number.normalize(), "f")


def _canonical(definition, value):
    """A stored value in one deterministic JSON shape: numbers (and money and decimal text) as
    canonical decimal text, text in NFC without surrounding whitespace, lists as ordered arrays
    of canonical items, mappings with text keys. NaN and Infinity are refused."""
    if value is None or isinstance(value, bool):
        return value
    numeric = definition is not None and definition.type in ("money", "decimal")
    if isinstance(value, int | float | Decimal) and (numeric or not isinstance(value, int)):
        return _number(value)
    if isinstance(value, int):
        return value
    if isinstance(value, list | tuple):
        return [_canonical(None, item) for item in value]
    if isinstance(value, Mapping):
        return {str(k): _canonical(None, v) for k, v in value.items()}
    text = unicodedata.normalize("NFC", str(value)).strip()
    if definition is not None and definition.type in ("money", "decimal"):
        try:
            return _number(Decimal(text))
        except InvalidOperation:
            return text
    return text


def identity_fingerprint(view: ListingView) -> str:
    """SHA-256 hex over the canonical JSON of the registry fields flagged `quota_identity`.

    The ONE place the fingerprint is computed. It is stored on the publication event and never
    leaves the service (no response, log or audit record carries it)."""
    parts = {
        definition.id: _canonical(definition, view.fields.get(definition.id))
        for definition in registry.fields()
        if definition.quota_identity
    }
    text = json.dumps(
        parts, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(text.encode()).hexdigest()


def _check_shape(base_revision, idempotency_key) -> dict[str, str]:
    errors: dict[str, str] = {}
    if base_revision is None:
        errors["base_revision"] = "required"
    elif isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 1:
        errors["base_revision"] = "invalid"
    if idempotency_key is None:
        errors[KEY_FIELD] = "required"
    elif not isinstance(idempotency_key, str) or not _KEY.fullmatch(idempotency_key):
        errors[KEY_FIELD] = "invalid"
    return errors


def _require_state(account) -> None:
    """Only an Active owner publishes. The refusal reasons are static and carry no account data."""
    if account.type == ADMIN_ACCOUNT_TYPE or account.type not in QUOTA_ACCOUNT_TYPES:
        raise DomainError("not_found")
    if account.state is AccountState.ACTIVE:
        return
    if account.state is AccountState.EMAIL_UNVERIFIED:
        raise DomainError("email_not_verified")
    if account.state is AccountState.SUSPENDED:
        raise DomainError("publishing_unavailable")
    raise DomainError("forbidden")  # Deactivated: no session exists over HTTP


def _replay(actor: Actor, result_ref: str) -> ListingView:
    """The owner's Listing as it is now, for a key that already published it. A reference that is
    malformed, or a Listing that is gone or not the caller's, is `not_found`."""
    kind, _, listing_id = result_ref.partition(":")
    if kind != "listing":
        raise DomainError("not_found")
    return get_owned_listing(actor, listing_id)


@dataclass(frozen=True)
class _Mode:
    """What differs between publishing a Draft and republishing an Archived Listing."""

    scope: str
    source: str  # the status the Listing must have
    kinds: tuple[str, ...]  # the event kinds a key of this mode may have produced
    action: str  # the audit action


_PUBLISH = _Mode(SCOPE, ListingStatus.DRAFT.value, _KINDS, "listing.published")
_REPUBLISH = _Mode(
    REPUBLISH_SCOPE, ListingStatus.ARCHIVED.value, _REPUBLISH_KINDS, "listing.republished"
)


@dataclass(frozen=True)
class Republication:
    """The result of `republish`: the owner's Listing and how it was recorded, `republish_counted`
    (used one publication) or `republish_free` (used none)."""

    listing: ListingView
    kind: str


def _republish_kind(row, fingerprint: str, now: datetime) -> str:
    """`republish_free` only if the Listing's last COUNTED event (`initial` or `republish_counted`;
    `seed` and `republish_free` events are never a baseline) fell in the same Karachi calendar
    month as `now` (and not later than `now`: a baseline in the future of the clock is not in the
    past, so it never makes a free republication) and carries the same identity fingerprint as the
    Listing has now; otherwise `republish_counted` (no baseline, another month, a baseline later
    than `now`, a null stored fingerprint, or a changed identity field). Called under
    the account and Listing locks, so no other publication can move the baseline meanwhile."""
    baseline = (
        PublicationEvent.objects.filter(
            account_id=row.owner_account_id,
            listing_id=row.id,
            counted=True,
            kind__in=COUNTED_KINDS,
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if baseline is None or baseline.identity_fingerprint != fingerprint:
        return KIND_REPUBLISH_COUNTED
    start, end = quota.month_bounds(now)
    if not start <= baseline.created_at <= now or baseline.created_at >= end:
        return KIND_REPUBLISH_COUNTED
    return KIND_REPUBLISH_FREE


def _run(
    mode: _Mode,
    actor: Actor,
    listing_id,
    base_revision,
    idempotency_key,
    now: datetime | None,
    kind: str | None,
) -> tuple[ListingView, str]:
    """The shared transaction of `publish` and `republish`; returns the owner's Listing and the
    kind of the event that recorded it (for a replay, the original event's kind)."""
    republishing = mode is _REPUBLISH
    seed = kind == KIND_SEED
    key = canonical_uuid(listing_id)
    if seed:
        owner_id = (
            PropertyListing.objects.filter(id=key)
            .values_list("owner_account_id", flat=True)
            .first()
            if key is not None
            else None
        )
        owned = owner_id is not None and identity.is_sample_account(owner_id)  # sample only
        subject = Actor.user(owner_id, actor.request_id) if owned else actor
    else:
        owner_id = actor.account_id
        subject = actor
        owned = (
            key is not None
            and actor.kind is ActorKind.USER
            and PropertyListing.objects.filter(id=key, owner_account_id=owner_id).exists()
        )
    if not owned:
        raise DomainError("not_found")
    problems = _check_shape(base_revision, idempotency_key)
    if problems:
        raise FieldsInvalid(problems)
    with transaction.atomic():
        claim = idempotency.claim(
            mode.scope,
            owner_id,
            idempotency_key,
            idempotency.request_fingerprint({"listing_id": key, "base_revision": base_revision}),
            now if now is not None else timezone.now(),
        )
        if not claim.is_new:
            if claim.result_ref is None:  # a claim without a result: never redo the work
                raise DomainError("conflict")
            replayed = _replay(subject, claim.result_ref)
            original = PublicationEvent.objects.filter(
                account_id=owner_id, idempotency_key=idempotency_key, kind__in=mode.kinds
            ).first()
            if original is None and republishing:  # the kind of the outcome is lost: no guess
                raise DomainError("not_found")
            return replayed, original.kind if original is not None else kind
        account = lock_account(owner_id)
        now = now if now is not None else timezone.now()  # read after the lock wait (month edge)
        done = PublicationEvent.objects.filter(
            account_id=account.id, idempotency_key=idempotency_key
        ).first()
        if done is not None:  # the claim expired or was purged, but this key already published
            if done.listing_id != key or done.kind not in mode.kinds:
                raise DomainError("idempotency_key_reused")  # other listing, or other operation
            claim.complete(f"listing:{done.listing_id}")
            return _replay(subject, f"listing:{done.listing_id}"), done.kind
        row = (
            PropertyListing.objects.select_for_update()
            .filter(id=key, owner_account_id=owner_id)
            .first()
        )
        if row is None:
            raise DomainError("not_found")
        if row.revision != base_revision:
            raise StaleRevision(view_of(row, with_private=True))
        if row.status != mode.source:
            raise DomainError("conflict")  # incl. sold, rented, removed, a Draft, Published
        if row.restricted:  # a restriction freezes the Listing whatever its status (Story 2.17)
            raise DomainError("forbidden")  # it stays in force; moderation lifts it
        _require_state(account)
        view = view_of(row, with_private=True)
        # The FULL re-judge of every stored value (no `touched` set): an Archived edit was only
        # validated field by field, so republication is where the whole Listing is judged again.
        problems = problems_of(row, view, live_image_count(row.id) > 0)
        if problems:
            raise FieldsInvalid(problems)
        fingerprint = identity_fingerprint(view)
        if republishing:
            kind = _republish_kind(row, fingerprint, now)
        counted = not seed and kind != KIND_REPUBLISH_FREE
        if counted:  # a seed or free event needs no allowance and uses none
            allowance = quota.allowance_for(account.id, account.type, now)
            if allowance.remaining < 1:
                raise QuotaExhausted(allowance)
        PublicationEvent.objects.create(
            account_id=account.id,
            listing_id=row.id,
            kind=kind,
            counted=counted,
            identity_fingerprint=fingerprint,
            idempotency_key=idempotency_key,
            created_at=now,
        )
        row.status = ListingStatus.PUBLISHED.value
        row.revision += 1
        row.updated_at = now
        row.delete_after = None  # no longer a Draft that can expire
        row.expiry_notice_sent_at = None
        row.save(
            update_fields=[
                "status",
                "revision",
                "updated_at",
                "delete_after",
                "expiry_notice_sent_at",
            ]
        )
        ListingRevision.objects.create(
            listing=row,
            revision=row.revision,
            origin=Origin.MANUAL.value,
            actor_account_id=owner_id,
            changes={"status": ListingStatus.PUBLISHED.value},
            created_at=now,
        )
        audit.record(actor, mode.action, "listing", row.id, {"kind": kind} if republishing else {})
        claim.complete(f"listing:{row.id}")
        published = view_of(row, with_private=True)
        emit(  # last: after commit
            ListingPublished(listing_id=row.id, account_id=account.id, republished=republishing)
        )
    return published, kind


def publish(
    actor: Actor,
    listing_id,
    base_revision,
    idempotency_key,
    now: datetime | None = None,
    *,
    kind: str = KIND_INITIAL,
) -> ListingView:
    """Publish one of the actor's own Drafts, immediately and at most once per key.

    Refusals before the transaction: Visitor `unauthenticated`; a listing id that is not a
    canonical UUID of a Listing owned by the actor's account `not_found` (another owner, an
    administrator, a system actor and an unknown id are indistinguishable); a bad
    `base_revision` or key `FieldsInvalid` (`idempotency_key` is the field name of the header).
    Inside: `idempotency_key_reused` (same key, other request), `StaleRevision`, `conflict` (not a
    Draft), `email_not_verified` / `publishing_unavailable` / `forbidden` (account state),
    `FieldsInvalid` (every missing or invalid item at once), `QuotaExhausted`. A replay within
    24 hours returns the Listing as it is now, with no second charge, event or audit record.

    `kind="seed"` is for the sample-data command: ONLY the system actor may pass it (any other
    actor is `forbidden`, nothing written; no HTTP route passes it) and the system actor then
    publishes the Draft of a sample account (`not_found` for any other owner). A seed event is
    written with `counted=False`: it needs no allowance and uses none. Everything else
    (idempotency, locks, revision, account state, complete validation, audit, event) is the same.
    Any other `kind` is `validation_error`.
    """
    require_signed_in(actor)
    if kind not in _KINDS:
        raise DomainError("validation_error")
    if kind == KIND_SEED and actor.kind is not ActorKind.SYSTEM:
        raise DomainError("forbidden")
    return _run(_PUBLISH, actor, listing_id, base_revision, idempotency_key, now, kind)[0]


def republish(
    actor: Actor,
    listing_id,
    base_revision,
    idempotency_key,
    now: datetime | None = None,
) -> Republication:
    """Take one of the actor's own Archived Listings back to Published (Story 2.18), at most once
    per key, with every guarantee of `publish`: the same refusals before the transaction, the same
    idempotency, locks, revision check and account-state refusals, and the same complete
    validation of the whole Listing (`FieldsInvalid`, all problems at once, nothing consumed).

    Differences: the Listing must be `archived` (`conflict` for a Draft, Published, Sold, Rented or
    Removed one: a Sold or Rented Listing must first be archived); a restricted Listing is
    `forbidden`. The event is `republish_free` (uncounted, no allowance needed or used) only when
    the Listing's last counted event is from the same Karachi calendar month as `now` and has the
    same identity fingerprint; otherwise `republish_counted`, which needs one remaining allowance
    (`QuotaExhausted`) and becomes the new baseline. A replay returns the original kind and the
    Listing as it is now, with no second charge, event or audit record."""
    require_signed_in(actor)
    result, kind = _run(_REPUBLISH, actor, listing_id, base_revision, idempotency_key, now, None)
    return Republication(result, kind)


def remove_seed_events(actor: Actor, account_ids, *, using: str | None = None) -> int:
    """Delete the `seed` events of the given (sample) accounts: the one place an event is ever
    removed. System actor only (`forbidden` otherwise); never touches another kind. The runtime
    role may only insert and read events, so the sample-data command passes `using`, the
    privileged connection alias (`sample_data.privileged_alias`). Returns the number removed."""
    if actor.kind is not ActorKind.SYSTEM:
        raise DomainError("forbidden")
    ids = list(account_ids)
    if not ids:
        return 0
    return (
        PublicationEvent.objects.using(using or DEFAULT_DB_ALIAS)
        .filter(account_id__in=ids, kind=KIND_SEED, counted=False)
        .delete()[0]
    )


def count_seed_events(account_ids) -> int:
    """How many `seed` events the given accounts have (a read, for the reset preview)."""
    return PublicationEvent.objects.filter(
        account_id__in=list(account_ids), kind=KIND_SEED, counted=False
    ).count()
