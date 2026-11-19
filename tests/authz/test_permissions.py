import uuid

import pytest
from django.test import override_settings

from tests.authz.conftest import ACCOUNT, as_identity

pytestmark = pytest.mark.urls("tests.authz.urls")


def error_of(response):
    error = response.json()["error"]
    assert error["request_id"] == response["X-Request-ID"]
    return error


def test_public_route_resolves_a_visitor_with_request_id(client):
    response = client.get("/who")
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "visitor"
    assert body["account_id"] is None
    assert body["request_id"] == response["X-Request-ID"]


def test_client_headers_cannot_claim_an_identity(client):
    response = client.get("/signed-in", headers={"X-Actor": "admin", "X-Account-Id": str(ACCOUNT)})
    assert response.status_code == 401


def test_visitor_on_signed_in_route_gets_unauthenticated(client):
    response = client.get("/signed-in")
    assert response.status_code == 401
    assert error_of(response)["code"] == "unauthenticated"


def test_signed_in_user_passes_signed_in_route(client):
    response = client.get("/signed-in", **as_identity("user"))
    assert response.status_code == 200
    assert response.json()["account_id"] == str(ACCOUNT)


def test_visitor_on_admin_route_gets_unauthenticated(client):
    response = client.get("/admin-only")
    assert response.status_code == 401
    assert error_of(response)["code"] == "unauthenticated"


def test_non_admin_on_admin_route_gets_not_found(client):
    response = client.get("/admin-only", **as_identity("user"))
    assert response.status_code == 404
    assert error_of(response)["code"] == "not_found"


def test_admin_passes_admin_route(client):
    response = client.get("/admin-only", **as_identity("admin"))
    assert response.status_code == 200
    assert response.json()["kind"] == "admin"


def test_malformed_identity_fails_closed(client):
    assert client.get("/signed-in", **as_identity("admin", "nope")).status_code == 401
    assert client.get("/signed-in", **as_identity("root")).status_code == 401


def test_hidden_resource_looks_like_a_missing_one(client):
    from tests.authz import sample_service

    owner, other, widget_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sample_service.STORE[widget_id] = sample_service.Widget(widget_id, owner, "w")
    try:
        hidden = client.get(f"/widgets/{widget_id}", **as_identity("user", other))
        missing = client.get(f"/widgets/{uuid.uuid4()}", **as_identity("user", other))
        mine = client.get(f"/widgets/{widget_id}", **as_identity("user", owner))
    finally:
        sample_service.STORE.pop(widget_id, None)
    assert hidden.status_code == missing.status_code == 404
    strip = lambda r: {k: v for k, v in error_of(r).items() if k != "request_id"}  # noqa: E731
    assert strip(hidden) == strip(missing)
    assert error_of(hidden)["code"] == "not_found"
    assert mine.status_code == 200


@override_settings(ROOT_URLCONF="tests.authz.bad_urls")
def test_view_without_permission_is_denied_with_envelope(client):
    response = client.get("/undeclared", **as_identity("admin"))
    assert response.status_code == 403
    assert error_of(response)["code"] == "forbidden"
    assert client.get("/undeclared").status_code == 403


@pytest.fixture
def widget():
    from tests.authz import sample_service

    owner = uuid.uuid4()
    w = sample_service.Widget(uuid.uuid4(), owner, "before")
    sample_service.STORE[w.id] = w
    yield w
    sample_service.STORE.pop(w.id, None)


def test_mutating_route_visitor_gets_401(client, widget):
    response = client.post(f"/widgets/{widget.id}")
    assert response.status_code == 401
    assert widget.name == "before"


@pytest.mark.parametrize("kind", ["user", "admin"])
def test_mutating_route_non_owner_gets_404_and_nothing_changes(client, widget, kind):
    response = client.post(f"/widgets/{widget.id}", **as_identity(kind, uuid.uuid4()))
    assert response.status_code == 404
    assert error_of(response)["code"] == "not_found"
    assert widget.name == "before"


def test_mutating_route_owner_succeeds(client, widget):
    response = client.post(f"/widgets/{widget.id}", **as_identity("user", widget.owner_id))
    assert response.status_code == 200
    assert widget.name == "renamed"
