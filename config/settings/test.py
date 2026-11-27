import atexit
import os
import shutil
import tempfile

# One native thread per test process: with parallel workers, scikit-learn would otherwise start
# one thread per core in each of them and starve the others (slow runs, timed-out estimates).
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from config.env import (
    env,
    env_list,
    load_dotenv,
    normalise_origin,
    validate_base_url,
    validate_hosts,
    validate_origins,
    validate_proxies,
)

load_dotenv()

from .base import *  # noqa: E402, F403
from .base import MIGRATOR_DB_USER, build_database  # noqa: E402

DEBUG = False
SECRET_KEY = "test-only-key-not-a-secret-" + "x" * 32  # noqa: S105
ALLOWED_HOSTS = validate_hosts(
    "DJANGO_ALLOWED_HOSTS",
    env_list("DJANGO_ALLOWED_HOSTS", "testserver,127.0.0.1,localhost"),
    required=True,
)
# The Django test client always uses this host, whatever a developer's .env says.
if "testserver" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append("testserver")
CSRF_TRUSTED_ORIGINS = validate_origins(
    "CSRF_TRUSTED_ORIGINS", env_list("CSRF_TRUSTED_ORIGINS", "http://localhost:3000")
)
FRONTEND_BASE_URL = validate_base_url(
    "FRONTEND_BASE_URL", env("FRONTEND_BASE_URL", "http://localhost:3000"), require_https=False
)
# Requests through the frontend proxy carry the frontend origin, so it is always trusted.
if normalise_origin(FRONTEND_BASE_URL) not in CSRF_TRUSTED_ORIGINS:
    CSRF_TRUSTED_ORIGINS = [*CSRF_TRUSTED_ORIGINS, normalise_origin(FRONTEND_BASE_URL)]
# Empty trusts no proxy. Entries are IPs or CIDR ranges; /0 is refused.
TRUSTED_PROXIES = validate_proxies("TRUSTED_PROXIES", env_list("TRUSTED_PROXIES"))
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Tests create and migrate their own database, so the default alias is the
# migrator. The runtime role is exercised through its own connection.
DATABASES = {"default": build_database(MIGRATOR_DB_USER, env("MIGRATOR_PASSWORD"))}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
MAIL_SEND_INLINE = True

# Fast hashing keeps tests quick; production uses the default hashers.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Uploaded media goes to a throw-away directory, never the developer's MEDIA_ROOT.
MEDIA_ROOT = tempfile.mkdtemp(prefix="estate-ai-test-media-")
atexit.register(shutil.rmtree, MEDIA_ROOT, ignore_errors=True)

# Same thread, same transaction: a worker thread would not see a test's uncommitted rows.
VALUATION_BACKGROUND_ESTIMATES = False

# Tests never call a provider, whatever a developer's environment says.
AI_PROVIDER = "stub"
os.environ.pop("GEMINI_API_KEY", None)  # nor does the test process keep a real key
GEMINI_API_KEY = ""
GEMINI_MODEL = "gemini-test-model"
GEMINI_EMBEDDING_MODEL = "gemini-test-embedding"
AI_DAILY_BUDGET = 1000
AI_RESERVED_PERCENT = 50
