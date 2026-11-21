"""`GET /api/v1/registry` (Story 2.1)."""

import pytest
from django.test import Client

from apps.catalog import registry
from apps.presentation.api.registry import RESPONSE_SHAPE_VERSION

URL = "/api/v1/registry"
FIELD_KEYS = {
    "id",
    "type",
    "requirement",
    "applicability",
    "allowed_values",
    "class",
    "searchable",
    "sortable",
    "ai_extractable",
    "valuation_input",
    "quota_identity",
    "allowed_values_by_purpose",
    "min_length",
    "max_length",
    "min",
    "max",
    "currency",
    "scale",
    "derived",
    "description",
}


def test_served_publicly_with_the_defined_shape():
    response = Client().get(URL)
    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["version", "taxonomy", "fields"]
    assert body["version"] == 3
    assert body["taxonomy"] == {
        "categories": [
            {"id": "house", "subtypes": ["house"]},
            {"id": "apartment", "subtypes": ["apartment"]},
            {"id": "plot", "subtypes": ["residential_plot", "commercial_plot", "plot_file"]},
            {"id": "commercial", "subtypes": ["shop", "office"]},
        ],
        "purposes": ["sale", "rent"],
    }
    assert [f["id"] for f in body["fields"]] == [
        "purpose",
        "category",
        "subtype",
        "title",
        "description",
        "asking_price",
        "price_basis",
        "rental_period",
        "area_value",
        "area_unit",
        "marla_basis",
        "area_sqft",
    ]
    for field in body["fields"]:
        assert set(field) == FIELD_KEYS
    for field in body["fields"][:3]:
        assert field["class"] == "public" and field["quota_identity"] is True
        assert field["searchable"] is False and field["requirement"] == "required_for_publication"
        assert field["applicability"] == {"purposes": [], "categories": [], "subtypes": []}
    assert body["fields"][0]["allowed_values"] == ["sale", "rent"]
    assert body["fields"][2]["allowed_values"] == [
        "house",
        "apartment",
        "residential_plot",
        "commercial_plot",
        "plot_file",
        "shop",
        "office",
    ]
    assert body["fields"][1]["allowed_values"] == ["house", "apartment", "plot", "commercial"]


def test_the_editing_fields_are_described_for_clients():
    fields = {f["id"]: f for f in Client().get(URL).json()["fields"]}
    title, description, price = fields["title"], fields["description"], fields["asking_price"]
    assert (title["type"], title["min_length"], title["max_length"]) == ("text", 10, 100)
    assert (description["type"], description["max_length"]) == ("text", 5000)
    assert description["requirement"] == "recommended"
    assert (price["type"], price["min"], price["max"], price["currency"]) == (
        "money",
        "0.01",
        "999999999999.99",
        "PKR",
    )
    assert price["valuation_input"] is False and price["ai_extractable"] is True
    basis = fields["price_basis"]
    assert basis["allowed_values_by_purpose"] == {
        "sale": ["total", "per_unit"],
        "rent": ["rental_period"],
    }
    period = fields["rental_period"]
    assert period["allowed_values"] == ["day", "week", "month", "year"]
    assert period["applicability"]["purposes"] == ["rent"]
    assert title["min"] is None and title["allowed_values_by_purpose"] is None


def test_no_internal_paths_or_hash_in_the_body():
    text = Client().get(URL).content.decode()
    for leak in ("/home", "fields.yaml", "fields.lock", registry.content_hash(), ".py"):
        assert leak not in text


def test_cache_headers_and_strong_etag():
    response = Client().get(URL)
    assert response.headers["Cache-Control"] == "public, max-age=300"
    tag = response.headers["ETag"]
    assert tag == f'"3-{registry.content_hash()}-{RESPONSE_SHAPE_VERSION}"' and not tag.startswith(
        "W/"
    )


def test_if_none_match_gets_304():
    client = Client()
    tag = client.get(URL).headers["ETag"]
    for header in (tag, f"W/{tag}", f'"other", {tag}', "*"):
        response = client.get(URL, HTTP_IF_NONE_MATCH=header)
        assert response.status_code == 304 and response.content == b""
        assert response.headers["ETag"] == tag
        assert response.headers["Cache-Control"] == "public, max-age=300"
    assert client.get(URL, HTTP_IF_NONE_MATCH='"stale"').status_code == 200


def test_the_etag_changes_with_the_version_or_content(monkeypatch):
    before = Client().get(URL).headers["ETag"]
    monkeypatch.setattr(registry, "load", lambda: registry.Registry(2, "ab" * 32, (), (), ()))
    assert Client().get(URL).headers["ETag"] != before


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_read_only(method):
    response = getattr(Client(), method)(URL)
    assert response.status_code == 405


def test_a_shape_change_alone_changes_the_etag(monkeypatch):
    before = Client().get(URL).headers["ETag"]
    monkeypatch.setattr("apps.presentation.api.registry.RESPONSE_SHAPE_VERSION", 99)
    after = Client().get(URL)
    assert after.headers["ETag"] != before
    assert Client().get(URL, HTTP_IF_NONE_MATCH=before).status_code == 200


def test_the_shape_version_is_pinned_and_the_area_fields_are_served_in_full():
    assert RESPONSE_SHAPE_VERSION == 3
    served = {f["id"]: f for f in Client().get(URL).json()["fields"]}
    value = served["area_value"]
    assert (value["type"], value["min"], value["max"], value["scale"]) == (
        "decimal",
        "0.0001",
        "9999999999.9999",
        4,
    )
    assert value["derived"] is False and value["currency"] is None
    sqft = served["area_sqft"]
    assert (sqft["derived"], sqft["scale"], sqft["max"]) == (True, 4, "9999999999999999.9999")
    basis = served["marla_basis"]
    assert basis["allowed_values"] == ["225", "250", "272.25"]
    assert basis["description"] == (
        "Square feet in one marla. Applies only while the unit is marla or kanal."
    )
    assert served["title"]["description"] is None and served["title"]["scale"] is None
    assert served["asking_price"]["min"] == "0.01" and served["asking_price"]["scale"] is None
    assert "E" not in "".join(f"{f['min']}{f['max']}" for f in served.values() if f["min"])
