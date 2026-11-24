"""Read path for identity. Returns plain immutable values, never model instances.

Also home of the two primitives other apps use: `lock_account` and `is_active_owner`.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from django.db import connection
from django.utils import timezone

from apps.catalog import selectors as catalog
from apps.identity import sessions
from apps.identity.models import (
    Account,
    AccountType,
    AgencyProfile,
    EmailChangeToken,
    IndividualProfile,
    Session,
)
from apps.identity.policy import capabilities_for
from apps.identity.state import AccountState
from apps.kernel import media, storage
from apps.kernel.errors import DomainError

ACCOUNT_TYPES = tuple(t.value for t in AccountType)
ADMIN_ACCOUNT_TYPE = AccountType.ADMIN.value


@dataclass(frozen=True)
class AccountView:
    """What callers may see of an account: no email, no password hash."""

    id: uuid.UUID
    type: str
    state: AccountState
    suspension_reason: str


@dataclass(frozen=True)
class AccountSnapshot:
    id: uuid.UUID
    type: str
    state: AccountState


def parse_id(account_id) -> uuid.UUID | None:
    if isinstance(account_id, uuid.UUID):
        return account_id
    if not isinstance(account_id, str):
        return None
    try:
        parsed = uuid.UUID(account_id)
    except ValueError:
        return None
    return parsed if str(parsed) == account_id else None  # canonical lowercase form only


def get_account(account_id) -> AccountView | None:
    """Any account by id (including deactivated ones, for administrators); unknown gives None."""
    key = parse_id(account_id)
    account = Account.objects.filter(id=key).first() if key else None
    if account is None:
        return None
    return AccountView(
        id=account.id,
        type=account.account_type,
        state=account.state,
        suspension_reason=account.suspension_reason,
    )


def get_signed_in_account(account_id) -> AccountView | None:
    """The account a session may act as: unknown and deactivated accounts give None."""
    view = get_account(account_id)
    if view is None or view.state is AccountState.DEACTIVATED:
        return None
    return view


def lock_account(account_id) -> AccountSnapshot:
    """Lock the account row until the surrounding transaction ends and return its snapshot.

    The only way to serialise work on one account (publication quota, eligibility checks).
    """
    if not connection.in_atomic_block:
        raise RuntimeError("lock_account must run inside an open transaction")
    key = parse_id(account_id)
    account = Account.objects.select_for_update().filter(id=key).first() if key else None
    if account is None:
        raise DomainError("not_found")
    return AccountSnapshot(id=account.id, type=account.account_type, state=account.state)


def is_active_owner(account_id) -> bool:
    """True only for an active Individual or Agency account (the eligible owner of FR-74)."""
    key = parse_id(account_id)
    account = Account.objects.filter(id=key).first() if key else None
    return (
        account is not None
        and account.state is AccountState.ACTIVE
        and account.account_type in (AccountType.INDIVIDUAL, AccountType.AGENCY)
    )


def active_owner_account_ids():
    """A lazy queryset of the ids of every Active eligible owner (an Active Individual or Agency
    account), for `listings.policy` to use as a subquery. It is the SAME rule as
    `is_active_owner`, written as a filter on the columns the account state derives from
    (verified, not suspended, not Deactivated); a test keeps the two in agreement for every
    account state and type. The ids are not data about anyone beyond "is eligible"."""
    return Account.objects.filter(
        email_verified_at__isnull=False,
        suspended_at__isnull=True,
        deactivated_at__isnull=True,
        account_type__in=(AccountType.INDIVIDUAL, AccountType.AGENCY),
    ).values("id")


@dataclass(frozen=True)
class LiveSession:
    """A valid session and the account it belongs to; never carries the token or its hash."""

    id: uuid.UUID
    account_id: uuid.UUID
    account_type: str
    last_seen_at: datetime


def live_session(token, now: datetime) -> LiveSession | None:
    """The session for a cookie token, or None unless it is open, inside both lifetimes and its
    account exists and is not Deactivated."""
    if not sessions.well_formed(token):
        return None
    row = (
        Session.objects.select_related("account")
        .filter(token_hash=sessions.hash_token(token), ended_at__isnull=True, expires_at__gt=now)
        .first()
    )
    if row is None:
        return None
    # The absolute limit is the stored `expires_at` (set per account type at creation); the idle
    # limit follows the account type now, so an administrator session never outlives 30 idle
    # minutes even if it was stored under longer limits.
    account_type = row.account.account_type
    if row.last_seen_at + sessions.idle_lifetime_for(account_type) <= now:
        return None
    if row.account.state is AccountState.DEACTIVATED:
        return None
    return LiveSession(row.id, row.account_id, row.account.account_type, row.last_seen_at)


@dataclass(frozen=True)
class ActorSummary:
    """What the account's owner may see of it. `email` is private: owner-only, never cached."""

    id: uuid.UUID
    account_type: str
    state: AccountState
    email: str
    full_name: str
    agency_name: str  # public-class; empty for non-agency accounts
    capabilities: tuple[str, ...]


