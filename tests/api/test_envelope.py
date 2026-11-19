import json
import re

import pytest

from apps.kernel.errors import CATALOGUE

pytestmark = [pytest.mark.urls("tests.api.urls")]

PHONE, EMAIL, ADDRESS = "0300-1234567", "private.person@@example", "12 Hidden Lane, Karachi"


def error_of(response):
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "fields", "request_id"}
    assert body["error"]["code"] in CATALOGUE
    assert body["error"]["request_id"] == response["X-Request-ID"]
    return body["error"]


def test_validation_error_never_echoes_submitted_values(client):
    payload = {"phone": PHONE, "email": EMAIL, "address": ADDRESS, "tags": ["x", PHONE]}
    response = client.post("/validate", json.dumps(payload), content_type="application/json")
    assert response.status_code == 400
    error = error_of(response)
    assert error["code"] == "validation_error"
    assert error["fields"]["phone"] == ["This value is too long."]
    assert error["fields"]["email"] == ["Enter a valid value."]
    assert error["fields"]["address"] == ["Select a valid choice."]
    assert error["fields"]["tags"] == ["Enter a valid value."]
    text = response.content.decode()
    for value in (PHONE, EMAIL, ADDRESS, "Hidden Lane"):
        assert value not in text


def test_missing_fields_and_malformed_body(client):
    response = client.post("/validate", "{}", content_type="application/json")
    assert error_of(response)["fields"]["phone"] == ["This field is required."]
    response = client.post("/validate", f"{{{PHONE}", content_type="application/json")
    assert response.status_code == 400
    assert error_of(response)["code"] == "bad_request"
    assert PHONE not in response.content.decode()


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [
        ("get", "/missing", 404, "not_found"),
        ("get", "/things/1", 404, "not_found"),
        ("put", "/validate", 405, "method_not_allowed"),
        ("get", "/raise/unauthenticated", 401, "unauthenticated"),
        ("get", "/raise/forbidden", 403, "forbidden"),
        ("get", "/raise/stale", 409, "stale_revision"),
        ("get", "/raise/idempotency", 422, "idempotency_key_reused"),
        ("get", "/raise/throttled", 429, "throttled"),
        ("get", "/raise/boom", 500, "server_error"),
        ("get", "/plain-failure", 500, "server_error"),
    ],
)
def test_known_errors(client, method, path, status, code):
    client.raise_request_exception = False
    response = getattr(client, method)(path)
    assert response.status_code == status
    error = error_of(response)
    assert error["code"] == code
    assert error["message"] == CATALOGUE[code].message
    assert error["fields"] == {}


def test_wrong_accept_and_content_type(client):
    response = client.get("/raise/stale", HTTP_ACCEPT="text/html")
    assert response.status_code == 406
    assert error_of(response)["code"] == "not_acceptable"
    response = client.post("/validate", "a=1", content_type="text/plain")
    assert response.status_code == 415
    assert error_of(response)["code"] == "unsupported_media_type"


def test_hostile_nested_keys_are_not_echoed(client):
    body = json.dumps({"meta": {PHONE: "x", EMAIL: {ADDRESS: "y"}}})
    response = client.post("/hostile", body, content_type="application/json")
    error = error_of(response)
    assert list(error["fields"]) == ["meta"]
    text = response.content.decode()
    for value in (PHONE, EMAIL, ADDRESS):
        assert value not in text


def test_leafless_validation_error_falls__back(client):
    error = error_of(client.get("/empty-validation"))
    assert error["fields"] == {"non_field_errors": [CATALOGUE["validation_error"].message]}


def test_django_level_400_and_403_use_the_envelope(client):
    from django.core.exceptions import PermissionDenied, SuspiciousOperation
    from django.test import RequestFactory

    from apps.presentation.api import handlers
    from apps.presentation.api.exceptions import code_for

    assert code_for(SuspiciousOperation()) == "bad_request"
    request = RequestFactory().get("/x")
    assert (
        json.loads(handlers.bad_request(request, SuspiciousOperation()).content)["error"]["code"]
        == "bad_request"
    )
    assert (
        json.loads(handlers.forbidden(request, PermissionDenied()).content)["error"]["code"]
        == "forbidden"
    )


def test_throttled_retry_after(client):
    assert client.get("/raise/throttled")["Retry-After"] == "30"
    assert "Retry-After" not in client.get("/raise/throttled-unknown")


def test_unhandled_exception_body_is_generic(client):
    client.raise_request_exception = False
    for path in ("/raise/boom", "/plain-failure"):
        text = client.get(path).content.decode()
        assert "secret@example.com" not in text
        assert PHONE not in text
        assert "RuntimeError" not in text


def test_client_supplied_request_id_is_ignored(client):
    sent = "11111111-1111-1111-1111-111111111111"
    response = client.get("/missing", HTTP_X_REQUEST_ID=sent)
    assert response["X-Request-ID"] != sent
    assert error_of(response)["request_id"] == response["X-Request-ID"]
    assert re.fullmatch(r"[0-9a-f-]{36}", response["X-Request-ID"])
