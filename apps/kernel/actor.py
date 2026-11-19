"""Who is acting (AD-22). Pure Python: no Django, DRF or request import.

An Actor is built once per request by the presentation resolver and passed first
to every service, so services never read ambient request state.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from apps.kernel.errors import DomainError


class ActorKind(StrEnum):
    VISITOR = "visitor"
    USER = "user"
    ADMIN = "admin"
    SYSTEM = "system"


_NEEDS_ACCOUNT = {ActorKind.USER, ActorKind.ADMIN}


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    request_id: str = ""
    account_id: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ActorKind):
            raise ValueError("kind must be an ActorKind")
        if self.account_id is not None and not isinstance(self.account_id, UUID):
            raise ValueError("account_id must be a UUID")
        if self.kind in _NEEDS_ACCOUNT and self.account_id is None:
            raise ValueError(f"a {self.kind.value} actor needs an account_id")
        if self.kind not in _NEEDS_ACCOUNT and self.account_id is not None:
            raise ValueError(f"a {self.kind.value} actor cannot carry an account_id")

    @classmethod
    def visitor(cls, request_id: str = "") -> "Actor":
        return cls(ActorKind.VISITOR, request_id)

    @classmethod
    def user(cls, account_id: UUID, request_id: str = "") -> "Actor":
        return cls(ActorKind.USER, request_id, account_id)

    @classmethod
    def admin(cls, account_id: UUID, request_id: str = "") -> "Actor":
        return cls(ActorKind.ADMIN, request_id, account_id)

    @classmethod
    def system(cls, request_id: str = "") -> "Actor":
        return cls(ActorKind.SYSTEM, request_id)

    @property
    def is_visitor(self) -> bool:
        return self.kind is ActorKind.VISITOR

    @property
    def is_admin(self) -> bool:
        return self.kind is ActorKind.ADMIN


def require_signed_in(actor: Actor) -> None:
    """Visitors get `unauthenticated`; every other kind passes."""
    if actor.is_visitor:
        raise DomainError("unauthenticated")


def require_admin(actor: Actor) -> None:
    """Visitors get `unauthenticated`; other non-admins get `not_found` (admin stays hidden).

    Rule: a system actor passes `require_signed_in` but is refused here like any non-admin.
    """
    require_signed_in(actor)
    if not actor.is_admin:
        raise DomainError("not_found")
