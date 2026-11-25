"""Publishing a Draft (AD-7, Story 2.15). The ONLY code that sets `ListingStatus.PUBLISHED` and
the only writer of `PublicationEvent` (an architecture guard enforces both).

`publish` runs in one transaction, in this order: claim the idempotency key; lock the account and
then the Listing row; compare `base_revision`; require a Draft; check the account state; validate
the Listing; check the monthly allowance; write the counted `initial` event with the identity
fingerprint; set the status and bump the revision; write the audit record; complete the claim;
emit `listing.published` (last, after commit). Any refusal or error rolls everything back, the
claim included, so nothing is consumed and the same key may be retried. No AI call, upload, mail
or notification happens here.
"""

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
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
    seed = kind == KIND_SEED
    if seed and actor.kind is not ActorKind.SYSTEM:
        raise DomainError("forbidden")
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
            SCOPE,
            owner_id,
            idempotency_key,
            idempotency.request_fingerprint({"listing_id": key, "base_revision": base_revision}),
            now if now is not None else timezone.now(),
        )
        if not claim.is_new:
            if claim.result_ref is None:  # a claim without a result: never redo the work
                raise DomainError("conflict")
            return _replay(subject, claim.result_ref)
        account = lock_account(owner_id)
        now = now if now is not None else timezone.now()  # read after the lock wait (month edge)
        done = PublicationEvent.objects.filter(
            account_id=account.id, idempotency_key=idempotency_key
        ).first()
        if done is not None:  # the claim expired or was purged, but this key already published
            if done.listing_id != key:
                raise DomainError("idempotency_key_reused")
            claim.complete(f"listing:{done.listing_id}")
            return _replay(subject, f"listing:{done.listing_id}")
        row = (
            PropertyListing.objects.select_for_update()
            .filter(id=key, owner_account_id=owner_id)
            .first()
        )
        if row is None:
            raise DomainError("not_found")
        if row.revision != base_revision:
            raise StaleRevision(view_of(row, with_private=True))
        if row.status != ListingStatus.DRAFT.value:
            raise DomainError("conflict")
        _require_state(account)
        view = view_of(row, with_private=True)
        problems = problems_of(row, view, live_image_count(row.id) > 0)
        if problems:
            raise FieldsInvalid(problems)
        if not seed:  # a seed event is uncounted: it needs no allowance and uses none
            allowance = quota.allowance_for(account.id, account.type, now)
            if allowance.remaining < 1:
                raise QuotaExhausted(allowance)
        PublicationEvent.objects.create(
            account_id=account.id,
            listing_id=row.id,
            kind=kind,
            counted=not seed,
            identity_fingerprint=identity_fingerprint(view),
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
        audit.record(actor, "listing.published", "listing", row.id, {})
        claim.complete(f"listing:{row.id}")
        published = view_of(row, with_private=True)
        emit(ListingPublished(listing_id=row.id, account_id=account.id))  # last: after commit
    return published


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
