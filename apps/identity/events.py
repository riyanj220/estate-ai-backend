"""Events emitted by identity."""

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID

from apps.identity import models
from apps.kernel.events import DomainEvent, emit

# `replaced` (a new sign-in, or the open-session cap) is internal and never announced.
SESSION_END_REASONS = frozenset(r for r in models.SESSION_END_REASONS if r != "replaced")


@dataclass(frozen=True)
class AccountSessionEnded(DomainEvent):
    """An account's sessions must end. Sign-out and password change reuse it."""

    name: ClassVar[str] = "account.session_ended"
    account_id: UUID
    reason: str
    except_session_id: UUID | None = None  # a password change keeps the session it came from
    ended_before: datetime | None = None  # only sessions created up to then end (never a later one)


def emit_session_ended(
    account_id: UUID,
    reason: str,
    except_session_id: UUID | None = None,
    ended_before: datetime | None = None,
) -> None:
    """Schedule `account.session_ended` for after the surrounding transaction commits."""
    if not isinstance(account_id, UUID):
        raise ValueError("account_id must be a UUID")
    if reason not in SESSION_END_REASONS:
        raise ValueError("unknown session end reason")
    if except_session_id is not None and not isinstance(except_session_id, UUID):
        raise ValueError("except_session_id must be a UUID")
    if ended_before is not None and (
        not isinstance(ended_before, datetime) or ended_before.tzinfo is None
    ):
        raise ValueError("ended_before must be an aware datetime")
    emit(
        AccountSessionEnded(
            account_id=account_id,
            reason=reason,
            except_session_id=except_session_id,
            ended_before=ended_before,
        )
    )


@dataclass(frozen=True)
class AccountRegistered(DomainEvent):
    """A new account was registered and awaits email verification (Story 1.16 sends the link)."""

    name: ClassVar[str] = "account.registered"
    account_id: UUID


def emit_registered(account_id: UUID) -> None:
    """Schedule `account.registered` for after the surrounding transaction commits."""
    if not isinstance(account_id, UUID):
        raise ValueError("account_id must be a UUID")
    emit(AccountRegistered(account_id=account_id))


@dataclass(frozen=True)
class AccountVerificationRequested(DomainEvent):
    """Someone registered again with an address whose account is still unverified."""

    name: ClassVar[str] = "account.verification_requested"
    account_id: UUID


def emit_verification_requested(account_id: UUID) -> None:
    if not isinstance(account_id, UUID):
        raise ValueError("account_id must be a UUID")
    emit(AccountVerificationRequested(account_id=account_id))


@dataclass(frozen=True)
class AccountPasswordResetRequested(DomainEvent):
    """A password reset was asked for an existing, non-deactivated account."""

    name: ClassVar[str] = "account.password_reset_requested"
    account_id: UUID


def emit_password_reset_requested(account_id: UUID) -> None:
    if not isinstance(account_id, UUID):
        raise ValueError("account_id must be a UUID")
    emit(AccountPasswordResetRequested(account_id=account_id))
