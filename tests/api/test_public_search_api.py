"""Filters, preferences, sorting and cursors of the public list (Story 3.3)."""

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.catalog import filters, registry
from apps.catalog import selectors as catalog
from apps.identity.models import Account
from apps.kernel.actor import Actor
from apps.listings import selectors
from tests.listings.support import (
    make_account,
    random_email,
    ready_draft,
    seed_cap,
    set_account_state,
    set_first_published,
    set_listing_visibility,
)

URL = "/api/v1/public/listings"
FILTERS_URL = "/api/v1/public/filters"
START = datetime(2026, 5, 1, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap(200)


@pytest.fixture
def owner():
    return make_account(random_email())


def published(owner, minutes=0, **extra):
    view = ready_draft(owner, extra=extra)
    set_listing_visibility(view.id)
    set_first_published(view.id, START + timedelta(minutes=minutes))
    return str(view.id)


def amenity_codes(kind=None, count=2):
    return [a.code for a in catalog.list_amenities() if kind in (None, a.kind)][:count]


def search(query=""):
    response = Client().get(f"{URL}?{query}")
    assert response.status_code == 200, response.content
    return response.json()


def found(query=""):
    return [card["id"] for card in search(query)["results"]]


def walk(query):
    body = search(query + "&limit=2")
    seen = [card["id"] for card in body["results"]]
    while body["next"]:
        body = search(body["next"].split("?", 1)[1])
        seen += [card["id"] for card in body["results"]]
    return seen


def refused(query):
    response = Client().get(f"{URL}?{query}")
    assert response.status_code == 400, response.content
    assert response["Cache-Control"] == "no-store"
    return response.json()["error"]["fields"]


def test_every_criterion_type(owner):
    gym, nearby = amenity_codes(count=1)[0], amenity_codes("nearby", 1)[0]
    first = published(
        owner,
        1,
        bedrooms=2,
        bathrooms=1,
        parking_spaces=0,
        furnishing="furnished",
        asking_price="100",
        area_value="1000",
        area_unit="sqft",
        marla_basis=None,
        amenities={gym: "present", nearby: "present"},
    )
    second = published(
        owner,
        2,
        bedrooms=5,
        bathrooms=4,
        parking_spaces=2,
        furnishing="unfurnished",
        asking_price="900",
        area_value="3000",
        area_unit="sqft",
        marla_basis=None,
        amenities={gym: "present", nearby: "none"},
    )
    card = search()["results"][0]
    city, locality = card["city"]["id"], card["locality"]["id"]
    both = [second, first]
    cases = {
        f"city={city}": both,
        f"city={uuid.uuid4()}": [],
        f"city={uuid.uuid4()},{city.upper()}": both,
        f"locality={locality}": both,
        "purpose=sale": both,
        "purpose=rent": [],
        "category=house&subtype=house": both,
        "category=plot": [],
        "category=plot,house": both,
        "price_basis=total": both,
        "rental_period=month": [],
        "asking_price_min=100&asking_price_max=100": [first],
        "asking_price_min=101": [second],
        "area_sqft_max=2000": [first],
        "area_sqft_min=0": both,
        "bedrooms_min=3": [second],
        "bathrooms_max=1": [first],
        "parking_spaces_min=1": [second],
        "furnishing=furnished": [first],
        f"amenities={gym}": both,
        f"amenities={gym},{nearby}": [first],
        "bedrooms_min=3&asking_price_max=500": [],
    }
    for query, expected in cases.items():
        assert found(query) == expected, query


def test_an_unanswered_attribute_never_matches(owner):
    marla = published(owner, area_value="5", marla_basis=None)  # no basis: no area_sqft
    assert found() == [marla]
    for query in (
        "area_sqft_min=0",
        "area_sqft_max=999999",
        "parking_spaces_min=0",
        "furnishing=unfurnished",
        f"amenities={amenity_codes(count=1)[0]}",
    ):
        assert found(query) == [], query


@pytest.mark.parametrize("state", ["draft", "restricted", "suspended"])
def test_a_hidden_listing_never_matches(owner, state):
    code = amenity_codes(count=1)[0]
    listing = published(owner, amenities={code: "present"})
    if state == "draft":
        set_listing_visibility(listing, status="draft")
    elif state == "restricted":
        set_listing_visibility(listing, restricted=True)
    else:
        set_account_state(owner.id, state)
        assert Account.objects.filter(id=owner.id).exists()
    for query in ("", "bedrooms_min=0", f"amenities={code}", "prefer_bedrooms_min=1"):
        assert found(query) == [], query
    assert found("sort=price_asc") == []


def test_preferences_rank_and_never_exclude(owner):
    one, two = amenity_codes()
    neither = published(owner, 3, bedrooms=1)
    some = published(owner, 2, bedrooms=1, amenities={one: "present"})
    most = published(owner, 1, bedrooms=4, amenities={one: "present", two: "present"})
    query = f"prefer_amenities={one},{two}&prefer_bedrooms_min=3"
    assert found(query) == [most, some, neither]
    assert walk(query + "&sort=relevance") == [most, some, neither]
    assert found(query + "&sort=newest") == [neither, some, most]
    assert found(query + "&bedrooms_max=1") == [some, neither]


@pytest.mark.parametrize(
    ("sort", "expected"),
    [("price_asc", (0, 1, 2)), ("price_desc", (2, 1, 0)), ("newest", (2, 1, 0))],
)
def test_sorts_paginate_with_ties(owner, sort, expected):
    prices = ["100", "200", "200", "200", "300"]
    made = [published(owner, i, asking_price=price) for i, price in enumerate(prices)]
    if sort == "newest":
        order = made[::-1]
    else:
        groups = ([made[0]], sorted(made[1:4], reverse=True), [made[4]])
        order = [listing for i in expected for listing in groups[i]]
    assert walk(f"sort={sort}") == order


def test_relevance_paginates_with_ties(owner):
    made = [published(owner, 1, bedrooms=n) for n in (3, 3, 3, 1, 1)]
    expected = sorted(made[:3], reverse=True) + sorted(made[3:], reverse=True)
    assert walk("prefer_bedrooms_min=2") == expected


def test_a_cursor_belongs_to_its_filter_and_sort(owner):
    for minutes in range(3):
        published(owner, minutes)
    cursor = search("bedrooms_min=1&limit=1")["next"].split("cursor=")[1].split("&")[0]
    assert len(found(f"bedrooms_min=1&cursor={cursor}")) == 2
    for query in ("", "bedrooms_min=2", "bedrooms_min=1&sort=price_asc", "prefer_bedrooms_min=1"):
        assert list(refused(f"{query}&cursor={cursor}")) == ["cursor"], query


@pytest.mark.parametrize(
    ("query", "name"),
    [
        ("colour=red", "colour"),
        ("title=house", "title"),
        ("street=x", "street"),
        ("latitude_min=1", "latitude_min"),
        ("prefer_house_number=1", "prefer_house_number"),
        ("area_value_min=1", "area_value_min"),
        ("city=karachi", "city"),
        ("purpose=lease", "purpose"),
        ("subtype=", "subtype"),
        ("amenities=teleporter", "amenities"),
        ("amenities=" + ",".join(["a"] * 51), "amenities"),
        ("bedrooms_min=-1", "bedrooms_min"),
        ("bedrooms_min=2.5", "bedrooms_min"),
        ("bedrooms_max=51", "bedrooms_max"),
        ("asking_price_min=NaN", "asking_price_min"),
        ("area_sqft_min=1e3", "area_sqft_min"),
        ("bedrooms_min=3&bedrooms_max=2", "bedrooms_max"),
        ("prefer_asking_price_min=3&prefer_asking_price_max=2", "prefer_asking_price_max"),
        ("purpose=sale&purpose=rent", "purpose"),
        ("sort=relevance", "sort"),
    ],
)
def test_invalid_input_names_the_parameter(query, name):
    assert list(refused(query)) == [name]


def test_a_private_field_cannot_be_searched_even_by_a_built_spec():
    spec = filters.ListingFilter((filters.Criterion("street", filters.CHOICE, ("x",)),))
    with pytest.raises(ValueError):
        selectors.search_public(Actor.visitor("r"), spec)


def test_the_filter_schema():
    response = Client().get(FILTERS_URL)
    assert response.status_code == 200
    assert response["Cache-Control"] == "public, max-age=60"
    body = response.json()
    assert body["sorts"] == ["newest", "price_asc", "price_desc", "relevance"]
    by_id = {item["id"]: item for item in body["filters"]}
    assert set(by_id) == {
        "purpose", "category", "subtype", "asking_price", "price_basis", "rental_period",
        "area_sqft", "bedrooms", "bathrooms", "furnishing", "parking_spaces", "city",
        "locality", "amenities", "payment_type", "title_document_type", "mortgage_declaration",
    }  # fmt: skip
    assert by_id["bedrooms"]["params"] == ["bedrooms_min", "bedrooms_max"]
    assert by_id["bedrooms"]["max"] == "50"
    assert by_id["purpose"]["allowed_values"] == ["sale", "rent"]
    assert by_id["city"]["reference"] == "city"
    assert "nearby" in {option["kind"] for option in by_id["amenities"]["options"]}
    private = {f.id for f in registry.fields() if f.field_class == "private"}
    assert private and not private & set(by_id)


def test_a_newly_searchable_field_is_filterable_without_code_changes(owner, monkeypatch):
    real = registry.load()
    patched = dataclasses.replace(
        real,
        fields=tuple(
            dataclasses.replace(f, searchable=True) if f.id == "condition" else f
            for f in real.fields
        ),
    )
    good = published(owner, condition="good")
    published(owner, condition="new")
    assert list(refused("condition=good")) == ["condition"]
    monkeypatch.setattr(registry, "load", lambda: patched)
    assert found("condition=good") == [good]
    ids = [item["id"] for item in Client().get(FILTERS_URL).json()["filters"]]
    assert "condition" in ids


def test_the_query_count_does_not_grow_with_the_page(owner):
    code = amenity_codes(count=1)[0]
    query = f"amenities={code}&bedrooms_min=1&prefer_amenities={code}"

    def count():
        with CaptureQueriesContext(connection) as captured:
            body = search(query)
        return len(captured), len(body["results"])

    published(owner, amenities={code: "present"})
    one, shown = count()
    assert shown == 1
    for minutes in range(1, 10):
        published(owner, minutes, amenities={code: "present"})
    assert count() == (one, 10)
