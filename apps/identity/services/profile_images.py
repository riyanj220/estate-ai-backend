"""Profile picture (Individual) and logo (Agency) writes (Story 2.11).

The asset is the kernel's `MediaAsset`, made by the Story 2.9 pipeline; the profile keeps only its
id (`image_asset_id`, a plain column). Account-state gate: exactly the one the profile PATCH uses
(`update_profile` / `update_agency_profile`): a Deactivated account is `unauthenticated`, the
wrong account type `forbidden`, everything else (Active, Email-unverified AND Suspended) may edit.
No new capability is invented. Lock order: account, profile, storage usage.
"""

from django.db import transaction

from apps.identity.models import Account, AccountType, AgencyProfile, IndividualProfile
from apps.identity.selectors import ProfileImageView, image_view_of, parse_id
from apps.identity.services._common import _require_owner_actor
from apps.identity.state import AccountState
from apps.kernel import audit, media
from apps.kernel.actor import Actor, ActorKind, require_signed_in
from apps.kernel.errors import DomainError

INDIVIDUAL = AccountType.INDIVIDUAL.value
AGENCY = AccountType.AGENCY.value
_MODELS = {INDIVIDUAL: IndividualProfile, AGENCY: AgencyProfile}


def _require_kind(kind: str) -> None:
    if kind not in _MODELS:
        raise ValueError("kind must be 'individual' or 'agency'")


def require_profile_image_access(actor: Actor, *, kind: str) -> None:
    """The refusals that need no body: Visitor `unauthenticated`, administrator or system
    `forbidden`, a Deactivated or unknown account `unauthenticated`, the other account type
    `forbidden`. The API runs it BEFORE it looks at the upload."""
    _require_kind(kind)
    _require_owner_actor(actor)
    account = Account.objects.filter(id=actor.account_id).first()
    if account is None or account.state is AccountState.DEACTIVATED:
        raise DomainError("unauthenticated")
    if account.account_type != kind or not _MODELS[kind].objects.filter(account=account).exists():
        raise DomainError("forbidden")


def _locked(actor: Actor, kind: str):
    """Inside a transaction: the account, then its profile, both locked and judged again."""
    account = Account.objects.select_for_update().filter(id=actor.account_id).first()
    if account is None or account.state is AccountState.DEACTIVATED:
        raise DomainError("unauthenticated")
    profile = (
        _MODELS[kind].objects.select_for_update().filter(account_id=account.id).first()
        if account.account_type == kind
        else None
    )
    if profile is None:
        raise DomainError("forbidden")
    return account, profile


def set_profile_image(actor: Actor, data: bytes, *, kind: str) -> ProfileImageView:
    """Upload or replace the signed-in account's picture (`kind` individual) or logo (agency).

    The upload is validated and re-encoded OUTSIDE any lock (`ImageRejected`, `Busy`). In one
    transaction under the account lock the previous asset is deleted (row, usage given back, file
    after commit) so a replacement counts only its net size against the cap, the new asset is
    stored attached (`storage_cap_exceeded`), the profile points at it and
    `account.profile_image_changed` is audited. A failure anywhere rolls everything back, and the
    new file is removed again, so the old image stays intact. A reference to a lost asset row is
    simply replaced.
    """
    require_profile_image_access(actor, kind=kind)
    processed = media.process_upload(actor, data)
    asset = None
    try:
        with transaction.atomic():
            account, profile = _locked(actor, kind)
            if profile.image_asset_id is not None:
                media.delete_asset(actor, profile.image_asset_id, missing_ok=True)
            asset = media.store_processed(
                actor, processed, account_type=account.account_type, attach=True
            )
            profile.image_asset_id = asset.id
            profile.save(update_fields=["image_asset_id", "updated_at"])
            audit.record(actor, "account.profile_image_changed", "account", account.id)
            view = image_view_of(asset, kind)  # from what this transaction stored: no re-read
    except BaseException:
        if asset is not None:
            media.discard_file(asset.storage_key)  # the row rolled back; no file may outlive it
        raise
    return view


def remove_profile_image(actor: Actor, *, kind: str) -> None:
    """Remove the picture or logo: the reference, the asset row, its usage and (after commit) its
    file. Repeating it, or removing when there is none, changes and records nothing."""
    require_profile_image_access(actor, kind=kind)
    with transaction.atomic():
        account, profile = _locked(actor, kind)
        asset_id = profile.image_asset_id
        if asset_id is None:
            return
        profile.image_asset_id = None
        profile.save(update_fields=["image_asset_id", "updated_at"])
        media.delete_asset(actor, asset_id, missing_ok=True)  # a lost row is no obstacle
        audit.record(actor, "account.profile_image_changed", "account", account.id)


def purge_profile_image(actor: Actor, account_id) -> None:
    """Erasure hook (AD-23): remove the account's picture or logo asset (row, usage, file after
    commit) and clear the reference, inside the caller's transaction when there is one. Called by
    `deactivate`; an administrator or the system only. Nothing to remove is a no-op. The caller
    writes the audit record of the account change itself."""
    require_signed_in(actor)
    if actor.kind not in (ActorKind.ADMIN, ActorKind.SYSTEM):
        raise DomainError("forbidden")
    key = parse_id(account_id)
    if key is None:
        raise DomainError("not_found")
    with transaction.atomic():
        for model in _MODELS.values():
            profile = model.objects.select_for_update().filter(account_id=key).first()
            if profile is None or profile.image_asset_id is None:
                continue
            asset_id = profile.image_asset_id
            profile.image_asset_id = None
            profile.save(update_fields=["image_asset_id", "updated_at"])
            media.purge_owned_asset(actor, key, asset_id)
