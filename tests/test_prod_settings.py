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


def test_asgi_requires_settings_module(monkeypatch):
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    sys.modules.pop("config.asgi", None)
    with pytest.raises(RuntimeError):
        importlib.import_module("config.asgi")
    assert "DJANGO_SETTINGS_MODULE" not in os.environ
