"""Run the app on this laptop against hosted services: Supabase PostgreSQL and Storage, real SMTP.

Not a production deployment (`prod.py` is that): the server still listens on loopback only and the
cookies work over plain http://localhost. All configuration comes from `.env.hosted` (ignored by
git; copy `.env.hosted.example`), which is the whole of it: variables of the same names left in the
shell, for example from the local `.env`, are discarded. `.env` is never read. See
docs/real-services.md.
"""

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

from config.env import (
    BASE_DIR,
    LOOPBACK_HOSTS,
    env_bool,
    env_int,
    load_dotenv,
    normalise_origin,
    require_secret,
    validate_base_url,
    validate_port,
    validate_sender,
)

if "pytest" in sys.modules or sys.argv[1:2] == ["test"]:
    raise ImproperlyConfigured("Tests never run with the hosted settings.")

HOSTED_VARIABLES = (
    "DJANGO_SECRET_KEY", "FRONTEND_BASE_URL", "SEED_ALLOWED",
    "SUPABASE_PROJECT_REF", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
    "DB_SCHEMA", "DB_SSLMODE", "DB_SSLROOTCERT",
    "MIGRATOR_USER", "MIGRATOR_PASSWORD", "RUNTIME_USER", "RUNTIME_PASSWORD",
    "PURGE_USER", "PURGE_PASSWORD",
    "S3_ENDPOINT_URL", "S3_REGION", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY",
    "MAIL_HOST", "MAIL_PORT", "MAIL_USER", "MAIL_PASSWORD", "MAIL_FROM",
    "MAIL_USE_TLS", "MAIL_USE_SSL",
    "AI_PROVIDER", "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_EMBEDDING_MODEL",
    "AI_DAILY_BUDGET", "AI_RESERVED_PERCENT",
)  # fmt: skip

_env_file = Path(os.environ.get("HOSTED_ENV_FILE") or BASE_DIR / ".env.hosted")
if not _env_file.is_file():
    raise ImproperlyConfigured("No .env.hosted file: copy .env.hosted.example and fill it in.")
for _name in HOSTED_VARIABLES:
    os.environ.pop(_name, None)
load_dotenv(_env_file, override=True)

from .base import *  # noqa: E402, F403
from .base import (  # noqa: E402
    DB_HOST,
    DB_PORT,
    MIGRATOR_DB_USER,
    PURGE_DB_USER,
    RUNTIME_DB_USER,
    build_database,
)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    # "<...>" without an "@" is an unfilled placeholder; "Name <address@domain>" is not.
    if not value or any("@" not in part for part in re.findall(r"<[^>]*>", value)):
        raise ImproperlyConfigured(f"Environment variable {name} is missing or not filled in.")
    return value


DEBUG = False
SECRET_KEY = require_secret("DJANGO_SECRET_KEY")
# This laptop only: the cookies below are not Secure, so no other host may be served.
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
FRONTEND_BASE_URL = validate_base_url(
    "FRONTEND_BASE_URL",
    os.environ.get("FRONTEND_BASE_URL") or "http://localhost:3000",
    require_https=False,
)
CSRF_TRUSTED_ORIGINS = [normalise_origin(FRONTEND_BASE_URL)]
TRUSTED_PROXIES: list[str] = []
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# --- Database -------------------------------------------------------------------------------
_required("POSTGRES_HOST")
if DB_HOST.strip().lower() in LOOPBACK_HOSTS or DB_HOST.startswith("/"):
    raise ImproperlyConfigured("POSTGRES_HOST must be the hosted database, not this machine.")
if DB_PORT == 6543:
    raise ImproperlyConfigured(
        "POSTGRES_PORT 6543 is the transaction pooler; use the session pooler (5432)."
    )
DB_SCHEMA = os.environ.get("DB_SCHEMA") or "estate"
_sslmode = os.environ.get("DB_SSLMODE") or "require"
if _sslmode not in ("require", "verify-ca", "verify-full"):
    raise ImproperlyConfigured("DB_SSLMODE must be require, verify-ca or verify-full.")
_ssl = {"sslmode": _sslmode}
if os.environ.get("DB_SSLROOTCERT"):
    _ssl["sslrootcert"] = os.environ["DB_SSLROOTCERT"]
# The shared pooler routes by `<role>.<project ref>`; a direct connection uses the bare role.
_suffix = ""
if DB_HOST.endswith(".pooler.supabase.com"):
    _suffix = "." + _required("SUPABASE_PROJECT_REF")


def _database(role: str, password_name: str, *, pooled: bool = False) -> dict:
    database = build_database(role + _suffix, require_secret(password_name))
    database["OPTIONS"] = {"connect_timeout": 15, **_ssl}
    if pooled:
        # A new TLS connection per request would cost several round trips to another region.
        database["OPTIONS"]["pool"] = {"min_size": 1, "max_size": 5, "timeout": 20}
        database["CONN_HEALTH_CHECKS"] = True
    return database


DATABASES = {
    "default": _database(RUNTIME_DB_USER, "RUNTIME_PASSWORD", pooled=True),
    "migrator": _database(MIGRATOR_DB_USER, "MIGRATOR_PASSWORD"),
}
if os.environ.get("PURGE_PASSWORD"):
    DATABASES["purge"] = _database(PURGE_DB_USER, "PURGE_PASSWORD")

# --- Media ----------------------------------------------------------------------------------
MEDIA_BACKEND = "s3"
S3_ENDPOINT_URL = _required("S3_ENDPOINT_URL")
_endpoint = urlsplit(S3_ENDPOINT_URL)
if _endpoint.scheme != "https" and _endpoint.hostname not in LOOPBACK_HOSTS:
    raise ImproperlyConfigured("S3_ENDPOINT_URL must be an https URL.")
S3_REGION = _required("S3_REGION")
S3_BUCKET = _required("S3_BUCKET")
S3_ACCESS_KEY_ID = _required("S3_ACCESS_KEY_ID")
S3_SECRET_ACCESS_KEY = _required("S3_SECRET_ACCESS_KEY")  # secret: never log or print it
S3_PREFIX = "media/"

# --- Mail -----------------------------------------------------------------------------------
EMAIL_BACKEND = "apps.kernel.mail_backend.ReservedSafeSMTPBackend"
EMAIL_HOST = _required("MAIL_HOST")
EMAIL_PORT = validate_port("MAIL_PORT", env_int("MAIL_PORT", 587))
EMAIL_HOST_USER = _required("MAIL_USER")
EMAIL_HOST_PASSWORD = _required("MAIL_PASSWORD")
DEFAULT_FROM_EMAIL = validate_sender("MAIL_FROM", _required("MAIL_FROM"))
EMAIL_USE_TLS = env_bool("MAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("MAIL_USE_SSL", False)
if EMAIL_USE_TLS == EMAIL_USE_SSL:
    raise ImproperlyConfigured("Exactly one of MAIL_USE_TLS and MAIL_USE_SSL must be true.")
EMAIL_TIMEOUT = 15
