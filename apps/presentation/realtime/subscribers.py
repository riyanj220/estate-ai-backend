"""Domain events turned into channel-layer messages (AD-13, AD-18). They run after commit.

`messaging` names the recipients from the Conversation row; nothing here, and nothing a client
sends, can add one.
"""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.identity.events import AccountSessionEnded, AccountSuspended
from apps.kernel import events
from apps.messaging.events import MessageSent
from apps.presentation.realtime import protocol
from apps.presentation.realtime.consumers import group_of


def _send(account_id, message: dict) -> None:
    async_to_sync(get_channel_layer().group_send)(group_of(account_id), message)


def push_message(event: MessageSent) -> None:
    payload = protocol.message_created(event.conversation_id, event.message_id, event.seq)
    for account_id in set(event.recipient_account_ids):
        _send(account_id, {"type": "live.event", "event": payload})


def check_sessions(event: AccountSessionEnded | AccountSuspended) -> None:
    """Every socket of the account checks its own session again and closes if it ended (a
    sign-out ends one session, a password change all but one)."""
    _send(event.account_id, {"type": "session.check"})


def register() -> None:
    """Subscribe unless already present (safe to call again after `events.clear()`)."""
    for event_type, handler in (
        (MessageSent, push_message),
        (AccountSessionEnded, check_sessions),
        (AccountSuspended, check_sessions),
    ):
        if not events.is_subscribed(event_type, handler):
            events.subscribe(event_type, handler)
