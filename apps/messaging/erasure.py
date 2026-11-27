"""Messaging's share of an account erasure (FR-8): nothing is deleted. Conversations and
Messages are kept for the other participant; the sender shows as "deleted user" because the
header takes the name from identity, which gives none for a Deactivated account.
"""

from apps.kernel import audit
from apps.kernel.actor import Actor, require_admin


def erase_account_data(actor: Actor, account_id) -> None:
    require_admin(actor)
    audit.record(actor, "messaging.account_erased", "account", account_id)
