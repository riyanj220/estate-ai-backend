"""Environment-variable access. Configuration comes only from the environment.

For local and test use, `load_dotenv` reads an ignored `.env` file into the
process environment without overriding variables that are already set.
"""

import os
from pathlib import Path

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


def load_dotenv(path: Path | None = None) -> None:
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
        os.environ.setdefault(key.strip(), value)


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
