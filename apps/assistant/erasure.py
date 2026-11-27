"""The Assistant's share of an account erasure (AD-23): its history goes (turns cascade)."""

from apps.assistant.models import AssistantSession
from apps.kernel import audit
from apps.kernel.actor import Actor, require_admin


def erase_account_data(actor: Actor, account_id) -> None:
    require_admin(actor)
    AssistantSession.objects.filter(account_id=account_id).delete()
    audit.record(actor, "assistant.account_erased", "account", account_id)
