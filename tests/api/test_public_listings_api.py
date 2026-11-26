"""The public browse and detail endpoints (Story 3.1)."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.catalog import selectors as catalog
from apps.identity.models import Account, IndividualProfile
from apps.kernel.models import MediaAsset
from apps.listings.models import ListingImage, PropertyListing
from tests.listings.support import (
    STREET,
    make_account,
    random_email,
    ready_draft,
    seed_cap,
    set_account_state,
    set_first_published,
    set_listing_visibility,
)

URL = "/api/v1/public/listings"
START = datetime(2026, 5, 1, 12, tzinfo=UTC)
PRIVATE = {
    "street": STREET,
    "house_number": "H-CANARY-77",
    "latitude": "24.861234",
    "longitude": "67.009876",
}


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap(200)


@pytest.fixture
def owner():
    return make_account(random_email())


def published(owner, minutes=0, **extra):
    """A public Listing first published `minutes` after START."""
    view = ready_draft(owner, extra=extra, images=2)
    set_listing_visibility(view.id)
    set_first_published(view.id, START + timedelta(minutes=minutes))
    return str(view.id)


def browse(query=""):
    response = Client().get(URL + query)
    assert response.status_code == 200, response.content
    return response.json()


def ids(body):
    return [card["id"] for card in body["results"]]


def assert_missing(listing_id):
    response = Client().get(f"{URL}/{listing_id}")
    assert response.status_code == 404
    assert response["Cache-Control"] == "no-store"
    error = response.json()["error"]
    return {**error, "request_id": ""}


def assert_cacheable(response):
    assert response["Cache-Control"] == "public, max-age=60"
    assert "cookie" not in response.get("Vary", "").lower()
    assert "Set-Cookie" not in response and not response.cookies


def test_an_empty_marketplace_is_an_empty_list():
    assert browse() == {"results": [], "next": None}


def test_card_and_detail_shapes(owner):
    amenity = catalog.list_amenities()[0].code
    listing = published(owner, amenities={amenity: "present"}, description="A quiet home.")
    cover = ListingImage.objects.get(listing_id=listing, is_cover=True)
    response = Client().get(URL)
    assert_cacheable(response)
    card = response.json()["results"][0]
    assert set(card) == {
        "id", "purpose", "category", "subtype", "title", "fields", "city", "locality",
        "cover_url", "published_on",
    }  # fmt: skip
    assert card["fields"]["asking_price"] == "1500000.00" and card["fields"]["bedrooms"] == 3
    assert card["city"]["name"] == "Karachi" and card["locality"]["name"] == "Testville North"
    assert card["cover_url"] == f"/api/v1/listings/{listing}/images/{cover.id}/file"
    assert card["published_on"] == "2026-05-01"

    response = Client().get(f"{URL}/{listing}")
    assert response.status_code == 200
    assert_cacheable(response)
    detail = response.json()
    assert set(detail) == {
        "id", "purpose", "category", "subtype", "title", "description", "fields", "media",
        "registry_version", "city", "locality", "published_on",
    }  # fmt: skip
    assert detail["description"] == "A quiet home."
    assert detail["fields"]["amenities"] == {amenity: "present"}
    assert len(detail["media"]) == 2 and detail["city"] == card["city"]


def test_the_answer_is_the_same_whatever_cookies_arrive(owner):
    listing = published(owner)
    for path in (URL, f"{URL}/{listing}"):
        client = Client(enforce_csrf_checks=True)
        client.cookies["csrftoken"] = "malformed"
        client.cookies["sessionid"] = "stale"
        response = client.get(path)
        assert response.status_code == 200
        assert_cacheable(response)


def test_no_private_value_reaches_a_public_answer(owner):
    listing = published(owner, **PRIVATE)
    IndividualProfile.objects.update_or_create(
        account_id=owner.id, defaults={"full_name": "NAME-CANARY", "phone": "+923001234567"}
    )
    PropertyListing.objects.filter(id=listing).update(restriction_reason="REASON-CANARY")
    text = Client().get(URL).content.decode() + Client().get(f"{URL}/{listing}").content.decode()
    assert listing in text
    secrets = [*PRIVATE.values(), "REASON-CANARY", str(owner.id)]
    secrets += ["3001234567", "NAME-CANARY", Account.objects.get(id=owner.id).email]
    for asset in MediaAsset.objects.filter(owner_account_id=owner.id):
        secrets += [str(asset.id), asset.storage_key]
    for secret in secrets:
        assert secret not in text
    names = json.dumps(Client().get(f"{URL}/{listing}").json())
    for name in (*PRIVATE, "owner", "status", "revision", "provenance", "restrict", "phone"):
        assert f'"{name}' not in names


@pytest.mark.parametrize("status", ["draft", "archived", "sold", "rented", "removed"])
def test_other_statuses_never_appear(owner, status):
    listing = published(owner)
    set_listing_visibility(listing, status=status)
    assert ids(browse()) == []
    assert_missing(listing)


@pytest.mark.parametrize("state", ["unverified", "suspended", "deactivated"])
def test_a_listing_of_an_owner_who_is_not_active_never_appears(owner, state):
    listing = published(owner)
    set_account_state(owner.id, state)
    assert ids(browse()) == []
    assert_missing(listing)


def test_restricted_unknown_and_malformed_ids_are_one_404(owner):
    listing = published(owner)
    set_listing_visibility(listing, restricted=True)
    assert ids(browse()) == []
    answers = [assert_missing(i) for i in (listing, uuid.uuid4(), "nope", listing.upper())]
    assert all(answer == answers[0] for answer in answers)


def test_newest_first_publication(owner):
    # that republishing does not bump a Listing: tests/listings/test_republication.py
    old, new = published(owner, 1), published(owner, 2)
    assert ids(browse()) == [new, old]
    assert ids(browse("?sort=newest")) == [new, old]


def test_pagination_is_stable_with_ties_and_new_arrivals(owner):
    # three share one publication time, so the id tiebreak decides
    made = [published(owner, minutes) for minutes in (1, 2, 2, 2, 3)]
    expected = [made[4], *sorted(made[1:4], reverse=True), made[0]]
    body = browse("?limit=2")
    seen = ids(body)
    published(owner, 10)  # arrives while paging: must not shift or repeat anything
    while body["next"]:
        body = browse("?" + body["next"].split("?", 1)[1])
        seen += ids(body)
    assert seen == expected


@pytest.mark.parametrize(
    ("query", "name"),
    [
        ("limit=0", "limit"),
        ("limit=51", "limit"),
        ("limit=x", "limit"),
        ("limit=", "limit"),
        ("cursor=garbage", "cursor"),
        ("cursor=%21", "cursor"),
        ("sort=price", "sort"),
    ],
)
def test_bad_parameters_are_400(query, name):
    response = Client().get(f"{URL}?{query}")
    assert response.status_code == 400
    assert response["Cache-Control"] == "no-store"
    assert name in response.json()["error"]["fields"]


def test_the_query_count_does_not_grow_with_the_page(owner):
    amenity = catalog.list_amenities()[0].code
    published(owner, amenities={amenity: "present"})

    def count():
        with CaptureQueriesContext(connection) as captured:
            body = browse()
        return len(captured), len(body["results"])

    one, shown = count()
    assert shown == 1
    for minutes in range(1, 20):
        published(owner, minutes, amenities={amenity: "present"})
    assert count() == (one, 20)
