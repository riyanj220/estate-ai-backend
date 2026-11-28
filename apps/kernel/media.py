"""Stored images and the per-account storage cap (Story 2.9, AD-15). The only writer of
`MediaAsset` and `StorageUsage`.

`store_image(actor, data, account_type=...)` processes the upload (`kernel.images`, on the runtime
executor), then in ONE transaction locks the account's `StorageUsage` row, refuses with
`storage_cap_exceeded` when the processed size does not fit, writes the file to the store BEFORE the
transaction commits and inserts the row. The file is removed again when the transaction fails (the
database stays authoritative; a crash between the write and the commit leaves a file with no row,
which retention removes). The cap counts the processed (stored) size.

The cap comes from the seeded policy rows `storage_cap_bytes_individual` and
`storage_cap_bytes_agency` (cap policies like `open_drafts`: a null window, edited by an
administrator in Story 2.20). The kernel cannot read an account's type, so the caller passes it.

`delete_asset` removes the row and gives the bytes back in one transaction; the file is deleted
after commit. Lock order across the code base: account, then Listing, then storage usage.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone

from apps.kernel import images, ratelimit, storage
from apps.kernel.actor import Actor, ActorKind, require_signed_in
from apps.kernel.errors import DomainError
from apps.kernel.models import MediaAsset, StorageUsage

logger = logging.getLogger("estate.media")

CAP_SCOPES = {
    "individual": "storage_cap_bytes_individual",
    "agency": "storage_cap_bytes_agency",
}
ORPHAN_AGE = timedelta(hours=24)
BATCH = 500  # most rows, and most stray files, one retention run removes


@dataclass(frozen=True)
class StoredAsset:
    """An asset as callers see it. `storage_key` is internal: never put it on the wire."""

    id: uuid.UUID
    owner_account_id: uuid.UUID
    storage_key: str
    content_type: str
    bytes: int
    width: int
    height: int
    created_at: datetime
    attached_at: datetime | None


def _view(row: MediaAsset) -> StoredAsset:
    return StoredAsset(
        id=row.id,
        owner_account_id=row.owner_account_id,
        storage_key=row.storage_key,
        content_type=row.content_type,
        bytes=row.bytes,
        width=row.width,
        height=row.height,
        created_at=row.created_at,
        attached_at=row.attached_at,
    )


def assets_by_id(asset_ids) -> dict[uuid.UUID, StoredAsset]:
    """The assets that exist among `asset_ids`, by id. Reads only; the caller has already decided
    who may see them (the kernel knows nothing of Listings), and `storage_key` stays internal."""
    ids = list(asset_ids)
    if not ids:
        return {}
    return {row.id: _view(row) for row in MediaAsset.objects.filter(id__in=ids)}


def read_asset(asset_id: uuid.UUID) -> tuple[StoredAsset, bytes] | None:
    """An asset and its stored bytes, or None when the row or the file is gone (a lost file is
    logged by class only, never with its key). No access check: the caller authorises first."""
    row = MediaAsset.objects.filter(id=asset_id).first()
    if row is None:
        return None
    try:
        data = storage.get_storage().get(row.storage_key)
    except (OSError, ValueError, KeyError) as exc:  # a lost file, a bad key: not servable
        logger.warning("media file unreadable (%s)", type(exc).__name__)
        return None
    return _view(row), data


def _require_account_actor(actor: Actor) -> None:
    require_signed_in(actor)
    if actor.kind is not ActorKind.USER:
        raise DomainError("forbidden")  # administrators and the system own no media


def cap_bytes(account_type: str) -> int:
    scope = CAP_SCOPES.get(account_type)
    if scope is None:
        raise ImproperlyConfigured("No storage cap is defined for this account type")
    policy = ratelimit.limit_for(scope)
    if policy.window_seconds is not None:
        raise ImproperlyConfigured(f"Scope {scope} must be a cap (no window)")
    return policy.limit


def _locked_usage(account_id: uuid.UUID) -> StorageUsage:
    StorageUsage.objects.bulk_create([StorageUsage(account_id=account_id)], ignore_conflicts=True)
    return StorageUsage.objects.select_for_update().get(pk=account_id)


def store_image(
    actor: Actor, data: bytes, *, account_type: str, attach: bool = False
) -> StoredAsset:
    """Process and store an upload. See the module note; also raises `images.ImageRejected` and
    `images.Busy`. Callers that hold a transaction open should use `process_upload` first and
    `store_processed` inside it, so the slow work does not run under their locks."""
    _require_account_actor(actor)
    return store_processed(
        actor, images.process_image(data), account_type=account_type, attach=attach
    )


def process_upload(actor: Actor, data: bytes) -> images.ProcessedImage:
    """The processing half of `store_image`: validate and re-encode, touching no stored state."""
    _require_account_actor(actor)
    return images.process_image(data)


def store_processed(
    actor: Actor,
    processed: images.ProcessedImage,
    *,
    account_type: str,
    attach: bool = False,
    listing_id: uuid.UUID | None = None,
) -> StoredAsset:
    """Store an already processed image under the cap. If the caller's own transaction fails after
    this returns, the caller must `discard_file(asset.storage_key)`. The file is filed under the
    account, in the folder of `listing_id` or in its profile folder."""
    _require_account_actor(actor)
    size = len(processed.data)
    key = storage.new_key(actor.account_id, listing_id)
    written = False
    try:
        with transaction.atomic():
            usage = _locked_usage(actor.account_id)
            if usage.bytes_used + size > cap_bytes(account_type):
                raise DomainError("storage_cap_exceeded")
            storage.get_storage().put(key, processed.data)  # before the row and the commit
            written = True
            now = timezone.now()
            row = MediaAsset.objects.create(
                owner_account_id=actor.account_id,
                storage_key=key,
                content_type=processed.content_type,
                bytes=size,
                width=processed.width,
                height=processed.height,
                created_at=now,
                attached_at=now if attach else None,
            )
            usage.bytes_used += size
            usage.save(update_fields=["bytes_used"])
    except BaseException:
        if written:
            storage.discard(key)
        raise
    return _view(row)


def discard_file(storage_key: str) -> None:
    """Compensation for a caller's failed transaction: remove a file, never raising."""
    storage.discard(storage_key)


