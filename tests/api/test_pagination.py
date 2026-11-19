from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.contenttypes.models import ContentType

pytestmark = [pytest.mark.urls("tests.api.urls"), pytest.mark.django_db]


@pytest.fixture
def rows():
    ContentType.objects.bulk_create(
        [ContentType(app_label="probe", model=f"m{i:03}") for i in range(120)]
    )
    return ContentType.objects.count()


def walk(client, first):
    seen, url, pages = [], first, 0
    while url:
        body = client.get(url).json()
        seen += [r["id"] for r in body["results"]]
        url, pages = body["next"], pages + 1
    return seen, pages


def test_pages_are_deterministic_and_complete(client, rows):
    seen, pages = walk(client, "/items?page_size=7")
    assert len(seen) == rows == len(set(seen))
    assert seen == sorted(seen)
    assert pages == -(-rows // 7)
    assert walk(client, "/items?page_size=7")[0] == seen


def test_default_and_max_page_size(client, rows):
    assert len(client.get("/items").json()["results"]) == 20
    assert len(client.get("/items?page_size=500").json()["results"]) == 100


def test_previous_cursor_returns_the_prior_page(client, rows):
    first = client.get("/items?page_size=10").json()
    second = client.get(first["next"]).json()
    assert first["previous"] is None
    back = client.get(second["previous"]).json()
    assert [r["id"] for r in back["results"]] == [r["id"] for r in first["results"]]


def test_invalid_cursor_is_a_not_found_envelope(client, rows):
    response = client.get("/items?cursor=garbage0300-1234567")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert "0300-1234567" not in response.content.decode()
    cursor = parse_qs(urlparse(client.get("/items").json()["next"]).query)["cursor"]
    assert cursor


def test_ordering_is_required(client, rows, caplog):
    client.raise_request_exception = False
    response = client.get("/unordered")
    assert response.status_code == 500
    assert caplog.records[-1].exception_class == "ImproperlyConfigured"


def test_multi_field_ordering_is_rejected(client, rows, caplog):
    client.raise_request_exception = False
    assert client.get("/multi").status_code == 500
    assert caplog.records[-1].exception_class == "ImproperlyConfigured"
