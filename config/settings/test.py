from config.env import env, load_dotenv

load_dotenv()

from .base import *  # noqa: E402, F403
from .base import MIGRATOR_DB_USER, build_database  # noqa: E402

DEBUG = False
SECRET_KEY = "test-only-key-not-a-secret-" + "x" * 32  # noqa: S105
ALLOWED_HOSTS = ["testserver", "127.0.0.1", "localhost"]

# Tests create and migrate their own database, so the default alias is the
# migrator. The runtime role is exercised through its own connection.
DATABASES = {"default": build_database(MIGRATOR_DB_USER, env("MIGRATOR_PASSWORD"))}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