def _give_back(owner_totals: dict[uuid.UUID, int]) -> None:
    """Subtract bytes from each owner's usage, never below zero, in a stable lock order."""
    for owner in sorted(owner_totals, key=str):
        list(StorageUsage.objects.select_for_update().filter(pk=owner))  # take the row lock
        StorageUsage.objects.filter(pk=owner).update(
            bytes_used=Greatest(F("bytes_used") - owner_totals[owner], 0)
        )


def _delete_after_commit(keys: list[str]) -> None:
    for key in keys:
        # `robust`: one failing delete is logged and never stops the others
        transaction.on_commit(lambda key=key: storage.get_storage().delete(key), robust=True)


def _remove_asset(owner_account_id: uuid.UUID, asset_id: uuid.UUID, missing_ok: bool) -> None:
    with transaction.atomic():
        row = (
            MediaAsset.objects.select_for_update()
            .filter(pk=asset_id, owner_account_id=owner_account_id)
            .first()
        )
        if row is None:
            if missing_ok:
                return
            raise DomainError("not_found")
        _give_back({row.owner_account_id: row.bytes})
        key = row.storage_key
        row.delete()
        _delete_after_commit([key])


def delete_asset(actor: Actor, asset_id: uuid.UUID, *, missing_ok: bool = False) -> None:
    """Remove an asset the actor's account owns: the row and the usage in one transaction, the
    file after commit. An unknown or foreign asset is `not_found`, or a no-op with `missing_ok`
    (the caller's own record is going away anyway; with no row the bytes are unknown, so usage
    is left as it is and a leftover file is found by retention)."""
    _require_account_actor(actor)
    _remove_asset(actor.account_id, asset_id, missing_ok)


