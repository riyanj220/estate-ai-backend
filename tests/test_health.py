import json
import logging
from unittest import mock

import pytest
from django.db import OperationalError


@pytest.mark.django_db
def test_health_ok(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_db_down_reveals_nothing(client, caplog):
    secret_detail = "connection to server at 10.9.8.7 port 5432 failed: password authentication"
    with (
        caplog.at_level(logging.DEBUG),
        mock.patch("apps.presentation.api.health.connection") as conn,
    ):
        conn.cursor.side_effect = OperationalError(secret_detail)
        response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert json.loads(response.content) == {"status": "unavailable"}
    assert "10.9.8.7" not in response.content.decode()
    assert "10.9.8.7" not in caplog.text


@pytest.mark.django_db
def test_health_head_and_other_methods(client):
    assert client.head("/api/v1/health").status_code == 200
    response = client.post("/api/v1/health")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


@pytest.mark.django_db
@pytest.mark.parametrize("accept", ["text/plain", "text/html", "application/xml"])
def test_health_ignores_accept_header(client, accept):
    response = client.get("/api/v1/health", HTTP_ACCEPT=accept)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
