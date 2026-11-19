from config.env import env, env_int, load_dotenv

load_dotenv()

from .base import *  # noqa: E402, F403
from .base import (  # noqa: E402
    MIGRATOR_DB_USER,
    RUNTIME_DB_USER,
    build_database,
)

DEBUG = True
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

DATABASES = {
    "default": build_database(RUNTIME_DB_USER, env("RUNTIME_PASSWORD")),
    "migrator": build_database(MIGRATOR_DB_USER, env("MIGRATOR_PASSWORD")),
}

EMAIL_HOST = env("MAIL_HOST", "127.0.0.1")
EMAIL_PORT = env_int("MAIL_PORT", 1025)
