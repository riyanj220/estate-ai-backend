"""Identity's share of an account erasure (AD-23, FR-8): anonymise the personal fields."""

from apps.identity import services
from apps.kernel.actor import Actor


def erase_account_data(actor: Actor, account_id) -> None:
    services.anonymise_account(actor, account_id)
