import importlib
import json
import sys

import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import Client, RequestFactory, override_settings
from django.urls import URLPattern, URLResolver, get_resolver

from apps.kernel.links import frontend_url
from apps.kernel.network import client_ip
from apps.kernel.security_headers import SecurityHeadersMiddleware
from tests.test_prod_settings import load_prod

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Content-Security-Policy": (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
}


def assert_secure_headers(response):
    for name, value in HEADERS.items():
        assert response[name] == value
    assert "Strict-Transport-Security" not in response


def test_headers_on_success_and_not_found(client):
    assert_secure_headers(client.get("/api/v1/health"))
    not_found = client.get("/nope")
    assert not_found.status_code == 404
    assert_secure_headers(not_found)


@pytest.mark.urls("tests.api.urls")
def test_headers_on_server_error():
    response = Client(raise_request_exception=False).get("/raise/boom")
    assert response.status_code == 500
    assert_secure_headers(response)


def test_headers_async(async_client):
    assert_secure_headers(async_to_sync(async_client.get)("/nope"))


def test_hostile_host_is_rejected_without_echo(client):
    response = client.get("/api/v1/health", HTTP_HOST="evil.example")
    assert response.status_code == 400
    assert "evil.example" not in response.content.decode()
    assert response.json()["error"]["code"] == "bad_request"
    assert_secure_headers(response)


def test_cookie_flags_in_test_settings(settings):
    for prefix in ("SESSION", "CSRF"):
        assert getattr(settings, f"{prefix}_COOKIE_HTTPONLY") is True
        assert getattr(settings, f"{prefix}_COOKIE_SAMESITE") == "Lax"
        assert getattr(settings, f"{prefix}_COOKIE_SECURE") is False
    assert not settings.SECURE_HSTS_SECONDS
    assert "*" not in settings.ALLOWED_HOSTS


# --- prod settings -------------------------------------------------------------


def test_prod_secure_defaults(monkeypatch):
    prod = load_prod(monkeypatch, CSRF_TRUSTED_ORIGINS="https://app.example.org")
    assert prod.ALLOWED_HOSTS == ["api.example.org"]
    assert prod.CSRF_TRUSTED_ORIGINS == ["https://app.example.org"]
    assert prod.FRONTEND_BASE_URL == "https://app.example.org"
    assert prod.TRUSTED_PROXIES == []
    for prefix in ("SESSION", "CSRF"):
        assert getattr(prod, f"{prefix}_COOKIE_HTTPONLY") is True
        assert getattr(prod, f"{prefix}_COOKIE_SAMESITE") == "Lax"
        assert getattr(prod, f"{prefix}_COOKIE_SECURE") is True
    assert prod.SECURE_HSTS_SECONDS > 0
    assert prod.SECURE_HSTS_INCLUDE_SUBDOMAINS is True
    assert prod.SECURE_SSL_REDIRECT is False


@pytest.mark.parametrize(
    "value", [None, "", "  ", "*", "a.example.org,*", ".example.org", "https://x.org"]
)
def test_prod_bad_hosts(monkeypatch, value):
    with pytest.raises(ImproperlyConfigured, match="DJANGO_ALLOWED_HOSTS") as exc:
        load_prod(monkeypatch, DJANGO_ALLOWED_HOSTS=value)
    if value and value.strip():
        assert value not in str(exc.value)


@pytest.mark.parametrize(
    "value", ["*", "app.example.org", "https://*.example.org", "ftp://x.org", "https://x.org/p"]
)
def test_prod_bad_origins(monkeypatch, value):
    with pytest.raises(ImproperlyConfigured, match="CSRF_TRUSTED_ORIGINS"):
        load_prod(monkeypatch, CSRF_TRUSTED_ORIGINS=value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "http://app.example.org",
        "https://app.example.org/path",
        "app.example.org",
        "https://*.example.org",
    ],
)
def test_prod_bad_frontend_url(monkeypatch, value):
    with pytest.raises(ImproperlyConfigured, match="FRONTEND_BASE_URL"):
        load_prod(monkeypatch, FRONTEND_BASE_URL=value)


@pytest.mark.parametrize("value", ["not-an-ip", "0.0.0.0/0", "::/0", "10.0.0.0/33"])
def test_bad_trusted_proxies_fail_startup(monkeypatch, value):
    with pytest.raises(ImproperlyConfigured, match="TRUSTED_PROXIES"):
        load_prod(monkeypatch, TRUSTED_PROXIES=value)


