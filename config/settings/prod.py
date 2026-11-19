import os

from config.env import require_secret

from .base import *  # noqa: F403
from .base import MIGRATOR_DB_USER, RUNTIME_DB_USER, build_database

DEBUG = False
SECRET_KEY = require_secret("DJANGO_SECRET_KEY")

DATABASES = {
    "default": build_database(RUNTIME_DB_USER, require_secret("RUNTIME_PASSWORD")),
}

# The serving process holds no DDL-capable credentials. The migrator alias exists
# only where MIGRATOR_PASSWORD is provided (the migration job).
if os.environ.get("MIGRATOR_PASSWORD"):
    DATABASES["migrator"] = build_database(MIGRATOR_DB_USER, require_secret("MIGRATOR_PASSWORD"))
