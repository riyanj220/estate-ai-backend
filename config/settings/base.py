import os

from django.core.exceptions import ImproperlyConfigured

from apps.kernel.runtime import validate_limits
from config.env import BASE_DIR, env, env_bool, env_int

ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"
WSGI_APPLICATION = None

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "rest_framework",
    "drf_spectacular",
    "config.apps.ConfigAppConfig",
    "apps.kernel",
    "apps.catalog",
    "apps.identity",
    "apps.listings",
    "apps.valuation",
]

MIDDLEWARE = [
    "apps.kernel.request_logging.RequestLoggingMiddleware",
    "apps.kernel.security_headers.SecurityHeadersMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "apps.kernel.upload_limit.BodySizeMiddleware",
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

# Tables the runtime role may read and insert but not delete, truncate or update (the audit table,
# Story 1.10), except for the columns named in RUNTIME_COLUMN_UPDATES below.
RUNTIME_APPEND_ONLY_TABLES: list[str] = ["kernel_auditrecord", "listings_publicationevent"]
# The only columns of those tables the runtime role may still UPDATE (Story 2.19: the one-time
# correction marks its counted publication event and moves its identity fingerprint).
RUNTIME_COLUMN_UPDATES: dict[str, list[str]] = {
    "listings_publicationevent": ["correction_used", "identity_fingerprint"],
}


# Story 2.16: `seed_sample_data` runs under the local and test settings only. A staging or other
# settings module must opt in explicitly; the production settings pin this to False and the
# command refuses them whatever this says.
SEED_ALLOWED = env_bool("SEED_ALLOWED", False)


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
# Estimates after publication and edits run on the executor, so no request waits for them.
VALUATION_BACKGROUND_ESTIMATES = True
validate_limits(RUNTIME_EXECUTOR_MAX_WORKERS, RUNTIME_EXECUTOR_RESERVED_WORKERS)

# AI gateway (AD-8, AD-25). `stub` (default) uses no network and no key; `gemini` calls Google.
AI_PROVIDER = env("AI_PROVIDER", "stub")
GEMINI_API_KEY = env("GEMINI_API_KEY", "")  # secret: never log or print it
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_EMBEDDING_MODEL = env("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
# Provider requests allowed per UTC day; at least half is kept for signed-in Listing creation.
AI_DAILY_BUDGET = env_int("AI_DAILY_BUDGET", 1000)
AI_RESERVED_PERCENT = env_int("AI_RESERVED_PERCENT", 50)

# Mail (Story 1.13). Delivery is off the request thread unless MAIL_SEND_INLINE (tests).
DEFAULT_FROM_EMAIL = env("MAIL_FROM", "EstateAI <no-reply@localhost>")
MAIL_SEND_INLINE = False

STATIC_URL = "static/"

# Media (Story 2.9). Uploaded images are stored under MEDIA_ROOT through the `kernel.storage` port;
# the directory is never served by this application. The default sits in the project, git-ignored.
MEDIA_ROOT = env("MEDIA_ROOT", str(BASE_DIR / "media"))
if not os.path.isabs(MEDIA_ROOT):
    raise ImproperlyConfigured("MEDIA_ROOT must be an absolute path.")
# One upload is at most 5 MiB of image inside a request of at most 6 MiB (`kernel.images`,
# `kernel.upload_limit`: the Listing image upload and reorder routes and `PUT` on the two profile
# image routes only; every other route keeps Django's defaults). DATA_UPLOAD_MAX_MEMORY_SIZE
# stays at Django's default; the file limit is aligned so an upload is held in memory and never
# spills to a temp file.
MAX_REQUEST_BODY_BYTES = 6 * 1024 * 1024
MAX_IMAGE_ORDER_BODY_BYTES = 64 * 1024  # the JSON body of `PATCH .../images`
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_REQUEST_BODY_BYTES
FILE_UPLOAD_HANDLERS = ["django.core.files.uploadhandler.MemoryFileUploadHandler"]

# Cookies: never readable by scripts, never sent cross-site on subrequests.
# Secure is on here and switched off only in local and test settings.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True

# Server-side sessions (Story 1.17). The cookie carries a random token; only its hash is stored.
SESSION_ABSOLUTE_SECONDS = env_int("SESSION_ABSOLUTE_SECONDS", 14 * 24 * 3600)
SESSION_IDLE_SECONDS = env_int("SESSION_IDLE_SECONDS", 24 * 3600)
SESSION_TOUCH_SECONDS = env_int("SESSION_TOUCH_SECONDS", 300)
SESSION_MAX_OPEN = env_int("SESSION_MAX_OPEN", 10)  # older open sessions end as `replaced`
# Administrator sessions are short (Story 1.22): 30 minutes idle, 8 hours absolute.
ADMIN_SESSION_ABSOLUTE_SECONDS = env_int("ADMIN_SESSION_ABSOLUTE_SECONDS", 8 * 3600)
ADMIN_SESSION_IDLE_SECONDS = env_int("ADMIN_SESSION_IDLE_SECONDS", 30 * 60)
if not (
    0 < SESSION_TOUCH_SECONDS < SESSION_IDLE_SECONDS <= SESSION_ABSOLUTE_SECONDS
    and SESSION_MAX_OPEN > 0
):
    raise ImproperlyConfigured(
        "Session settings must satisfy 0 < SESSION_TOUCH_SECONDS < SESSION_IDLE_SECONDS "
        "<= SESSION_ABSOLUTE_SECONDS and SESSION_MAX_OPEN > 0."
    )
if not (
    0 < SESSION_TOUCH_SECONDS < ADMIN_SESSION_IDLE_SECONDS <= ADMIN_SESSION_ABSOLUTE_SECONDS
    and ADMIN_SESSION_IDLE_SECONDS <= SESSION_IDLE_SECONDS
    and ADMIN_SESSION_ABSOLUTE_SECONDS <= SESSION_ABSOLUTE_SECONDS
):
    raise ImproperlyConfigured(
        "Administrator session settings must satisfy 0 < SESSION_TOUCH_SECONDS < "
        "ADMIN_SESSION_IDLE_SECONDS <= ADMIN_SESSION_ABSOLUTE_SECONDS, with neither above its "
        "ordinary SESSION_* counterpart."
    )

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
    # Throttling is opt-in per view (PolicyThrottle). Cookie sessions authenticate (Story 1.17).
    "DEFAULT_PERMISSION_CLASSES": ["apps.presentation.api.permissions.DenyByDefault"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.presentation.api.authentication.SessionAuthentication"
    ],
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
    "ENUM_NAME_OVERRIDES": {
        "RegistrationAcceptedStatusEnum": ["check_your_email"],
        "OwnerAccountTypeEnum": ["individual", "agency"],
        "TypeEnum": "apps.catalog.registry.FIELD_TYPES",
        "EmailVerifiedStatusEnum": ["verified"],
        "FieldClassEnum": ["public", "private"],
        "LocalityKindEnum": ["area", "society"],
        "VisibilityEnum": "apps.listings.lifecycle.Visibility",
        "ListingStatusEnum": ["draft", "published", "archived", "sold", "rented", "removed"],
        "FieldRequirementEnum": ["required_for_publication", "recommended", "optional"],
        "EstimateStatusEnum": ["available", "unavailable"],
    },
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