def purge_owned_asset(actor: Actor, owner_account_id: uuid.UUID, asset_id: uuid.UUID) -> None:
    """Erasure: remove an asset on behalf of its owner's account, by an administrator or the
    system (an account being deactivated). Same effect as `delete_asset`, and an unknown asset is
    a no-op. A user actor is `forbidden`: owners use `delete_asset`."""
    require_signed_in(actor)
    if actor.kind not in (ActorKind.ADMIN, ActorKind.SYSTEM):
        raise DomainError("forbidden")
    _remove_asset(owner_account_id, asset_id, True)


def has_assets(account_id: uuid.UUID) -> bool:
    """Whether the account still owns any stored asset (reads only)."""
    return MediaAsset.objects.filter(owner_account_id=account_id).exists()


def forget_usage(actor: Actor, account_id: uuid.UUID) -> bool:
    """Erasure: drop the empty storage-usage row of an account that is going away. System actor
    only (`forbidden` otherwise); refused (False, nothing changed) while the account still owns an
    asset or the row still counts bytes. Returns whether a row was removed."""
    require_signed_in(actor)
    if actor.kind is not ActorKind.SYSTEM:
        raise DomainError("forbidden")
    with transaction.atomic():
        if MediaAsset.objects.filter(owner_account_id=account_id).exists():
            return False
        return StorageUsage.objects.filter(pk=account_id, bytes_used=0).delete()[0] > 0


def purge_orphans(now: datetime, dry_run: bool = False) -> int:
    """Retention hook: assets never attached within 24 hours, and stored files with no row that
    are older than 24 hours. At most `BATCH` of each per run; every step is idempotent and a failed
    file never stops the rest. Returns the number removed (or that would be, in dry-run)."""
    cutoff = now - ORPHAN_AGE
    orphans = MediaAsset.objects.filter(attached_at__isnull=True, created_at__lt=cutoff)
    if dry_run:
        return min(orphans.count(), BATCH) + len(_stray_keys(cutoff, dry_run=True))
    strays = _stray_keys(cutoff, dry_run=False)  # first: the rows below still vouch for their files
    rows = list(orphans.select_for_update(skip_locked=True).order_by("created_at")[:BATCH])
    totals: dict[uuid.UUID, int] = {}
    for row in rows:
        totals[row.owner_account_id] = totals.get(row.owner_account_id, 0) + row.bytes
    MediaAsset.objects.filter(pk__in=[row.pk for row in rows]).delete()
    _give_back(totals)
    _delete_after_commit([row.storage_key for row in rows])
    return len(rows) + len(strays)


def _stray_keys(cutoff: datetime, dry_run: bool) -> list[str]:
    """Files older than the cutoff that no row refers to (removed unless `dry_run`), and leftover
    temp files. Walks the store in batches; stops after `BATCH` strays."""
    store = storage.get_storage()
    if not hasattr(store, "iter_old_keys"):
        return []
    seconds = cutoff.timestamp()
    strays: list[str] = []
    batch: list[str] = []

    def check() -> None:
        known = set(
            MediaAsset.objects.filter(storage_key__in=batch).values_list("storage_key", flat=True)
        )
        strays.extend(key for key in batch if key not in known)
        batch.clear()

    for key in store.iter_old_keys(seconds):
        batch.append(key)
        if len(batch) >= BATCH:
            check()
        if len(strays) >= BATCH:
            break
    if batch and len(strays) < BATCH:
        check()
    strays = strays[:BATCH]
    if not dry_run:
        for key in strays:
            storage.discard(key)  # logged by class on failure; the rest go on
        store.remove_old_temp_files(seconds, BATCH)
    return strays