def test_prod_hsts_header(monkeypatch):
    prod = load_prod(monkeypatch)
    with override_settings(
        SECURE_HSTS_SECONDS=prod.SECURE_HSTS_SECONDS, SECURE_HSTS_INCLUDE_SUBDOMAINS=True
    ):
        response = SecurityHeadersMiddleware(lambda r: HttpResponse())(RequestFactory().get("/"))
    assert (
        response["Strict-Transport-Security"]
        == f"max-age={prod.SECURE_HSTS_SECONDS}; includeSubDomains"
    )


# --- admin -----------------------------------------------------------------------


def walk(patterns, prefix=""):
    for pattern in patterns:
        route = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            yield route
            yield from walk(pattern.url_patterns, route)
        elif isinstance(pattern, URLPattern):
            yield route


def test_admin_absent(monkeypatch, settings):
    prod = load_prod(monkeypatch)
    for installed in (settings.INSTALLED_APPS, prod.INSTALLED_APPS):
        assert not [a for a in installed if "admin" in a.split(".")]
    routes = list(walk(get_resolver("config.urls").url_patterns))
    assert routes
    assert not [r for r in routes if r.startswith("admin")]
    assert prod.ROOT_URLCONF == "config.urls"


# --- links -----------------------------------------------------------------------


def test_frontend_url_ignores_host_header(settings):
    settings.FRONTEND_BASE_URL = "https://app.example.org"
    settings.ALLOWED_HOSTS = ["evil.example"]
    request = RequestFactory().get("/", HTTP_HOST="evil.example")
    assert request.get_host() == "evil.example"
    assert frontend_url("/verify") == "https://app.example.org/verify"
    assert frontend_url("/a/b-c_1.html") == "https://app.example.org/a/b-c_1.html"


@pytest.mark.parametrize(
    "path",
    [
        "verify",
        "https://evil.example/x",
        "//evil.example",
        "/\\evil.example",
        "/a\\b",
        "/a\nb",
        "javascript:x",
        "",
        " /x",
        "/a%2fb",
        "/a%2Fb",
        "/a%5cb",
        "/a%5Cb",
        "/../x",
        "/a/..",
        "/a/%2e%2e/b",
        "/a?x=1",
        "/a#f",
    ],
)
def test_frontend_url_rejects_unsafe_paths(path):
    with pytest.raises(ValueError):
        frontend_url(path)


# --- client address --------------------------------------------------------------


def request_from(remote, forwarded=None):
    extra = {"REMOTE_ADDR": remote}
    if forwarded is not None:
        extra["HTTP_X_FORWARDED_FOR"] = forwarded
    return RequestFactory().get("/", **extra)


def test_untrusted_source_ignores_forwarded(settings):
    settings.TRUSTED_PROXIES = []
    assert client_ip(request_from("203.0.113.9", "1.2.3.4")) == "203.0.113.9"
    settings.TRUSTED_PROXIES = ["10.0.0.0/8"]
    assert client_ip(request_from("203.0.113.9", "1.2.3.4")) == "203.0.113.9"


def test_trusted_proxy_picks_rightmost_untrusted(settings):
    settings.TRUSTED_PROXIES = ["10.0.0.0/8", "192.168.1.5"]
    forged = "6.6.6.6, 198.51.100.7, 10.1.1.1, 192.168.1.5"
    assert client_ip(request_from("10.0.0.2", forged)) == "198.51.100.7"
    assert client_ip(request_from("10.0.0.2", "2001:db8::1")) == "2001:db8::1"


def test_trusted_proxy_edge_cases(settings):
    settings.TRUSTED_PROXIES = ["10.0.0.0/8"]
    assert client_ip(request_from("10.0.0.2")) == "10.0.0.2"
    assert client_ip(request_from("10.0.0.2", "10.0.0.3, 10.0.0.4")) == "10.0.0.2"
    assert client_ip(request_from("10.0.0.2", "1.2.3.4, garbage")) == "10.0.0.2"


def test_trusted_proxies_loaded_from_env(monkeypatch):
    prod = load_prod(monkeypatch, TRUSTED_PROXIES="10.0.0.0/8, 192.168.1.5")
    assert prod.TRUSTED_PROXIES == ["10.0.0.0/8", "192.168.1.5"]


