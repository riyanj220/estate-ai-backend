"""Rate limits (AD-25): policy rows plus fixed-window counters in PostgreSQL.

Windows are fixed, not sliding: a client can burst up to twice the limit across a
window boundary. That is accepted for the prototype in exchange for one atomic
statement per hit. If the counter store fails the hit is allowed and only the
exception class is logged (fail open): a database outage already fails the request,
and a throttle fault must not lock users out.
"""

import hashlib
import hmac
import ipaddress
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, connection, transaction

from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import RateLimitPolicy
from apps.kernel.network import client_ip

logger = logging.getLogger("estate.ratelimit")

_UPSERT = """
INSERT INTO kernel_ratelimitcounter (id, scope, key_hash, window_start, count)
VALUES (%s, %s, %s, %s, 1)
ON CONFLICT (scope, key_hash, window_start)
DO UPDATE SET count = LEAST(kernel_ratelimitcounter.count + 1, %s)
RETURNING count
"""

_PEEK = """
SELECT count FROM kernel_ratelimitcounter
WHERE scope = %s AND key_hash = %s AND window_start = %s
"""

_ENDED = """
FROM kernel_ratelimitcounter c
WHERE NOT EXISTS (
    SELECT 1 FROM kernel_ratelimitpolicy p
    WHERE p.scope = c.scope
      AND p.window_seconds IS NOT NULL
      AND c.window_start + p.window_seconds * interval '1 second' > %s
)
"""


@dataclass(frozen=True)
class Policy:
    scope: str
    limit: int
    window_seconds: int | None
    key_kind: str


@dataclass(frozen=True)
class Decision:
    allowed: bool
    count: int
    limit: int
    retry_after: int = 0


class RateLimited(DomainError):
    """A limited request; the API layer answers 429 with a `Retry-After` header."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("throttled")
        self.retry_after = retry_after


def derive_key(*parts: str) -> str:
    """HMAC of the parts under SECRET_KEY, so no raw address, session or id is stored."""
    message = "\x00".join(parts).encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def _network_part(address: str) -> str:
    """IPv6 clients are keyed by their /64, since one subscriber controls a whole /64."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return address
    if parsed.version == 6:
        return str(ipaddress.ip_network(f"{parsed}/64", strict=False))
    return address


def client_network(request) -> str:
    """The trusted client address (IPv6 as its /64), for composite keys built with `derive_key`."""
    return _network_part(client_ip(request))


def client_key(actor: Actor, request, key_kind: str) -> str:
    """Key for a policy's `key_kind`.

    `account` needs a signed-in actor. `client` always uses the trusted client address
    (IPv6 as /64) plus session, even for signed-in actors, so it cannot be dodged by signing in.
    """
    if key_kind == RateLimitPolicy.KEY_ACCOUNT:
        if actor.account_id is None:
            raise ImproperlyConfigured("An account-keyed scope needs a signed-in actor")
        return derive_key("account", str(actor.account_id))
    if key_kind != RateLimitPolicy.KEY_CLIENT:
        raise ImproperlyConfigured(f"Unknown key kind: {key_kind}")
    session = getattr(request, "session", None)
    session_key = getattr(session, "session_key", None) or ""
    return derive_key("visitor", _network_part(client_ip(request)), session_key)


def limit_for(scope: str) -> Policy:
    try:
        row = RateLimitPolicy.objects.get(scope=scope)
    except RateLimitPolicy.DoesNotExist:
        raise ImproperlyConfigured(f"Unknown rate-limit scope: {scope}") from None
    return Policy(row.scope, row.limit, row.window_seconds, row.key_kind)


def _aware(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now


def _log_store_failure(exc: Exception) -> None:
    logger.warning(
        "ratelimit_store_failure",
        extra={"event": "ratelimit_store_failure", "exception_class": type(exc).__name__},
    )


def _lookup(scope: str) -> Policy | None:
    """Policy read with the same fail-open rule; an unknown scope still raises."""
    try:
        with transaction.atomic():
            return limit_for(scope)
    except DatabaseError as exc:
        _log_store_failure(exc)
        return None


def _count(policy: Policy, key: str, now: datetime | None) -> Decision:
    if policy.window_seconds is None:
        raise ImproperlyConfigured(f"Scope {policy.scope} is a cap; count against limit_for(scope)")
    now = _aware(now)
    epoch = int(now.timestamp())
    start = epoch - epoch % policy.window_seconds
    params = [
        uuid.uuid4(),
        policy.scope,
        key,
        datetime.fromtimestamp(start, UTC),
        policy.limit + 1,  # stored count is capped so over-limit hits cannot grow it
    ]
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(_UPSERT, params)
            count = cursor.fetchone()[0]
    except DatabaseError as exc:
        _log_store_failure(exc)
        return Decision(True, 0, policy.limit)
    if count <= policy.limit:
        return Decision(True, count, policy.limit)
    return Decision(False, count, policy.limit, max(1, start + policy.window_seconds - epoch))


def hit(scope: str, key: str, now: datetime | None = None) -> Decision:
    _aware(now)
    policy = _lookup(scope)
    if policy is None:
        return Decision(True, 0, 0)
    return _count(policy, key, now)


def peek(scope: str, key: str, now: datetime | None = None) -> bool:
    """True when the key has reached the limit in the current window. Never counts a hit.

    Fails open like `hit`: a store fault reports not reached.
    """
    now = _aware(now)
    policy = _lookup(scope)
    if policy is None:
        return False
    if policy.window_seconds is None:
        raise ImproperlyConfigured(f"Scope {scope} is a cap; count against limit_for(scope)")
    epoch = int(now.timestamp())
    start = datetime.fromtimestamp(epoch - epoch % policy.window_seconds, UTC)
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(_PEEK, [scope, key, start])
            row = cursor.fetchone()
    except DatabaseError as exc:
        _log_store_failure(exc)
        return False
    return row is not None and row[0] >= policy.limit


def hit_client(scope: str, actor: Actor, request, now: datetime | None = None) -> Decision:
    """Hit using the key kind the policy asks for."""
    _aware(now)
    policy = _lookup(scope)
    if policy is None:
        return Decision(True, 0, 0)
    return _count(policy, client_key(actor, request, policy.key_kind), now)


def enforce(scope: str, key: str, now: datetime | None = None) -> Decision:
    """Like `hit`, but raises `RateLimited` when the limit is exceeded."""
    decision = hit(scope, key, now)
    if not decision.allowed:
        raise RateLimited(decision.retry_after)
    return decision


def purge_expired(now: datetime, dry_run: bool = False) -> int:
    """Delete counters whose window has ended (and those of scopes with no windowed policy).

    With `dry_run` it only counts them.
    """
    _aware(now)
    with connection.cursor() as cursor:
        if dry_run:
            cursor.execute("SELECT count(*) " + _ENDED, [now])
            return cursor.fetchone()[0]
        cursor.execute("DELETE " + _ENDED, [now])
        return cursor.rowcount
