"""Events emitted by identity."""

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from apps.kernel.events import DomainEvent, emit

SESSION_END_REASONS = frozenset({"sign_out", "password_changed", "deactivated"})


@dataclass(frozen=True)
class AccountSessionEnded(DomainEvent):
    """An account's sessions must end. Sign-out and password change reuse it."""

    name: ClassVar[str] = "account.session_ended"
    account_id: UUID
    reason: str


def emit_session_ended(account_id: UUID, reason: str) -> None:
    """Schedule `account.session_ended` for after the surrounding transaction commits."""
    if not isinstance(account_id, UUID):
        raise ValueError("account_id must be a UUID")
    if reason not in SESSION_END_REASONS:
        raise ValueError("unknown session end reason")
    emit(AccountSessionEnded(account_id=account_id, reason=reason))
