"""The public Agency profile and its Listings (Story 3.8)."""

import uuid

import pytest
from django.test import Client

from apps.identity.models import Account, AgencyProfile
from tests.api.test_public_listings_api import agency
from tests.api.test_public_search_api import published
from tests.listings.support import (
    make_account,
    random_email,
    seed_cap,
    set_account_state,
    set_listing_visibility,
)

URL = "/api/v1/public/agencies"


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap(200)


def profile_of(account):
    return AgencyProfile.objects.get(account_id=account.id)


def test_the_profile_is_name_logo_description_and_verification_only(media_root):
    account = agency(logo=True)
    profile = profile_of(account)
    AgencyProfile.objects.filter(id=profile.id).update(description="Homes in Karachi.")
    response = Client().get(f"{URL}/{profile.id}")
    assert response.status_code == 200
    assert response.json() == {
        "name": "Sunrise Estates",
        "logo_url": f"/api/v1/agencies/{profile.id}/logo",
        "description": "Homes in Karachi.",
        "verified": False,
    }
    assert response["Cache-Control"] == "public, max-age=60"
    assert not response.cookies


def test_the_listings_are_the_agencys_public_ones_with_filters_and_cursors():
    account, other = agency(), agency("Other Homes")
    first = published(account, 1, bedrooms=2, asking_price="100")
    second = published(account, 2, bedrooms=5, asking_price="900")
    hidden = published(account, 3)
    set_listing_visibility(hidden, status="draft")
    published(other, 4)
    published(make_account(random_email()), 5)
    url = f"{URL}/{profile_of(account).id}/listings"

    def get(query=""):
        response = Client().get(f"{url}?{query}")
        assert response.status_code == 200, response.content
        assert response["Cache-Control"] == "public, max-age=60"
        return response.json()

    def ids(body):
        return [card["id"] for card in body["results"]]

    assert ids(get()) == [second, first]
    assert ids(get("sort=price_asc")) == [first, second]
    assert ids(get("bedrooms_min=3")) == [second]
    assert ids(get("prefer_bedrooms_max=3")) == [first, second]
    page = get("limit=1")
    assert ids(page) == [second]
    following = Client().get(page["next"])
    assert url in page["next"]
    assert ids(following.json()) == [first]
    assert following.json()["next"] is None
    bad = Client().get(f"{url}?owner_account_id={other.id}")
    assert bad.status_code == 400
    assert bad["Cache-Control"] == "no-store"
    assert Client().get(f"{url}?cursor=nope").status_code == 400


def test_every_hidden_or_unknown_agency_is_the_same_404(media_root):
    gone = {}
    for state in ("suspended", "deactivated", "unverified"):
        account = agency(f"Agency {state}", logo=True)
        published(account)
        profile = profile_of(account)
        if state == "unverified":
            Account.objects.filter(id=account.id).update(email_verified_at=None)
        else:
            set_account_state(account.id, state)
        gone[state] = str(profile.id)
    active = agency("Still Here")
    gone |= {
        "unknown": str(uuid.uuid4()),
        "malformed": "nope",
        "upper": str(profile_of(active).id).upper(),
        "account id": str(active.id),
        "individual": str(make_account(random_email()).id),
    }
    bodies = set()
    for why, key in gone.items():
        for suffix in ("", "/listings", "/listings?nonsense=1"):
            response = Client().get(f"{URL}/{key}{suffix}")
            assert response.status_code == 404, why
            assert response["Cache-Control"] == "no-store"
            body = response.json()
            body["error"].pop("request_id", None)
            bodies.add(str(body))
        assert Client().get(f"/api/v1/agencies/{key}/logo").status_code == 404, why
    assert len(bodies) == 1