def summarise(account: Account) -> ActorSummary:
    profile = agency = None
    if account.account_type == AccountType.INDIVIDUAL:
        profile = IndividualProfile.objects.filter(account_id=account.id).first()
    elif account.account_type == AccountType.AGENCY:
        agency = AgencyProfile.objects.filter(account_id=account.id).only("agency_name").first()
    return ActorSummary(
        id=account.id,
        account_type=account.account_type,
        state=account.state,
        email=account.email,
        full_name=profile.full_name if profile else "",
        agency_name=agency.agency_name if agency else "",
        capabilities=tuple(c.value for c in capabilities_for(account.account_type, account.state)),
    )


def get_actor_summary(account_id) -> ActorSummary | None:
    """The signed-in account's own summary; unknown and deactivated accounts give None."""
    key = parse_id(account_id)
    account = Account.objects.filter(id=key).first() if key else None
    if account is None or account.state is AccountState.DEACTIVATED:
        return None
    return summarise(account)


OWN_IMAGE_URLS = {
    AccountType.INDIVIDUAL.value: "/api/v1/me/profile/image/file",
    AccountType.AGENCY.value: "/api/v1/me/agency-profile/image/file",
}


def logo_url(agency_id: uuid.UUID) -> str:
    return f"/api/v1/agencies/{agency_id}/logo"


@dataclass(frozen=True)
class ProfileImageView:
    """A stored picture or logo as the owner sees it. No asset id, key or account id."""

    content_type: str
    width: int
    height: int
    bytes: int
    url: str


@dataclass(frozen=True)
class ServedProfileImage:
    content_type: str
    data: bytes
    size: int


def image_view_of(asset: media.StoredAsset, kind: str) -> ProfileImageView:
    """The owner's view of an asset already in hand (no read)."""
    return ProfileImageView(
        content_type=asset.content_type,
        width=asset.width,
        height=asset.height,
        bytes=asset.bytes,
        url=OWN_IMAGE_URLS[kind],
    )


def image_view(asset_id, kind: str) -> ProfileImageView | None:
    """The image a profile points at, or None: no reference, or a ghost (the asset row is gone,
    so there is nothing to show; the next write clears the stale reference)."""
    if asset_id is None:
        return None
    asset = media.assets_by_id([asset_id]).get(asset_id)
    if asset is None:
        return None
    return image_view_of(asset, kind)


@dataclass(frozen=True)
class ProfileView:
    """An Individual's own details. `phone` and both addresses are private: owner only."""

    full_name: str
    phone: str = field(repr=False)
    email: str = field(repr=False)
    pending_email: str | None = field(repr=False)
    image: ProfileImageView | None = None


