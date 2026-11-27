"""The one writer of an Agency's verified flag (Stories 8.3/8.4, AD-3).

`governance` owns the requests and decisions and calls this inside its own transaction, which
also writes the audit record. No Agency-side action reaches it.
"""

from django.db import transaction

from apps.identity.models import AgencyProfile
from apps.kernel.actor import Actor, require_admin
from apps.kernel.errors import DomainError


def set_agency_verification(actor: Actor, account_id, verified: bool) -> None:
    """Grant or remove the verified flag. An account without an Agency profile is `not_found`."""
    require_admin(actor)
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("set_agency_verification must run inside the decision's transaction")
    profile = AgencyProfile.objects.select_for_update().filter(account_id=account_id).first()
    if profile is None:
        raise DomainError("not_found")
    profile.verification_status = "verified" if verified else "unverified"
    profile.save(update_fields=["verification_status", "updated_at"])
