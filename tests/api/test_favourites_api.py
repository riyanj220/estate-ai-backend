"""Favourites (Story 3.7): save, list, remove, over HTTP with CSRF checks on."""

import uuid

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.identity import services as identity
from apps.kernel.errors import DomainError
from apps.kernel.models import RateLimitPolicy
from apps.listings import services
from apps.listings.models import PropertyListing, SavedListing
from tests.listings.support import (
    PASSWORD,
    SYSTEM,
    make_account,
    random_email,
    ready_draft,
    seed_cap,
    seed_media_policies,
    set_account_state,
    set_listing_visibility,
    user,
)

URL = "/api/v1/me/favourites"
ADMIN_PASSWORD = "correct horse battery 9 staple"


def seed_favourites_cap(limit=500):
    RateLimitPolicy.objects.update_or_create(
        scope="favourites",
        defaults={"limit": limit, "window_seconds": None, "key_kind": "account", "note": "t"},
    )


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap(200)
    seed_media_policies()
    seed_favourites_cap()


@pytest.fixture
def owner():
    return make_account(random_email())


def published(owner):
    view = ready_draft(owner, images=1)
    set_listing_visibility(view.id)
    return str(view.id)


class Api:
    def __init__(self, account_type="individual", state="active", email=None):
        email = email or random_email()
        admin = account_type == "admin"
        if admin:
            self.account = identity.provision_admin(SYSTEM, email, ADMIN_PASSWORD)
        else:
            self.account = make_account(email, account_type)
        self.client = Client(enforce_csrf_checks=True)
        response = self.client.post(
            "/api/v1/admin-session" if admin else "/api/v1/session",
            {"email": email, "password": ADMIN_PASSWORD if admin else PASSWORD},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=self.token(),
        )
        assert response.status_code == 200, response.content
        if state != "active":
            set_account_state(self.account.id, state)

    def token(self):
        return self.client.get("/api/v1/csrf").json()["csrf_token"]

    def get(self, path=URL):
        return self.client.get(path)

    def save(self, listing_id):
        return self.client.put(f"{URL}/{listing_id}", HTTP_X_CSRFTOKEN=self.token())

    def remove(self, listing_id):
        return self.client.delete(f"{URL}/{listing_id}", HTTP_X_CSRFTOKEN=self.token())

    def ids(self):
        return self.get(f"{URL}/ids").json()["listing_ids"]


def code(response):
    return response.json()["error"]["code"]


@pytest.mark.parametrize("account_type", ["individual", "agency"])
def test_save_list_and_remove(owner, account_type):
    listing, buyer = published(owner), Api(account_type)
    assert buyer.save(listing).status_code == 204
    assert buyer.save(listing).status_code == 204  # idempotent
    assert SavedListing.objects.count() == 1

    response = buyer.get()
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    [item] = response.json()["results"]
    assert item["listing_id"] == listing and item["listing"]["id"] == listing
    assert buyer.ids() == [listing]

    before = PropertyListing.objects.values().get(id=listing)
    assert buyer.remove(listing).status_code == 204
    assert PropertyListing.objects.values().get(id=listing) == before
    assert buyer.get().json() == {"results": [], "next": None}
    assert code(buyer.remove(listing)) == "not_found"


def test_only_a_public_listing_can_be_newly_saved(owner):
    draft = str(ready_draft(owner, images=1).id)
    restricted = published(owner)
    set_listing_visibility(restricted, restricted=True)
    buyer = Api()
    for listing in (draft, restricted, str(uuid.uuid4()), "nonsense"):
        response = buyer.save(listing)
        assert (response.status_code, code(response)) == (404, "not_found")
    assert not SavedListing.objects.exists()