def test_client_ip_normalises_and_unknown(settings):
    settings.TRUSTED_PROXIES = ["10.0.0.0/8"]
    assert client_ip(request_from("", "1.2.3.4")) == "unknown"
    assert client_ip(request_from("not-an-ip")) == "unknown"
    assert client_ip(request_from("::ffff:203.0.113.9")) == "203.0.113.9"
    assert client_ip(request_from("2001:DB8:0::1")) == "2001:db8::1"
    assert client_ip(request_from("10.0.0.2", "198.51.100.7:4711")) == "198.51.100.7"
    assert client_ip(request_from("10.0.0.2", "[2001:db8::7]:443")) == "2001:db8::7"
    assert client_ip(request_from("10.0.0.2", "[2001:db8::7]")) == "2001:db8::7"
    assert client_ip(request_from("10.0.0.2", "::ffff:198.51.100.7")) == "198.51.100.7"
    assert client_ip(request_from("::ffff:10.0.0.2", "198.51.100.7")) == "198.51.100.7"
    assert client_ip(request_from("10.0.0.2", "::ffff:10.0.0.9, ::ffff:10.0.0.3")) == "10.0.0.2"


@pytest.mark.parametrize("value", ["1.0.0.0/7", "0.0.0.0/1", "2001::/15", "::/0"])
def test_proxy_ranges_too_broad_are_refused(monkeypatch, value):
    with pytest.raises(ImproperlyConfigured, match="TRUSTED_PROXIES"):
        load_prod(monkeypatch, TRUSTED_PROXIES=value)


@pytest.mark.parametrize("value", ["10.0.0.0/8", "2001:db8::/16", "192.168.1.5"])
def test_proxy_ranges_at_limit_are_accepted(monkeypatch, value):
    assert load_prod(monkeypatch, TRUSTED_PROXIES=value).TRUSTED_PROXIES == [value]


@pytest.mark.parametrize(
    "value",
    [
        "http://app.example.org",
        "https://app.example.org?x",
        "https://app.example.org#f",
        "https://u@app.example.org",
    ],
)
def test_prod_origins_must_be_clean_https(monkeypatch, value):
    with pytest.raises(ImproperlyConfigured, match="CSRF_TRUSTED_ORIGINS"):
        load_prod(monkeypatch, CSRF_TRUSTED_ORIGINS=value)


@pytest.mark.parametrize(
    "value", ["https://app.example.org?x", "https://app.example.org#f", "https://u@app.example.org"]
)
def test_prod_frontend_url_rejects_query_fragment_userinfo(monkeypatch, value):
    with pytest.raises(ImproperlyConfigured, match="FRONTEND_BASE_URL"):
        load_prod(monkeypatch, FRONTEND_BASE_URL=value)


def test_prod_empty_origins_allowed(monkeypatch):
    # The frontend origin is always trusted, so the list is never empty.
    prod = load_prod(monkeypatch, CSRF_TRUSTED_ORIGINS="")
    assert prod.CSRF_TRUSTED_ORIGINS == [prod.FRONTEND_BASE_URL]


def test_test_settings_always_allow_testserver(monkeypatch):
    import config.settings.test as test_settings

    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "example.org")
    try:
        assert importlib.reload(test_settings).ALLOWED_HOSTS == ["example.org", "testserver"]
    finally:
        monkeypatch.undo()
        importlib.reload(test_settings)


