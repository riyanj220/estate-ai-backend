"""`GET /api/v1/registry` (Story 2.1)."""

import pytest
from django.test import Client

from apps.catalog import registry
from apps.presentation.api.registry import RESPONSE_SHAPE_VERSION
from tests.listings.support import LOCATION_IDS, TAIL_IDS, TERMS_IDS

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
    "allowed_values_by_subtype",
    "seller_declared",
    "section",
    "min_length",
    "max_length",
    "min",
    "max",
    "currency",
    "scale",
    "derived",
    "description",
    "reference",
}


def test_served_publicly_with_the_defined_shape():
    response = Client().get(URL)
    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["version", "taxonomy", "fields"]
    assert body["version"] == 7
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
        *TAIL_IDS,
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
    assert tag == f'"7-{registry.content_hash()}-{RESPONSE_SHAPE_VERSION}"' and not tag.startswith(
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
    assert RESPONSE_SHAPE_VERSION == 6
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


def test_the_residential_fields_are_served_in_registry_order_with_their_rules():
    served = {f["id"]: f for f in Client().get(URL).json()["fields"]}
    bedrooms = served["bedrooms"]
    assert (bedrooms["type"], bedrooms["min"], bedrooms["max"]) == ("integer", "0", "50")
    assert bedrooms["requirement"] == "required_for_publication"
    assert bedrooms["searchable"] and bedrooms["valuation_input"] and bedrooms["ai_extractable"]
    assert bedrooms["applicability"] == {
        "purposes": [],
        "categories": [],
        "subtypes": ["house", "apartment"],
    }
    assert bedrooms["scale"] is None and bedrooms["currency"] is None
    assert bedrooms["allowed_values"] is None
    assert (served["construction_year"]["min"], served["construction_year"]["max"]) == (
        "1900",
        "2100",
    )
    assert served["lift"]["type"] == "boolean" and served["lift"]["min"] is None
    assert served["lift"]["applicability"]["subtypes"] == ["apartment", "office"]
    assert served["kitchens"]["applicability"]["subtypes"] == ["house"]
    assert served["condition"]["allowed_values"] == ["new", "excellent", "good", "needs_renovation"]
    covered = served["covered_area_sqft"]
    assert (covered["type"], covered["scale"], covered["max"]) == (
        "decimal",
        4,
        "9999999999.9999",
    )
    charges = served["maintenance_charges"]
    assert (charges["type"], charges["min"], charges["currency"]) == ("money", "0", "PKR")
    assert charges["requirement"] == "optional"
    assert "E" not in "".join(f"{f['min']}{f['max']}" for f in served.values() if f["min"])
    assert {"integer", "boolean"} <= {f["type"] for f in served.values()}


def test_seller_declared_and_the_by_subtype_map_are_served_and_the_shape_is_bumped():
    assert (
        RESPONSE_SHAPE_VERSION >= 4
    )  # the payload gained two keys per field in version 5 of the registry
    served = {f["id"]: f for f in Client().get(URL).json()["fields"]}
    assert {n for n, f in served.items() if f["seller_declared"]} == {
        "possession_status",
        "balloting_status",
        "development_status",
        "approval_authority",
        "plot_file_status",
        "possession_availability",
    }
    for field in served.values():
        assert isinstance(field["seller_declared"], bool)
        assert field["allowed_values_by_subtype"] is None or isinstance(
            field["allowed_values_by_subtype"], dict
        )
    possession = served["possession_status"]
    assert possession["allowed_values"] == ["possession_ready", "possession_pending", "file_only"]
    assert possession["allowed_values_by_subtype"] == {
        "residential_plot": ["possession_ready", "possession_pending"],
        "commercial_plot": ["possession_ready", "possession_pending"],
        "plot_file": ["file_only"],
    }
    assert possession["applicability"]["subtypes"] == [
        "residential_plot",
        "commercial_plot",
        "plot_file",
    ]
    assert [n for n, f in served.items() if f["allowed_values_by_subtype"] is not None] == [
        "possession_status"
    ]  # no new map in version 6
    assert served["condition"]["seller_declared"] is False
    assert served["condition"]["allowed_values_by_subtype"] is None


def test_the_plot_and_commercial_fields_are_served_in_registry_order_with_their_rules():
    body = Client().get(URL).json()
    assert [f["id"] for f in body["fields"]][-len(TAIL_IDS) :] == list(TAIL_IDS)
    served = {f["id"]: f for f in body["fields"]}
    width = served["plot_width"]
    assert (width["type"], width["min"], width["max"], width["scale"]) == (
        "decimal",
        "0.01",
        "99999.99",
        2,
    )
    assert width["requirement"] == "recommended" and width["valuation_input"] is True
    assert (served["road_width_ft"]["min"], served["road_width_ft"]["max"]) == ("1.00", "999.99")
    assert (served["ceiling_height_ft"]["min"], served["ceiling_height_ft"]["max"]) == (
        "1.00",
        "99.99",
    )
    assert served["frontage_ft"]["applicability"]["subtypes"] == ["shop"]
    assert served["plot_file_status"]["applicability"]["subtypes"] == ["plot_file"]
    assert served["conference_rooms"]["applicability"]["subtypes"] == ["office"]
    assert (served["conference_rooms"]["min"], served["conference_rooms"]["max"]) == ("0", "50")
    for name in ("electricity", "water", "gas"):
        assert served[name]["type"] == "boolean" and served[name]["ai_extractable"] is True
        assert served[name]["class"] == "public"
    assert served["floor_number"]["applicability"]["subtypes"] == ["apartment", "shop", "office"]
    assert served["building_security"]["applicability"]["subtypes"] == [
        "apartment",
        "shop",
        "office",
    ]
    assert served["gas"]["requirement"] == "optional" and served["water"]["requirement"] == (
        "recommended"
    )
    for name in ("business_use", "frontage_ft", "road_access"):
        assert served[name]["requirement"] == "recommended"
    for field in served.values():
        for key in ("min", "max"):
            assert field[key] is None or "E" not in field[key].upper()


def test_the_etag_names_version_seven_and_the_new_shape():
    tag = Client().get(URL).headers["ETag"]
    assert tag == f'"7-{registry.content_hash()}-6"'


def test_the_date_type_and_the_terms_are_served_in_the_existing_keys():
    assert RESPONSE_SHAPE_VERSION == 6  # `reference` was added in 7; a date serves `min` and `max`
    body = Client().get(URL).json()
    served = {f["id"]: f for f in body["fields"]}
    for field in body["fields"]:
        assert set(field) == FIELD_KEYS
    availability = served["availability_date"]
    assert availability["type"] == "date" and availability["requirement"] == "recommended"
    assert (availability["min"], availability["max"]) == ("2000-01-01", "2100-12-31")
    assert availability["applicability"] == {"purposes": ["rent"], "categories": [], "subtypes": []}
    assert availability["currency"] is None and availability["scale"] is None
    assert availability["allowed_values"] is None and availability["seller_declared"] is False
    dates = [f["id"] for f in body["fields"] if f["type"] == "date"]
    assert dates == ["availability_date"]
    # only a date serves date text; the others keep decimal strings or null
    # (a coordinate's bound is the one other minus sign: latitude and longitude start below zero)
    assert all(
        f["min"] is None
        or f["type"] == "date"
        or "-" not in f["min"]
        or n in ("latitude", "longitude")
        for n, f in served.items()
    )
    deposit = served["security_deposit"]
    assert (deposit["type"], deposit["min"], deposit["max"], deposit["currency"]) == (
        "money",
        "0",
        "999999999999.99",
        "PKR",
    )
    assert deposit["requirement"] == "recommended"
    assert served["advance_rent"]["requirement"] == "optional"
    assert (served["minimum_lease_months"]["min"], served["minimum_lease_months"]["max"]) == (
        "1",
        "120",
    )
    assert (served["notice_period_days"]["min"], served["notice_period_days"]["max"]) == (
        "0",
        "365",
    )
    rent_only = {"purposes": ["rent"], "categories": [], "subtypes": []}
    for name in (
        "security_deposit",
        "availability_date",
        "advance_rent",
        "minimum_lease_months",
        "notice_period_days",
        "utility_electricity_included",
        "utility_gas_included",
        "utility_water_included",
        "utility_internet_included",
        "pets_allowed",
        "occupant_restrictions",
    ):
        assert served[name]["applicability"] == rent_only, name
    possession = served["possession_availability"]
    assert possession["applicability"] == {"purposes": ["sale"], "categories": [], "subtypes": []}
    assert possession["allowed_values"] == [
        "available_now",
        "within_one_month",
        "within_three_months",
        "after_three_months",
    ]
    assert possession["seller_declared"] is True and possession["ai_extractable"] is False
    assert served["price_negotiable"]["applicability"] == {
        "purposes": [],
        "categories": [],
        "subtypes": [],
    }
    assert served["occupant_restrictions"]["allowed_values"] == [
        "no_restriction",
        "families_only",
        "bachelors_only",
    ]
    assert served["occupant_restrictions"]["seller_declared"] is False
    assert served["rental_period"]["requirement"] == "required_for_publication"
    assert served["rental_period"]["applicability"]["purposes"] == ["rent"]
    assert not any(f["valuation_input"] for n, f in served.items() if n in TERMS_IDS)
    assert not any(f["searchable"] or f["sortable"] for n, f in served.items() if n in TERMS_IDS)


def test_the_etag_changes_with_a_date_bound_and_the_conditional_get_follows_it(monkeypatch):
    first = Client().get(URL)
    tag = first.headers["ETag"]
    assert Client().get(URL, HTTP_IF_NONE_MATCH=tag).status_code == 304
    assert Client().get(URL, HTTP_IF_NONE_MATCH='"5-abc-4"').status_code == 200
    # a registry whose date window differs has another hash, hence another tag and a full 200
    changed = registry.parse(registry.FIELDS_PATH.read_text().replace("2100-12-31", "2099-12-31"))
    assert changed.content_hash != registry.content_hash()
    monkeypatch.setattr(registry, "load", lambda: changed)
    after = Client().get(URL)
    assert after.headers["ETag"] != tag
    assert any(f["max"] == "2099-12-31" for f in after.json()["fields"])
    assert Client().get(URL, HTTP_IF_NONE_MATCH=tag).status_code == 200


def test_the_section_marker_is_served_for_every_field():
    served = {f["id"]: f for f in Client().get(URL).json()["fields"]}
    terms = {
        "price_negotiable",
        "possession_availability",
        "security_deposit",
        "availability_date",
        "advance_rent",
        "minimum_lease_months",
        "notice_period_days",
        "utility_electricity_included",
        "utility_gas_included",
        "utility_water_included",
        "utility_internet_included",
        "pets_allowed",
        "occupant_restrictions",
        "rental_period",
    }
    assert {n for n, f in served.items() if f["section"] == "terms"} == terms
    assert {f["section"] for f in served.values()} == {"details", "terms", "location"}
    assert {n for n, f in served.items() if f["section"] == "location"} == set(LOCATION_IDS)
    assert served["asking_price"]["section"] == "details"
    assert served["bedrooms"]["section"] == "details"
