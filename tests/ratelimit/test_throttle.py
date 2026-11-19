import uuid

import pytest

from tests.authz.conftest import as_identity

pytestmark = [pytest.mark.urls("tests.ratelimit.urls")]
A = uuid.UUID("11111111-1111-4111-8111-111111111111")
B = uuid.UUID("22222222-2222-4222-8222-222222222222")


@pytest.fixture(autouse=True)
def header_identity(settings):
    settings.MIDDLEWARE = [*settings.MIDDLEWARE, "tests.authz.middleware.HeaderIdentityMiddleware"]


def test_over_limit_returns_envelope_with_retry_after(client, probe):
    for _ in range(3):
        assert client.get("/limited").status_code == 200
    response = client.get("/limited")
    assert response.status_code == 429
    body = response.json()["error"]
    assert body["code"] == "throttled" and body["request_id"]
    assert 1 <= int(response["Retry-After"]) <= 60


def test_client_scope_counts_signed_in_users_by_address_too(client, probe):
    for _ in range(3):
        assert client.get("/limited", **as_identity("user", A)).status_code == 200
    assert client.get("/limited", **as_identity("user", B)).status_code == 429
    assert client.get("/limited").status_code == 429


def test_account_scope_counts_accounts_separately(client, probe_account):
    for _ in range(3):
        assert client.get("/limited-account", **as_identity("user", A)).status_code == 200
    assert client.get("/limited-account", **as_identity("user", A)).status_code == 429
    assert client.get("/limited-account", **as_identity("user", B)).status_code == 200


def test_account_scope_refuses_a_visitor_as_a_configuration_error(client, probe_account):
    assert client.get("/limited-account").status_code == 500


def test_service_path_rate_limited_maps_to_429_with_retry_after(client, probe):
    for _ in range(3):
        assert client.get("/service").status_code == 200
    response = client.get("/service")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "throttled"
    assert response["Retry-After"] == "50"


def test_rotating_forwarded_header_cannot_escape_the_limit(client, probe, settings):
    settings.TRUSTED_PROXIES = []
    codes = [
        client.get("/limited", HTTP_X_FORWARDED_FOR=f"1.1.1.{n}").status_code for n in range(5)
    ]
    assert codes == [200, 200, 200, 429, 429]


def test_view_without_scope_is_a_configuration_error(client, probe):
    response = client.get("/unscoped")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "server_error"
