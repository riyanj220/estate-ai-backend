"""Hosted mode (`config.settings.hosted`), loaded in a subprocess with a made-up env file, and the
mail backend that never mails reserved addresses. No network."""

import json
import os
import subprocess
import sys

import pytest
from django.core.mail import EmailMessage
from django.core.mail.backends.smtp import EmailBackend

from apps.kernel.mail_backend import ReservedSafeSMTPBackend, is_undeliverable
from config.env import BASE_DIR

SECRET = "k" * 48
VALUES = {
    "DJANGO_SECRET_KEY": SECRET,
    "SUPABASE_PROJECT_REF": "abcdefgh",
    "POSTGRES_HOST": "aws-0-region.pooler.supabase.com",
    "POSTGRES_DB": "postgres",
    "MIGRATOR_PASSWORD": "m" * 40,
    "RUNTIME_PASSWORD": "r" * 40,
    "S3_ENDPOINT_URL": "https://abcdefgh.storage.supabase.co/storage/v1/s3",
    "S3_REGION": "region",
    "S3_BUCKET": "media",
    "S3_ACCESS_KEY_ID": "id",
    "S3_SECRET_ACCESS_KEY": "secret",
    "MAIL_HOST": "smtp.invalid",
    "MAIL_USER": "user",
    "MAIL_PASSWORD": "password",
    "MAIL_FROM": "EstateAI <sender@mail.invalid>",
}
PROBE = """
import json, config.settings.hosted as s
d = s.DATABASES
print(json.dumps({
    "users": [d[a]["USER"] for a in d], "ssl": d["default"]["OPTIONS"]["sslmode"],
    "media": s.MEDIA_BACKEND, "seed": s.SEED_ALLOWED, "mail": s.EMAIL_HOST, "debug": s.DEBUG,
}))
"""


def load(tmp_path, changes=None, shell=None, code=PROBE):
    path = tmp_path / ".env.hosted"
    values = {**VALUES, **(changes or {})}
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    env = {"PATH": os.environ["PATH"], "HOSTED_ENV_FILE": str(path), **(shell or {})}
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], cwd=BASE_DIR, env=env, capture_output=True, text=True
    )


def test_a_filled_file_selects_the_hosted_services(tmp_path):
    # Left over from the local .env in the shell: ignored.
    shell = {"MAIL_HOST": "127.0.0.1", "SEED_ALLOWED": "true", "PURGE_PASSWORD": "p" * 40}
    result = load(tmp_path, shell=shell)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "users": ["runtime.abcdefgh", "migrator.abcdefgh"],
        "ssl": "require",
        "media": "s3",
        "seed": False,
        "mail": "smtp.invalid",
        "debug": False,
    }


@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"POSTGRES_HOST": "127.0.0.1"}, "POSTGRES_HOST"),
        ({"POSTGRES_PORT": "6543"}, "POSTGRES_PORT"),
        ({"DB_SSLMODE": "disable"}, "DB_SSLMODE"),
        ({"S3_ENDPOINT_URL": "http://store.invalid"}, "S3_ENDPOINT_URL"),
        ({"S3_BUCKET": "<bucket-name>"}, "S3_BUCKET"),
        ({"RUNTIME_PASSWORD": "short-secret"}, "RUNTIME_PASSWORD"),
        ({"MAIL_USE_TLS": "false"}, "MAIL_USE_TLS"),
    ],
)
def test_unsafe_or_missing_values_are_refused_without_echoing_them(tmp_path, changes, named):
    result = load(tmp_path, changes)
    last = result.stderr.strip().splitlines()[-1]
    assert result.returncode != 0 and named in last
    assert "short-secret" not in result.stderr and SECRET not in result.stderr


def test_tests_never_load_the_hosted_settings(tmp_path):
    result = load(tmp_path, code="import pytest, config.settings.hosted")
    assert result.returncode != 0 and "Tests never run" in result.stderr


def test_the_example_file_names_only_known_variables_and_is_refused_unfilled(tmp_path):
    example = (BASE_DIR / ".env.hosted.example").read_text()
    names = {line.split("=")[0] for line in example.splitlines() if line[:1].isupper()}
    from_source = (BASE_DIR / "config/settings/hosted.py").read_text()
    assert all(f'"{name}"' in from_source for name in names)
    path = tmp_path / "unfilled"
    path.write_text(example)
    env = {"PATH": os.environ["PATH"], "HOSTED_ENV_FILE": str(path)}
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "import config.settings.hosted"],
        cwd=BASE_DIR, env=env, capture_output=True, text=True,
    )  # fmt: skip
    assert result.returncode != 0 and "ImproperlyConfigured" in result.stderr


def test_reserved_addresses():
    for address in ("a@sample.estate-ai.invalid", "a@x.test", "A <a@Example.com>", "a@localhost"):
        assert is_undeliverable(address), address
    for address in ("a@gmail.com", "a@invalid.org", "a@myexample.com"):
        assert not is_undeliverable(address), address


def test_the_backend_sends_only_deliverable_recipients(monkeypatch):
    sent = []
    monkeypatch.setattr(
        EmailBackend, "send_messages", lambda self, messages: sent.extend(messages) or len(messages)
    )
    backend = ReservedSafeSMTPBackend(host="smtp.invalid")
    sample = EmailMessage("s", "b", to=["sample.agency.1@sample.estate-ai.invalid"])
    assert backend.send_messages([sample]) == 0 and sent == []
    mixed = EmailMessage("s", "b", to=["a@sample.estate-ai.invalid", "me@real-domain.pk"])
    assert backend.send_messages([sample, mixed]) == 1
    assert [m.to for m in sent] == [["me@real-domain.pk"]]