@pytest.mark.parametrize("change", ["archived", "restricted", "owner_suspended"])
def test_a_listing_that_stops_being_public_has_no_details(owner, change):
    listing, buyer = published(owner), Api()
    assert buyer.save(listing).status_code == 204
    if change == "owner_suspended":
        set_account_state(owner.id, "suspended")
    else:
        status = "archived" if change == "archived" else "published"
        set_listing_visibility(listing, status=status, restricted=change == "restricted")
    [item] = buyer.get().json()["results"]
    assert set(item) == {"listing_id", "saved_at", "listing"}
    assert item["listing"] is None
    assert buyer.save(listing).status_code == 204  # already saved: still nothing disclosed
    assert buyer.remove(listing).status_code == 204


def test_another_account_neither_sees_nor_removes_them(owner):
    listing, buyer, other = published(owner), Api(), Api()
    buyer.save(listing)
    assert other.get().json()["results"] == [] and other.ids() == []
    assert code(other.remove(listing)) == "not_found"
    assert buyer.ids() == [listing]


def test_who_may_keep_favourites(owner):
    listing = published(owner)
    visitor = Client()
    for response in (visitor.get(URL), visitor.get(f"{URL}/ids"), visitor.put(f"{URL}/{listing}")):
        assert (response.status_code, code(response)) == (401, "unauthenticated")

    unverified = Api(state="unverified")
    assert unverified.save(listing).status_code == 204

    admin = Api("admin")
    for response in (admin.get(), admin.get(f"{URL}/ids"), admin.save(listing)):
        assert (response.status_code, code(response)) == (403, "forbidden")
    assert code(admin.remove(listing)) == "forbidden"

    suspended = Api()
    suspended.save(listing)
    set_account_state(suspended.account.id, "suspended")
    assert code(suspended.save(published(owner))) == "forbidden"
    assert suspended.ids() == [listing]  # view and remove stay (FR-74)
    assert suspended.remove(listing).status_code == 204


def test_a_deactivated_account_is_refused_by_the_service(owner):
    gone = make_account(random_email(), state="deactivated")
    with pytest.raises(DomainError) as refused:
        services.save_favourite(user(gone), published(owner))
    assert refused.value.code == "forbidden"


def test_changes_need_a_csrf_token(owner):
    listing, buyer = published(owner), Api()
    assert code(buyer.client.put(f"{URL}/{listing}")) == "forbidden"
    buyer.save(listing)
    assert code(buyer.client.delete(f"{URL}/{listing}")) == "forbidden"
    assert buyer.ids() == [listing]


def test_the_cap_is_429(owner):
    seed_favourites_cap(1)
    first, second, buyer = published(owner), published(owner), Api()
    assert buyer.save(first).status_code == 204
    response = buyer.save(second)
    assert (response.status_code, code(response)) == (429, "throttled")
    assert "Retry-After" not in response
    assert buyer.save(first).status_code == 204  # a saved one is not a new one
    buyer.remove(first)
    assert buyer.save(second).status_code == 204


def test_pages_and_a_bad_cursor(owner):
    buyer = Api()
    listings = [published(owner) for _ in range(3)]
    for listing in listings:
        buyer.save(listing)
    first = buyer.get(f"{URL}?page_size=2").json()
    second = buyer.get(first["next"]).json()
    seen = [item["listing_id"] for item in first["results"] + second["results"]]
    assert seen == listings[::-1] and second["next"] is None
    response = buyer.get(f"{URL}?cursor=nonsense")
    assert response.status_code == 400 and "cursor" in response.json()["error"]["fields"]


def test_the_list_costs_the_same_queries_whatever_its_size(owner):
    buyer = Api()

    def queries():
        with CaptureQueriesContext(connection) as captured:
            assert buyer.get().status_code == 200
        return len(captured)

    buyer.save(published(owner))
    queries()  # the session is touched on a first request only
    one = queries()
    for _ in range(3):
        buyer.save(published(owner))
    assert queries() == one


def test_deleting_a_listing_takes_its_favourites(owner):
    listing, buyer = published(owner), Api()
    buyer.save(listing)
    set_listing_visibility(listing, status="draft")
    services.delete_draft(user(owner), listing)
    assert not SavedListing.objects.exists()
