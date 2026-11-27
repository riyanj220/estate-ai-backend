"""Who may hold a socket (AD-13): the session cookie and the exact Origin, nothing the client
names. The same session rules as HTTP; an open socket never counts as activity, so it does not
keep a session alive."""

import uuid
from urllib.parse import urlsplit

from django.conf import settings
from django.http.cookie import parse_cookie
from django.utils import timezone

from apps.identity import selectors as identity
from apps.identity.capabilities import Capability
from apps.identity.policy import can
from apps.identity.sessions import COOKIE_NAME
from apps.identity.state import AccountState
from apps.kernel.actor import Actor

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(value: str) -> tuple[str, str, int] | None:
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname or "@" in parts.netloc:
        return None
    if parts.path or parts.query or parts.fragment:
        return None
    return scheme, parts.hostname, port or _DEFAULT_PORTS[scheme]


def _header(scope, name: bytes) -> list[str]:
    return [v.decode("latin-1") for k, v in scope.get("headers", []) if k.lower() == name]


def origin_allowed(scope) -> bool:
    """Exactly one Origin, equal in scheme, host and port to a configured one."""
    sent = _header(scope, b"origin")
    wanted = _origin(sent[0]) if len(sent) == 1 else None
    return wanted is not None and wanted in {_origin(o) for o in settings.CSRF_TRUSTED_ORIGINS}


def session_token(scope) -> str:
    cookies = parse_cookie("; ".join(_header(scope, b"cookie")))
    return cookies.get(COOKIE_NAME, "")


def live_account(token: str) -> uuid.UUID | None:
    """The account that may receive events on this session right now, or None: no live session,
    an administrator, or an account that is Suspended or Deactivated. Database work: call it off
    the event loop."""
    session = identity.live_session(token, timezone.now())
    if session is None or session.account_type == identity.ADMIN_ACCOUNT_TYPE:
        return None
    account = identity.get_account(session.account_id)
    if account is None or account.state in (AccountState.SUSPENDED, AccountState.DEACTIVATED):
        return None
    if not can(Actor.user(account.id), Capability.READ_CONVERSATIONS):
        return None
    return account.id
