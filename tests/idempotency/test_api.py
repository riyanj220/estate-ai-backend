import pytest

pytestmark = [pytest.mark.urls("tests.idempotency.urls"), pytest.mark.django_db]


def test_key_reuse_maps_to_the_422_envelope(client):
    assert client.post("/claim/1").status_code == 200
    assert client.post("/claim/1").status_code == 200
    response = client.post("/claim/2")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "idempotency_key_reused"
