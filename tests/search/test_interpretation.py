"""From a recorded model answer to the public filter (Stories 6.1 and 6.2). No model is called."""

import json

import pytest

from apps.catalog import filters
from apps.catalog import selectors as catalog
from apps.search import interpretation, labelled

pytestmark = pytest.mark.django_db

ITEMS = labelled.load()


def read(query="a house", **answer):
    return interpretation.interpret(json.dumps(answer), query)


def number(field, comparison, value, unit=None, must=False):
    return {"field": field, "comparison": comparison, "value": value, "unit": unit, "must": must}


def test_the_labelled_set_is_large_enough_and_each_query_is_new():
    assert len(ITEMS) >= 60
    assert len({item["query"] for item in ITEMS}) == len(ITEMS)


@pytest.mark.parametrize("item", ITEMS, ids=[item["query"] for item in ITEMS])
def test_labelled_query(item):
    found = interpretation.interpret(labelled.answer_of(item), item["query"])
    assert labelled.named(found.params) == item["params"]
    assert list(found.unsupported) == item.get("unsupported", [])
    assert found.quality != interpretation.REJECTED
    filters.parse({name: [value] for name, value in found.params.items()})


@pytest.mark.parametrize(
    "answer",
    ["This is a stub reply.", "[1]", "{}", '{"sql": "DROP TABLE x"}', "{", "[" * 30_000],
)
def test_an_unusable_answer_is_rejected(answer):
    found = interpretation.interpret(answer, "a house")
    assert found.quality == interpretation.REJECTED and found.empty


def test_what_does_not_fit_is_dropped_and_the_rest_kept():
    found = read(
        purpose="steal",
        categories=["house", "castle"],
        numbers=[
            number("owner_account_id", "exactly", "1"),
            number("asking_price", "under", "lots"),
            number("bedrooms", "at_least", "2.5"),
            number("bathrooms", "at_least", "999"),
            number(["asking_price"], "under", "1"),
            "bedrooms",
        ],
        options=[{"field": "furnishing", "value": "gold"}, {"field": "status", "value": "draft"}],
        amenities=[{"code": "helipad"}],
        unsupported=["not in the request"],
        extra="ignored",
    )
    assert found.params == {"category": "house"}
    assert found.unsupported == ()
    assert found.quality == interpretation.REPAIRED


def test_an_inverted_range_is_dropped():
    found = read(
        purpose="sale",
        numbers=[
            number("asking_price", "at_least", "2", "crore"),
            number("asking_price", "at_most", "1", "crore"),
        ],
    )
    assert found.params == {"purpose": "sale"} and found.quality == interpretation.REPAIRED


def test_a_clean_answer_is_reported_as_clean():
    assert read(purpose="rent").quality == interpretation.CLEAN


def test_a_short_name_means_every_locality_that_starts_with_it():
    found = read("flat in DHA", localities=["DHA"])
    names = labelled.named(found.params)["locality"]
    assert "DHA Phase 6" in names and all(name.startswith("DHA ") for name in names)


def test_notices_explain_the_marla_size_and_seller_declarations():
    found = read(
        "5 marla near a park",
        numbers=[number("area", "unstated", "5", "marla")],
        amenities=[{"code": "nearby_park", "must": False}],
    )
    text = " ".join(found.notices)
    assert "225 to 272.25" in text and "left out" in text and "seller-declared" in text


def test_the_schema_offers_only_filterable_fields_and_catalogue_codes():
    schema = interpretation.schema()
    assert schema["additionalProperties"] is False
    fields = schema["properties"]["numbers"]["items"]["properties"]["field"]["enum"]
    assert set(fields) == {"asking_price", "bedrooms", "bathrooms", "parking_spaces", "area"}
    codes = schema["properties"]["amenities"]["items"]["properties"]["code"]["enum"]
    assert set(codes) == set(catalog.amenity_codes())
