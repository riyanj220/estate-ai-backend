import os

from config.env import (
    env_list,
    require_secret,
    validate_base_url,
    validate_hosts,
    validate_origins,
    validate_proxies,
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
