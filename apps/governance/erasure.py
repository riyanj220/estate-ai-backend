"""Governance's share of an account erasure (AD-23, PRD 8.3): Conversation Report snapshots are
anonymised and Verification evidence is removed. Verification uploads no file, so the evidence is
the text of the requests. Decision history, Reports and moderation records are kept.
"""

from apps.governance import reports, services
from apps.kernel import audit
from apps.kernel.actor import Actor, require_admin


def erase_account_data(actor: Actor, account_id) -> None:
    require_admin(actor)
    reports.anonymise_participant(account_id)  # in this transaction, not left to the event
    services.erase_verification(actor, account_id)
    audit.record(actor, "governance.account_erased", "account", account_id)
