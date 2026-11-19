"""The one place a request becomes an Actor (AD-22).

`SessionAuthentication` (Story 1.17) sets `request.authenticated_identity` from a verified
session; for anything unrecognised the caller is a Visitor. No client header, cookie or body
value is ever read here.
"""

from uuid import UUID

from apps.kernel.actor import Actor, ActorKind

_SIGNED_IN_KINDS = {ActorKind.USER.value: Actor.user, ActorKind.ADMIN.value: Actor.admin}


def _parse_account_id(raw) -> UUID | None:
    """Only the canonical lowercase hyphenated form; the nil UUID is never an account."""
    if isinstance(raw, UUID):
        value = raw
    elif isinstance(raw, str) and len(raw) == 36:
        try:
            value = UUID(raw)
        except ValueError:
            return None
        if str(value) != raw:
            return None
    else:
        return None
    return None if value.int == 0 else value


def resolve_actor(request) -> Actor:
    request_id = getattr(request, "request_id", "")
    if not isinstance(request_id, str):
        request_id = ""
    identity = getattr(request, "authenticated_identity", None)
    build = _SIGNED_IN_KINDS.get(str(getattr(identity, "kind", "")))
    if identity is None or build is None:
        return Actor.visitor(request_id)
    raw_id = getattr(identity, "account_id", None)
    account_id = _parse_account_id(raw_id)
    if account_id is None:
        return Actor.visitor(request_id)
    return build(account_id, request_id)


def get_actor(request) -> Actor:
    """Resolve once per request and attach it as `request.actor`."""
    if (actor := getattr(request, "actor", None)) is not None:
        return actor
    actor = resolve_actor(request)
    target = getattr(request, "_request", request)
    target.actor = actor
    if target is not request:
        request.actor = actor
    return actor
