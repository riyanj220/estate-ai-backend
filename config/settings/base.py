from apps.kernel.runtime import validate_limits
from config.env import env, env_int

ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"
WSGI_APPLICATION = None

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "rest_framework",
    "drf_spectacular",
    "config.apps.ConfigAppConfig",
    "apps.kernel",
]

MIDDLEWARE = [
    "apps.kernel.request_logging.RequestLoggingMiddleware",
    "apps.kernel.security_headers.SecurityHeadersMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
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

# Tables the runtime role may only read and insert (the audit table, Story 1.10).
RUNTIME_APPEND_ONLY_TABLES: list[str] = ["kernel_auditrecord"]


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


# Runtime executor (AD-14): heavy work never uses more than total minus reserved workers.
RUNTIME_EXECUTOR_MAX_WORKERS = env_int("RUNTIME_EXECUTOR_MAX_WORKERS", 4)
RUNTIME_EXECUTOR_RESERVED_WORKERS = env_int("RUNTIME_EXECUTOR_RESERVED_WORKERS", 1)
validate_limits(RUNTIME_EXECUTOR_MAX_WORKERS, RUNTIME_EXECUTOR_RESERVED_WORKERS)

STATIC_URL = "static/"

# Cookies: never readable by scripts, never sent cross-site on subrequests.
# Secure is on here and switched off only in local and test settings.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True

CSRF_FAILURE_VIEW = "apps.presentation.api.handlers.csrf_failure"
SECURE_REFERRER_POLICY = "no-referrer"
# TLS terminates at the proxy and X-Forwarded-Proto is never trusted from an
# unconfigured source, so no SSL redirect or proxy-SSL header is configured.
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0


REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    # Default deny: every view declares its permission explicitly (Story 1.6).
    # Throttling is opt-in per view (PolicyThrottle); authentication arrives in Story 1.17.
    "DEFAULT_PERMISSION_CLASSES": ["apps.presentation.api.permissions.DenyByDefault"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,  # the auth app is not installed
    "EXCEPTION_HANDLER": "apps.presentation.api.exceptions.exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "EstateAI API",
    "DESCRIPTION": "EstateAI backend contract. One error envelope, stable error codes.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_AUTHENTICATION": [],
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "apps.presentation.api.schema.add_error_contract",
    ],
}

# Structured JSON to stdout with an allow-list of fields (AD-26). Loggers that
# record request paths and query strings are silenced; the request middleware
# logs the route template instead.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "apps.kernel.request_logging.JsonFormatter"}},
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json",
        },
        "null": {"class": "logging.NullHandler"},
    },
    "root": {"handlers": ["stdout"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
        **{
            name: {"handlers": ["null"], "level": "CRITICAL", "propagate": False}
            for name in (
                "django.request",
                "django.server",
                "django.channels.server",
                "daphne.server",
            )
        },
    },
}
