"""`can(actor, capability)`: the one place capability questions are answered (FR-74)."""

from apps.identity.capabilities import (
    PENDING_CAPABILITIES,
    Access,
    Capability,
    matrix_access,
)
from apps.identity.models import Account, AccountType
from apps.identity.state import AccountState
from apps.kernel.actor import Actor, ActorKind

_ADMIN_CAPABILITIES = frozenset({Capability.SIGN_IN, Capability.BROWSE})


def can(actor: Actor, capability: Capability | str) -> bool:
    capability = Capability(capability)  # an unknown capability is a programming error
    if actor.kind is ActorKind.SYSTEM:
        return True
    if actor.kind is ActorKind.VISITOR:
        return capability is Capability.BROWSE
    is_admin_actor = actor.kind is ActorKind.ADMIN
    if not is_admin_actor and capability in PENDING_CAPABILITIES:
        return False
    account = Account.objects.filter(id=actor.account_id).first()  # one read
    if account is None:
        return False
    if is_admin_actor or account.account_type == AccountType.ADMIN:
        # Administrators sign in and browse only; a deactivated one does neither.
        if account.state is AccountState.DEACTIVATED:
            return False
        return capability in _ADMIN_CAPABILITIES
    return matrix_access(account.state, capability) is Access.ALLOWED


def capabilities_for(account_type: str, state: AccountState) -> list[Capability]:
    """The capabilities `can` grants an account of this type and state, in matrix order.

    Mirrors `can` for a signed-in account: pending capabilities are not listed, administrators
    sign in and browse only, and a deactivated administrator has none.
    """
    if account_type == AccountType.ADMIN:
        if state is AccountState.DEACTIVATED:
            return []
        return [c for c in Capability if c in _ADMIN_CAPABILITIES]
    return [
        c
        for c in Capability
        if c not in PENDING_CAPABILITIES and matrix_access(state, c) is Access.ALLOWED
    ]
