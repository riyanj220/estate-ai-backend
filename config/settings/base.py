from config.env import env, env_int

ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"
WSGI_APPLICATION = None

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "config.apps.ConfigAppConfig",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

TEMPLATES: list = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Database roles. `migrator` owns the schema and runs migrations; `runtime`
# serves requests with DML rights only (no DDL).
DB_NAME = env("POSTGRES_DB", "estate_ai")
DB_HOST = env("POSTGRES_HOST", "127.0.0.1")
DB_PORT = env_int("POSTGRES_PORT", 5432)
MIGRATOR_DB_USER = env("MIGRATOR_USER", "migrator")
RUNTIME_DB_USER = env("RUNTIME_USER", "runtime")

# Tables the runtime role may only read and insert (for example the audit table,
# added in Story 1.10). Grants skip UPDATE and DELETE on these.
RUNTIME_APPEND_ONLY_TABLES: list[str] = []


def build_database(user: str, password: str) -> dict:
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": DB_NAME,
        "USER": user,
        "PASSWORD": password,
        "HOST": DB_HOST,
        "PORT": DB_PORT,
        "OPTIONS": {"connect_timeout": 3},
    }


STATIC_URL = "static/"
