"""Read path for identity. Returns plain immutable values, never model instances.

Also home of the two primitives other apps use: `lock_account` and `is_active_owner`.
"""

import uuid
from dataclasses import dataclass

from django.db import connection

from apps.identity.models import Account, AccountType
from apps.identity.state import AccountState
from apps.kernel.errors import DomainError


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
