"""Cookie-session authentication and CSRF enforcement (Story 1.17).

The cookie is read here and nowhere else in the request path. A valid session sets
`request.authenticated_identity`, which `actors.resolve_actor` turns into an Actor. An unknown,
expired or ended cookie is simply a Visitor, never an error.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from django.conf import settings
from django.db import DatabaseError
from django.middleware.csrf import CsrfViewMiddleware
from django.utils import timezone
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication

from apps.identity import selectors, services
from apps.identity.sessions import COOKIE_NAME, touch_interval
from apps.kernel.actor import Actor, ActorKind
from apps.kernel.errors import DomainError

logger = logging.getLogger("estate.session")
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


@dataclass(frozen=True)
class Identity:
    kind: str
    account_id: UUID


class _CsrfCheck(CsrfViewMiddleware):
    def _reject(self, request, reason):
        return reason


def enforce_csrf(request) -> None:
    """Raise `forbidden` unless the request carries a valid CSRF token (and a trusted origin).

    DRF views are CSRF-exempt by default, so cookie-authenticated unsafe requests and sign-in
    run Django's own check here. The token is read from the `X-CSRFToken` header.
    """
    http_request = getattr(request, "_request", request)
    check = _CsrfCheck(lambda r: None)
    check.process_request(http_request)
    if check.process_view(http_request, None, (), {}) is not None:
        raise DomainError("forbidden")


class SessionAuthentication(BaseAuthentication):
    def authenticate(self, request):
        token = request.COOKIES.get(COOKIE_NAME)
        if not token:
            return None
        now = timezone.now()
        session = selectors.live_session(token, now)
        if session is None:
            return None
        if request.method not in SAFE_METHODS:
            enforce_csrf(request)
        if now - session.last_seen_at >= touch_interval():
            try:
                services.refresh_session(Actor.system(), session.id)
            except DatabaseError as exc:  # a failed touch never fails the request
                logger.warning(
                    "session_touch_failure",
                    extra={"event": "session_touch_failure", "exception_class": type(exc).__name__},
                )
        kind = ActorKind.ADMIN if session.account_type == "admin" else ActorKind.USER
        request._request.authenticated_identity = Identity(kind.value, session.account_id)
        return (None, session)


def cookie_kwargs() -> dict:
    return {
        "httponly": True,
        "samesite": "Lax",
        "secure": settings.SESSION_COOKIE_SECURE,
        "path": "/",
    }


class SessionAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = SessionAuthentication
    name = "sessionCookie"

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "cookie", "name": COOKIE_NAME}
