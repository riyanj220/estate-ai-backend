"""Valuation's share of an account erasure (AD-23): the estimates stored under the account."""

from apps.kernel import audit
from apps.kernel.actor import Actor, require_admin
from apps.valuation.models import PriceEstimate


def erase_account_data(actor: Actor, account_id) -> None:
    require_admin(actor)
    PriceEstimate.objects.filter(account_id=account_id).delete()
    audit.record(actor, "valuation.account_erased", "account", account_id)
