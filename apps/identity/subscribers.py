"""Identity's event handlers. Registered from `AppConfig.ready`; they run after commit."""

from django.db import transaction

from apps.identity import notices, services
from apps.identity.events import (
    AccountRegistered,
    AccountSessionEnded,
    AccountVerificationRequested,
)
from apps.kernel import events
from apps.kernel.actor import Actor


def send_verification_email(event: AccountRegistered | AccountVerificationRequested) -> None:
    """Issue a fresh single-use link and queue the email, in a transaction of its own."""
    with transaction.atomic():
        issued = services.issue_verification_token(Actor.system(), event.account_id)
        if issued is not None:
            notices.send_verification(*issued)


def end_sessions(event: AccountSessionEnded) -> None:
    """A password change or deactivation ends every session; a sign-out ended its own already."""
    if event.reason in ("password_changed", "deactivated"):
        services.end_account_sessions(Actor.system(), event.account_id, event.reason)


def register() -> None:
    """Subscribe the handlers unless already present (safe to call again after `events.clear()`)."""
    for event_type in (AccountRegistered, AccountVerificationRequested):
        if not events.is_subscribed(event_type, send_verification_email):
            events.subscribe(event_type, send_verification_email)
    if not events.is_subscribed(AccountSessionEnded, end_sessions):
        events.subscribe(AccountSessionEnded, end_sessions)
