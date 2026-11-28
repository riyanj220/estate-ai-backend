"""Environment-variable access. Configuration comes only from the environment.

For local and test use, `load_dotenv` reads an ignored `.env` file into the
process environment without overriding variables that are already set.
"""

import ipaddress
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

MIN_SECRET_LENGTH = 32
_PLACEHOLDER_MARKERS = (
    "change-me",
    "changeme",
    "change_me",
    "placeholder",
    "replace-me",
    "replace_me",
    "django-insecure",
    "your-secret",
    "example",
)


def load_dotenv(path: Path | None = None, *, override: bool = False) -> None:
    path = path or BASE_DIR / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if override or key.strip() not in os.environ:
            os.environ[key.strip()] = value


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise ImproperlyConfigured(f"Environment variable {name} is required.")
    return value


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ImproperlyConfigured(f"Environment variable {name} must be an integer.") from None


def is_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def require_secret(name: str) -> str:
    """Return the secret in `name` or raise, naming the variable and never the value."""
    value = os.environ.get(name, "")
    if not value.strip():
        raise ImproperlyConfigured(f"Environment variable {name} is missing or empty.")
    if is_placeholder(value):
        raise ImproperlyConfigured(f"Environment variable {name} holds a placeholder value.")
    if len(value) < MIN_SECRET_LENGTH:
        raise ImproperlyConfigured(
            f"Environment variable {name} must be at least {MIN_SECRET_LENGTH} characters."
        )
    return value


def env_list(name: str, default: str = "") -> list[str]:
    """Comma-separated variable as a list; blanks are dropped."""
    raw = os.environ.get(name)
    if raw is None:
        raw = default
    return [item.strip() for item in raw.split(",") if item.strip()]


_HOST_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


def _bad(name: str, why: str) -> ImproperlyConfigured:
    # Names the variable only; a value is never echoed.
    return ImproperlyConfigured(f"Environment variable {name} {why}.")


def validate_hosts(name: str, hosts: list[str], *, required: bool) -> list[str]:
    if required and not hosts:
        raise _bad(name, "is missing or empty")
    for host in hosts:
        if (
            "*" in host
            or host.startswith(".")
            or not (_HOST_RE.match(host) or _is_ip(host.strip("[]")))
        ):
            raise _bad(name, "must list exact host names without wildcards")
    return hosts


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _origin_parts(value: str):
    if any(ch in value for ch in "?#@*"):
        return None
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises on an invalid port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or "*" in parts.netloc:
        return None
    if parts.path or parts.query or parts.fragment or parts.username or parts.password:
        return None
    return parts


def validate_origins(name: str, origins: list[str], *, require_https: bool = False) -> list[str]:
    for origin in origins:
        parts = _origin_parts(origin)
        if parts is None or (require_https and parts.scheme != "https"):
            raise _bad(name, "must list origins like https://host with a scheme and no wildcard")
    return origins


def validate_base_url(name: str, value: str, *, require_https: bool) -> str:
    parts = _origin_parts(value.strip()) if value else None
    if parts is None or (require_https and parts.scheme != "https"):
        scheme = "an https" if require_https else "an http(s)"
        raise _bad(name, f"must be {scheme} origin with no path")
    return value.strip()


def validate_proxies(name: str, entries: list[str]) -> list[str]:
    for entry in entries:
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            raise _bad(name, "must list IP addresses or CIDR ranges") from None
        if network.prefixlen < (8 if network.version == 4 else 16):
            raise _bad(name, "must list the proxy's own narrow range (at least /8 or /16 for IPv6)")
    return entries


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    lowered = raw.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise _bad(name, "must be true or false")


def validate_loopback_host(name: str, value: str) -> str:
    """Local mail must reach only the catcher on this machine."""
    host = value.strip().strip("[]").lower()
    if host not in LOOPBACK_HOSTS:
        raise _bad(
            name, "must be a loopback address (127.0.0.1, localhost or ::1) in local settings"
        )
    return host


def validate_port(name: str, port: int) -> int:
    if not 1 <= port <= 65535:
        raise _bad(name, "must be between 1 and 65535")
    return port


def _plausible_sender(value: str) -> bool:
    """`addr` or `Name <addr>`, where addr is `local@domain` with no `<`, `>`, `@` or space."""
    if value.endswith(">"):
        name, bracket, address = value[:-1].partition("<")
        if not bracket or not name.strip() or "@" in name or ">" in name:
            return False
    else:
        address = value
    local, at, domain = address.partition("@")
    return bool(
        at and local and domain and not any(ch in "<>@" or ch.isspace() for ch in local + domain)
    )


def validate_sender(name: str, value: str) -> str:
    """`addr` or `Name <addr>`: one plausible address and no control or format characters."""
    value = value.strip()
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in value):
        raise _bad(name, "must not contain control characters")
    if not _plausible_sender(value) or "," in value or ";" in value:
        raise _bad(name, "must be one address, optionally as Name <address>")
    return value


def normalise_origin(value: str) -> str:
    """Scheme and host lowercased, no trailing slash: the form Django compares origins in."""
    parts = urlsplit(value.strip())
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"
