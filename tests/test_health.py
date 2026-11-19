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
        mock.patch("config.health.connection") as conn,
    ):
        conn.cursor.side_effect = OperationalError(secret_detail)
        response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert json.loads(response.content) == {"status": "unavailable"}
    assert "10.9.8.7" not in response.content.decode()
    assert "10.9.8.7" not in caplog.text