def _pending_email(account: Account) -> str | None:
    """The newest accepted, unused, unexpired email-change request, whether or not a link was
    sent, so the answer never reveals the address's status."""
    return (
        EmailChangeToken.objects.filter(
            account_id=account.id,
            used_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .order_by("-created_at", "-id")
        .values_list("new_email", flat=True)
        .first()
    )


def profile_view(account: Account, profile: IndividualProfile) -> ProfileView:
    return ProfileView(
        profile.full_name,
        profile.phone,
        account.email,
        _pending_email(account),
        image_view(profile.image_asset_id, AccountType.INDIVIDUAL.value),
    )


def get_profile(actor_account_id) -> ProfileView:
    """The signed-in Individual's profile. Other account types are `forbidden`, unknown or
    Deactivated accounts `unauthenticated`."""
    key = parse_id(actor_account_id)
    account = Account.objects.filter(id=key).first() if key else None
    if account is None or account.state is AccountState.DEACTIVATED:
        raise DomainError("unauthenticated")
    profile = (
        IndividualProfile.objects.filter(account_id=account.id).first()
        if account.account_type == AccountType.INDIVIDUAL
        else None
    )
    if profile is None:
        raise DomainError("forbidden")
    return profile_view(account, profile)


@dataclass(frozen=True)
class AgencyProfileView:
    """An Agency's own details. Phone, address and both emails are private: owner only."""

    agency_name: str
    description: str
    city_id: uuid.UUID
    business_phone: str = field(repr=False)
    office_address: str = field(repr=False)
    email: str = field(repr=False)
    pending_email: str | None = field(repr=False)
    verification_status: str
    image: ProfileImageView | None = None
    agency_id: uuid.UUID | None = None  # public identifier, distinct from the account id
    logo_url: str | None = None


def agency_profile_view(account: Account, profile: AgencyProfile) -> AgencyProfileView:
    image = image_view(profile.image_asset_id, AccountType.AGENCY.value)
    return AgencyProfileView(
        agency_name=profile.agency_name,
        description=profile.description,
        city_id=profile.city_id,
        business_phone=profile.business_phone,
        office_address=profile.office_address,
        email=account.email,
        pending_email=_pending_email(account),
        verification_status=profile.verification_status,
        image=image,
        agency_id=profile.id,
        logo_url=logo_url(profile.id) if image else None,
    )


def get_agency_profile(actor_account_id) -> AgencyProfileView:
    """The signed-in Agency's profile. Other account types are `forbidden`, unknown or
    Deactivated accounts `unauthenticated`."""
    key = parse_id(actor_account_id)
    account = Account.objects.filter(id=key).first() if key else None
    if account is None or account.state is AccountState.DEACTIVATED:
        raise DomainError("unauthenticated")
    profile = (
        AgencyProfile.objects.filter(account_id=account.id).first()
        if account.account_type == AccountType.AGENCY
        else None
    )
    if profile is None:
        raise DomainError("forbidden")
    return agency_profile_view(account, profile)


def city_is_active(city_id) -> bool:
    """True when the id names an active city of the City reference list."""
    return catalog.get_city(city_id) is not None


def own_image_for_serving(
    actor_account_id, kind: str, *, with_bytes: bool = True
) -> ServedProfileImage:
    """THE seam of the owner's picture and logo routes: the bytes of the image of the profile of
    type `kind` that belongs to this account, or `not_found` for everything else (a Visitor, an
    administrator, another account, the other account type, a Deactivated account, no image, a
    lost asset row or file). `with_bytes=False` (HEAD) reads no file: the size is the row's."""
    key = parse_id(actor_account_id)
    account = Account.objects.filter(id=key).first() if key else None
    if account is None or account.state is AccountState.DEACTIVATED or account.account_type != kind:
        raise DomainError("not_found")
    model = IndividualProfile if kind == AccountType.INDIVIDUAL.value else AgencyProfile
    asset_id = (
        model.objects.filter(account_id=account.id).values_list("image_asset_id", flat=True).first()
    )
    return _served(asset_id, with_bytes)


def logo_for_serving(agency_id, *, with_bytes: bool = True) -> ServedProfileImage:
    """The public logo seam: the logo of the Agency profile with this PUBLIC id, only while its
    account is Active. Suspended, Deactivated, Email-unverified, unknown or malformed ids and an
    Agency without a logo (or with a lost one) are all the same `not_found`."""
    key = parse_id(agency_id)
    row = AgencyProfile.objects.select_related("account").filter(id=key).first() if key else None
    if row is None or row.account.state is not AccountState.ACTIVE:
        raise DomainError("not_found")
    return _served(row.image_asset_id, with_bytes)


def _file_exists(storage_key: str) -> bool:
    try:
        return storage.get_storage().exists(storage_key)
    except (OSError, ValueError):
        return False


def _served(asset_id, with_bytes: bool) -> ServedProfileImage:
    if asset_id is None:
        raise DomainError("not_found")
    if not with_bytes:
        asset = media.assets_by_id([asset_id]).get(asset_id)
        if asset is None or not _file_exists(asset.storage_key):
            raise DomainError("not_found")  # HEAD agrees with GET about a lost file
        return ServedProfileImage(asset.content_type, b"", asset.bytes)
    stored = media.read_asset(asset_id)
    if stored is None:
        raise DomainError("not_found")
    asset, data = stored
    return ServedProfileImage(asset.content_type, data, len(data))
