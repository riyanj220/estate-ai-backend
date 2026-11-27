"""The FR-74 capability matrix. Pure Python: a complete table and a function that reads it."""

from enum import StrEnum

from apps.identity.state import AccountState


class Capability(StrEnum):
    SIGN_IN = "sign_in"
    BROWSE = "browse"
    VIEW_DRAFTS = "view_drafts"
    EDIT_DRAFTS = "edit_drafts"
    PUBLISH = "publish"
    SEND_MESSAGES = "send_messages"
    READ_CONVERSATIONS = "read_conversations"
    REPORT = "report"
    REQUEST_SUPPORT = "request_support"
    REQUEST_DEACTIVATION = "request_deactivation"


class Access(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"


_ALL = frozenset(Capability)
_C = Capability

_ALLOWED: dict[AccountState, frozenset[Capability]] = {
    AccountState.EMAIL_UNVERIFIED: _ALL - {_C.PUBLISH, _C.SEND_MESSAGES},
    AccountState.ACTIVE: _ALL,
    # Read-only: may view drafts and conversations; support and deactivation requests only.
    AccountState.SUSPENDED: frozenset(
        {
            _C.SIGN_IN,
            _C.BROWSE,
            _C.VIEW_DRAFTS,
            _C.READ_CONVERSATIONS,
            _C.REQUEST_SUPPORT,
            _C.REQUEST_DEACTIVATION,
        }
    ),
    # Browse at Visitor level only.
    AccountState.DEACTIVATED: frozenset({_C.BROWSE}),
}

MATRIX: dict[AccountState, dict[Capability, Access]] = {
    state: {cap: Access.ALLOWED if cap in allowed else Access.DENIED for cap in Capability}
    for state, allowed in _ALLOWED.items()
}

# Capabilities whose features arrive in later stories. `can` denies them until the owning story
# removes them from this set (in its own change, with its own test).
PENDING_CAPABILITIES: frozenset[Capability] = _ALL - {
    _C.SIGN_IN,
    _C.BROWSE,
    _C.VIEW_DRAFTS,  # Story 2.1
    _C.EDIT_DRAFTS,  # Story 2.1
    _C.SEND_MESSAGES,  # Story 7.1
    _C.READ_CONVERSATIONS,  # Story 7.1
    _C.REPORT,  # Story 9.1
}


def matrix_access(state: AccountState, capability: Capability) -> Access:
    """The FR-74 cell, ignoring whether the capability is pending."""
    return MATRIX[AccountState(state)][Capability(capability)]