def test_local_settings_smoke(monkeypatch):
    import config.env as env_module

    monkeypatch.setenv("DJANGO_SECRET_KEY", "k" * 40)
    monkeypatch.setenv("RUNTIME_PASSWORD", "r" * 40)
    monkeypatch.setenv("MIGRATOR_PASSWORD", "m" * 40)
    for name in (
        "DJANGO_ALLOWED_HOSTS",
        "CSRF_TRUSTED_ORIGINS",
        "FRONTEND_BASE_URL",
        "TRUSTED_PROXIES",
        "MAIL_HOST",
        "MAIL_PORT",
        "MAIL_FROM",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(env_module, "load_dotenv", lambda path=None: None)
    # Import only after the environment is set: the module reads secrets at import
    # time, and CI has no .env file to fall back on.
    sys.modules.pop("config.settings.local", None)
    try:
        loaded = importlib.import_module("config.settings.local")
        assert loaded.ALLOWED_HOSTS == ["127.0.0.1", "localhost"]
        assert loaded.CSRF_TRUSTED_ORIGINS == ["http://localhost:3000"]
        assert loaded.FRONTEND_BASE_URL == "http://localhost:3000"
        assert loaded.TRUSTED_PROXIES == []
        for prefix in ("SESSION", "CSRF"):
            assert getattr(loaded, f"{prefix}_COOKIE_SECURE") is False
            assert getattr(loaded, f"{prefix}_COOKIE_HTTPONLY") is True
            assert getattr(loaded, f"{prefix}_COOKIE_SAMESITE") == "Lax"
    finally:
        sys.modules.pop("config.settings.local", None)


@pytest.mark.urls("tests.api.urls")
class TestCsrf:
    def test_missing_token_gets_envelope_with_headers(self):
        response = Client(enforce_csrf_checks=True).post("/csrf-post")
        assert response.status_code == 403
        body = response.json()
        assert body["error"]["code"] == "forbidden"
        assert body["error"]["request_id"] == response["X-Request-ID"]
        assert_secure_headers(response)

    def test_untrusted_origin_rejected(self):
        client = Client(enforce_csrf_checks=True)
        token = client.get("/csrf-token").json()["token"]
        response = client.post(
            "/csrf-post", HTTP_ORIGIN="http://evil.example", HTTP_X_CSRFTOKEN=token
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"
        assert "evil.example" not in response.content.decode()

    def test_trusted_origin_with_token_accepted(self):
        client = Client(enforce_csrf_checks=True)
        token = client.get("/csrf-token").json()["token"]
        response = client.post(
            "/csrf-post", HTTP_ORIGIN="http://localhost:3000", HTTP_X_CSRFTOKEN=token
        )
        assert response.status_code == 200
        assert json.loads(response.content) == {"ok": True}
        assert_secure_headers(response)


# --- CSRF trusted origins derive from FRONTEND_BASE_URL -------------------------------------


def test_prod_trusts_a_non_default_frontend_origin_normalised(monkeypatch):
    prod = load_prod(
        monkeypatch,
        FRONTEND_BASE_URL="https://Shop.Example.ORG",
        CSRF_TRUSTED_ORIGINS="https://other.example.org",
    )
    assert prod.CSRF_TRUSTED_ORIGINS == ["https://other.example.org", "https://shop.example.org"]
    assert prod.FRONTEND_BASE_URL == "https://Shop.Example.ORG"  # links keep the configured form


def test_prod_does_not_duplicate_a_listed_frontend_origin(monkeypatch):
    prod = load_prod(
        monkeypatch,
        FRONTEND_BASE_URL="https://app.example.org",
        CSRF_TRUSTED_ORIGINS="https://app.example.org",
    )
    assert prod.CSRF_TRUSTED_ORIGINS == ["https://app.example.org"]


@pytest.mark.parametrize("module", ["config.settings.test", "config.settings.local"])
def test_local_and_test_settings_derive_trusted_origins(monkeypatch, module):
    import config.env as env_module

    monkeypatch.setenv("DJANGO_SECRET_KEY", "k" * 40)
    monkeypatch.setenv("RUNTIME_PASSWORD", "r" * 40)
    monkeypatch.setenv("MIGRATOR_PASSWORD", "m" * 40)
    monkeypatch.setenv("FRONTEND_BASE_URL", "http://Front.Example.test:4000")
    # CI exports a non-loopback MAIL_HOST for the prod-settings tests; local settings need loopback.
    monkeypatch.setenv("MAIL_HOST", "127.0.0.1")
    monkeypatch.delenv("CSRF_TRUSTED_ORIGINS", raising=False)
    monkeypatch.setattr(env_module, "load_dotenv", lambda path=None: None)
    saved = sys.modules.pop(module, None)
    try:
        loaded = importlib.import_module(module)
        assert "http://front.example.test:4000" in loaded.CSRF_TRUSTED_ORIGINS
        assert loaded.FRONTEND_BASE_URL == "http://Front.Example.test:4000"
    finally:
        sys.modules.pop(module, None)
        if saved is not None:
            sys.modules[module] = saved
