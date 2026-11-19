import os

from django.core.exceptions import ImproperlyConfigured

from config.env import (
    env_bool,
    env_int,
    env_list,
    require_secret,
    validate_base_url,
    validate_hosts,
    validate_origins,
    validate_port,
    validate_proxies,
    validate_sender,
)

from .base import *  # noqa: F403
from .base import MIGRATOR_DB_USER, RUNTIME_DB_USER, build_database

DEBUG = False
SECRET_KEY = require_secret("DJANGO_SECRET_KEY")

ALLOWED_HOSTS = validate_hosts(
    "DJANGO_ALLOWED_HOSTS", env_list("DJANGO_ALLOWED_HOSTS"), required=True
)
CSRF_TRUSTED_ORIGINS = validate_origins(
    "CSRF_TRUSTED_ORIGINS", env_list("CSRF_TRUSTED_ORIGINS"), require_https=True
)
FRONTEND_BASE_URL = validate_base_url(
    "FRONTEND_BASE_URL", os.environ.get("FRONTEND_BASE_URL", ""), require_https=True
)

# Empty trusts no proxy. Entries are IPs or CIDR ranges; /0 is refused.
TRUSTED_PROXIES = validate_proxies("TRUSTED_PROXIES", env_list("TRUSTED_PROXIES"))

SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True

DATABASES = {
    "default": build_database(RUNTIME_DB_USER, require_secret("RUNTIME_PASSWORD")),
}

# The serving process holds no DDL-capable credentials. The migrator alias exists
# only where MIGRATOR_PASSWORD is provided (the migration job).
if os.environ.get("MIGRATOR_PASSWORD"):
    DATABASES["migrator"] = build_database(MIGRATOR_DB_USER, require_secret("MIGRATOR_PASSWORD"))

# Mail: SMTP from the environment. MAIL_PASSWORD is a secret and is never logged.
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("MAIL_HOST", "").strip()
if not EMAIL_HOST:
    raise ImproperlyConfigured("Environment variable MAIL_HOST is missing or empty.")
_mail_from = os.environ.get("MAIL_FROM", "").strip()
if not _mail_from:
    raise ImproperlyConfigured("Environment variable MAIL_FROM is missing or empty.")
DEFAULT_FROM_EMAIL = validate_sender("MAIL_FROM", _mail_from)
EMAIL_PORT = validate_port("MAIL_PORT", env_int("MAIL_PORT", 587))
EMAIL_HOST_USER = os.environ.get("MAIL_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
EMAIL_USE_TLS = env_bool("MAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("MAIL_USE_SSL", False)
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured("MAIL_USE_TLS and MAIL_USE_SSL cannot both be true.")
if EMAIL_HOST_USER and not (EMAIL_USE_TLS or EMAIL_USE_SSL):
    raise ImproperlyConfigured("MAIL_USER requires MAIL_USE_TLS or MAIL_USE_SSL.")
EMAIL_TIMEOUT = 10
