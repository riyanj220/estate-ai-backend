from config.env import (
    env,
    env_int,
    env_list,
    load_dotenv,
    validate_base_url,
    validate_hosts,
    validate_loopback_host,
    validate_origins,
    validate_port,
    validate_proxies,
)

load_dotenv()

from .base import *  # noqa: E402, F403
from .base import (  # noqa: E402
    MIGRATOR_DB_USER,
    RUNTIME_DB_USER,
    build_database,
)

DEBUG = True
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = validate_hosts(
    "DJANGO_ALLOWED_HOSTS",
    env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost"),
    required=True,
)
CSRF_TRUSTED_ORIGINS = validate_origins(
    "CSRF_TRUSTED_ORIGINS", env_list("CSRF_TRUSTED_ORIGINS", "http://localhost:3000")
)
FRONTEND_BASE_URL = validate_base_url(
    "FRONTEND_BASE_URL", env("FRONTEND_BASE_URL", "http://localhost:3000"), require_https=False
)
# Empty trusts no proxy. Entries are IPs or CIDR ranges; /0 is refused.
TRUSTED_PROXIES = validate_proxies("TRUSTED_PROXIES", env_list("TRUSTED_PROXIES"))
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

DATABASES = {
    "default": build_database(RUNTIME_DB_USER, env("RUNTIME_PASSWORD")),
    "migrator": build_database(MIGRATOR_DB_USER, env("MIGRATOR_PASSWORD")),
}

# Mailpit catcher; start-up refuses any non-loopback host so local mail never leaves the machine.
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = validate_loopback_host("MAIL_HOST", env("MAIL_HOST", "127.0.0.1"))
EMAIL_PORT = validate_port("MAIL_PORT", env_int("MAIL_PORT", 1025))
EMAIL_USE_TLS = False
EMAIL_TIMEOUT = 10
