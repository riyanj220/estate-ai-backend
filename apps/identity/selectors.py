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
from apps.kernel.errors import DomainError

ACCOUNT_TYPES = tuple(t.value for t in AccountType)


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
    if row is None or row.last_seen_at + sessions.idle_lifetime() <= now:
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


@dataclass(frozen=True)
class ProfileView:
    """An Individual's own details. `phone` and both addresses are private: owner only."""

    full_name: str
    phone: str = field(repr=False)
    email: str = field(repr=False)
    pending_email: str | None = field(repr=False)


def profile_view(account: Account, profile: IndividualProfile) -> ProfileView:
    pending = (
        EmailChangeToken.objects.filter(
            account_id=account.id,
            used_at__isnull=True,
            link_issued=True,
            expires_at__gt=timezone.now(),
        )
        .order_by("-created_at", "-id")
        .values_list("new_email", flat=True)
        .first()
    )
    return ProfileView(profile.full_name, profile.phone, account.email, pending)


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


def city_is_active(city_id) -> bool:
    """True when the id names an active city of the City reference list."""
    return catalog.get_city(city_id) is not None
