"""Account state (FR-74). Pure Python: derived from three separate conditions, never stored."""

from enum import StrEnum


class AccountState(StrEnum):
    EMAIL_UNVERIFIED = "email_unverified"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


def derive_state(*, email_verified: bool, suspended: bool, deactivated: bool) -> AccountState:
    """Deactivated beats suspended beats email-unverified beats active."""
    if deactivated:
        return AccountState.DEACTIVATED
    if suspended:
        return AccountState.SUSPENDED
    if not email_verified:
        return AccountState.EMAIL_UNVERIFIED
    return AccountState.ACTIVE
