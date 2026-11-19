import importlib
import os
import sys

import pytest
from django.core.exceptions import ImproperlyConfigured

GOOD = "k3Jf9-aLw0Zq8Xv2Nn5Rt7Yu1Op4Sd6Gh3Bm"
RUNTIME_GOOD = "Zp8Qe2Lm5Vx1Cn7Bt4Ra9Ky6Hu3Jd0Wf5Gs"
MIGRATOR_GOOD = "Aq1Ws2Ed3Rf4Tg5Yh6Uj7Ik8Ol9Pm0Nb1Vc"
REQUIRED = ("DJANGO_SECRET_KEY", "RUNTIME_PASSWORD")
BAD_VALUES = [
    "",
    "   ",
    "change-me",
    "change-me-change-me-change-me-change-me",
    "short-secret",
    "x" * 31,
]


def load_prod(monkeypatch, **overrides):
    monkeypatch.setenv("DJANGO_SECRET_KEY", GOOD)
    monkeypatch.setenv("RUNTIME_PASSWORD", RUNTIME_GOOD)
    monkeypatch.delenv("MIGRATOR_PASSWORD", raising=False)
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "api.example.org")
    monkeypatch.setenv("FRONTEND_BASE_URL", "https://app.example.org")
    monkeypatch.setenv("MAIL_HOST", "smtp.example.org")
    monkeypatch.setenv("MAIL_FROM", "EstateAI <no-reply@example.org>")
    monkeypatch.delenv("CSRF_TRUSTED_ORIGINS", raising=False)
    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    for name, value in overrides.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    import config.settings.prod as prod

    return importlib.reload(prod)


def test_prod_loads_with_valid_secrets(monkeypatch):
    prod = load_prod(monkeypatch)
    assert prod.DEBUG is False
    assert prod.SECRET_KEY == GOOD


def test_prod_default_is_runtime_role(monkeypatch):
    prod = load_prod(monkeypatch)
    default = prod.DATABASES["default"]
    assert default["USER"] == prod.RUNTIME_DB_USER
    assert default["PASSWORD"] == RUNTIME_GOOD


def test_prod_loads_without_migrator_password(monkeypatch):
    prod = load_prod(monkeypatch)
    assert "migrator" not in prod.DATABASES


def test_prod_defines_migrator_when_password_present(monkeypatch):
    prod = load_prod(monkeypatch, MIGRATOR_PASSWORD=MIGRATOR_GOOD)
    assert prod.DATABASES["migrator"]["USER"] == prod.MIGRATOR_DB_USER
    assert prod.DATABASES["migrator"]["PASSWORD"] == MIGRATOR_GOOD


@pytest.mark.parametrize("name", REQUIRED)
def test_prod_missing_secret_names_variable(monkeypatch, name):
    with pytest.raises(ImproperlyConfigured, match=name):
        load_prod(monkeypatch, **{name: None})


@pytest.mark.parametrize("name", [*REQUIRED, "MIGRATOR_PASSWORD"])
@pytest.mark.parametrize("value", BAD_VALUES)
def test_prod_rejects_placeholder_or_short_secret(monkeypatch, name, value):
    if name == "MIGRATOR_PASSWORD" and not value.strip():
        pytest.skip("blank migrator password means the alias is simply not defined")
    with pytest.raises(ImproperlyConfigured, match=name) as exc:
        load_prod(monkeypatch, **{name: value})
    assert value.strip() == "" or value not in str(exc.value)


@pytest.mark.parametrize("name", ["MAIL_HOST", "MAIL_FROM"])
def test_prod_requires_mail_settings(monkeypatch, name):
    with pytest.raises(ImproperlyConfigured, match=name):
        load_prod(monkeypatch, **{name: None})


def test_prod_mail_uses_smtp_from_environment(monkeypatch):
    prod = load_prod(
        monkeypatch, MAIL_PORT="2525", MAIL_USER="u", MAIL_PASSWORD="pw", MAIL_USE_TLS="true"
    )
    assert prod.EMAIL_BACKEND.endswith("smtp.EmailBackend")
    assert (prod.EMAIL_HOST, prod.EMAIL_PORT, prod.EMAIL_HOST_USER) == (
        "smtp.example.org",
        2525,
        "u",
    )
    assert prod.EMAIL_HOST_PASSWORD == "pw"  # noqa: S105
    assert prod.EMAIL_USE_TLS is True
    assert prod.MAIL_SEND_INLINE is False


def test_prod_mail_defaults(monkeypatch):
    prod = load_prod(
        monkeypatch,
        MAIL_PORT=None,
        MAIL_USE_TLS=None,
        MAIL_USE_SSL=None,
        MAIL_USER=None,
        MAIL_PASSWORD=None,
    )
    assert (prod.EMAIL_PORT, prod.EMAIL_USE_TLS, prod.EMAIL_USE_SSL) == (587, True, False)
    assert prod.EMAIL_TIMEOUT == 10
    assert prod.DEFAULT_FROM_EMAIL == "EstateAI <no-reply@example.org>"


@pytest.mark.parametrize(
    ("overrides", "name"),
    [
        ({"MAIL_USE_TLS": "true", "MAIL_USE_SSL": "true"}, "MAIL_USE_SSL"),
        ({"MAIL_USER": "u", "MAIL_USE_TLS": "false", "MAIL_USE_SSL": "false"}, "MAIL_USER"),
        ({"MAIL_PORT": "0"}, "MAIL_PORT"),
        ({"MAIL_PORT": "65536"}, "MAIL_PORT"),
        ({"MAIL_FROM": "a@example.org\r\nBcc: x@example.org"}, "MAIL_FROM"),
        ({"MAIL_FROM": "not-an-address"}, "MAIL_FROM"),
        ({"MAIL_FROM": "a@example.org, b@example.org"}, "MAIL_FROM"),
    ],
)
def test_prod_mail_refusals(monkeypatch, overrides, name):
    with pytest.raises(ImproperlyConfigured, match=name):
        load_prod(monkeypatch, **overrides)


def test_prod_accepts_implicit_tls_and_bare_sender(monkeypatch):
    prod = load_prod(
        monkeypatch, MAIL_USE_TLS="false", MAIL_USE_SSL="true", MAIL_FROM="a@example.org"
    )
    assert prod.EMAIL_USE_SSL is True and prod.DEFAULT_FROM_EMAIL == "a@example.org"


def test_asgi_requires_settings_module(monkeypatch):
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    sys.modules.pop("config.asgi", None)
    with pytest.raises(RuntimeError):
        importlib.import_module("config.asgi")
    assert "DJANGO_SETTINGS_MODULE" not in os.environ
