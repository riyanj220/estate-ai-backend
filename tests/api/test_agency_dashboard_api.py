"""The Agency dashboard (Story 8.1) and Listing view statistics (Story 8.2), over HTTP."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.identity import services as identity
from apps.kernel.models import RateLimitPolicy
from apps.listings import retention, services
from apps.listings.models import ListingViewBucket, ListingViewCount, PropertyListing
from apps.messaging.models import Conversation
from tests.listings.support import (
    PASSWORD,
    SYSTEM,
    make_account,
    places,
    random_email,
    ready_draft,
    seed_cap,
    seed_media_policies,
    set_account_state,
    set_listing_visibility,
    user,
)

URL = "/api/v1/me/agency-dashboard"
LIST = f"{URL}/listings"
BROWSER = "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"
ADMIN_PASSWORD = "correct horse battery 9 staple"


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap(200)
    seed_media_policies()


def sign_in(account_type="agency", state="active"):
    """A signed-in client and its account."""
    email = random_email()
    client = Client(enforce_csrf_checks=True)
    admin = account_type == "admin"
    if admin:
        account = identity.provision_admin(SYSTEM, email, ADMIN_PASSWORD)
    else:
        account = make_account(email, account_type)
    token = client.get("/api/v1/csrf").json()["csrf_token"]
    response = client.post(
        "/api/v1/admin-session" if admin else "/api/v1/session",
        {"email": email, "password": ADMIN_PASSWORD if admin else PASSWORD},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 200, response.content
    if state != "active":
        set_account_state(account.id, state)
    return client, account


def listing(account, values=("sale", "house", "house"), status="published", created=None):
    view = ready_draft(account, values)
    set_listing_visibility(view.id, status=status)
    if created:
        PropertyListing.objects.filter(id=view.id).update(created_at=created)
    return view.id


def inquiry(listing_id, owner, buyer=None):
    return Conversation.objects.create(
        listing_id=listing_id,
        initiator_account_id=buyer or uuid.uuid4(),
        owner_account_id=owner.id,
    )


def view(listing_id, client=None, agent=BROWSER, **extra):
    client = client or Client()
    return client.post(
        f"/api/v1/public/listings/{listing_id}/views", HTTP_USER_AGENT=agent, **extra
    )


def total(listing_id) -> int:
    row = ListingViewCount.objects.filter(listing_id=listing_id).first()
    return row.views if row else 0


def test_totals_and_rows_match_the_listings_and_conversations():
    client, agency = sign_in()
    published = listing(agency)
    draft = listing(agency, status="draft")
    other = make_account(random_email(), "agency")
    foreign = listing(other)
    inquiry(published, agency)
    inquiry(published, agency)
    inquiry(draft, agency)
    inquiry(foreign, other, buyer=agency.id)  # the Agency as a buyer: not received
    ListingViewCount.objects.create(listing_id=published, views=7)
    ListingViewCount.objects.create(listing_id=foreign, views=50)

    response = client.get(URL)
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    body = response.json()
    assert body["total_listings"] == 2
    assert body["by_status"]["published"] == 1 and body["by_status"]["draft"] == 1
    assert body["inquiries_total"] == 3 and body["views_total"] == 7

    rows = {r["id"]: r for r in client.get(LIST).json()["results"]}
    assert set(rows) == {str(published), str(draft)}
    assert (rows[str(published)]["views"], rows[str(published)]["inquiries"]) == (7, 2)
    assert (rows[str(draft)]["views"], rows[str(draft)]["inquiries"]) == (0, 1)
    assert rows[str(published)]["locality"]["name"] == "Testville North"


def test_filters_combine_and_page():
    client, agency = sign_in()
    place = places()
    old = datetime(2026, 3, 1, 20, 0, tzinfo=UTC)  # 2 March in Karachi
    house = listing(agency, created=old)
    flat = listing(agency, ("sale", "apartment", "apartment"), created=old)
    listing(agency, status="draft")

    def ids(**params):
        response = client.get(LIST, params)
        assert response.status_code == 200, response.content
        return {r["id"] for r in response.json()["results"]}

    assert ids(category="apartment") == {str(flat)}
    assert ids(created_from="2026-03-02", created_to="2026-03-02") == {str(house), str(flat)}
    assert ids(created_to="2026-03-01") == set()
    combined = {
        "status": "published",
        "category": "house",
        "city_id": str(place["karachi"].id),
        "locality_id": str(place["area"].id),
        "created_to": "2026-03-02",
    }
    assert ids(**combined) == {str(house)}
    assert ids(**{**combined, "city_id": str(place["lahore"].id)}) == set()
    assert ids(status="", category="") == ids()

    first = client.get(LIST, {"page_size": 2}).json()
    assert len(first["results"]) == 2
    assert len(client.get(first["next"]).json()["results"]) == 1
    changed = client.get(first["next"] + "&status=draft")
    assert changed.status_code == 400 and "cursor" in changed.json()["error"]["fields"]


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"status": "gone"}, "status"),
        ({"category": "castle"}, "category"),
        ({"city_id": "x"}, "city_id"),
        ({"created_from": "2026-13-01"}, "created_from"),
        ({"created_from": "2026-03-02", "created_to": "2026-03-01"}, "created_to"),
        ({"cursor": "x"}, "cursor"),
    ],
)
def test_a_bad_filter_is_refused(params, field):
    client, _ = sign_in()
    response = client.get(LIST, params)
    assert response.status_code == 400 and field in response.json()["error"]["fields"]


@pytest.mark.parametrize("path", [URL, LIST])
def test_only_the_agency_account(path):
    assert Client().get(path).status_code == 401
    for account_type in ("individual", "admin"):
        client, _ = sign_in(account_type)
        assert client.get(path, {"status": "nonsense"}).status_code == 403
    suspended, _ = sign_in(state="suspended")
    assert suspended.get(path).status_code == 200


def test_a_view_counts_once_per_browser_and_window():
    agency = make_account(random_email(), "agency")
    key = listing(agency)
    response = view(key)
    assert response.status_code == 204 and not response.cookies
    assert view(key).status_code == 204
    assert total(key) == 1
    view(key, agent="Mozilla/5.0 Safari/605.1")
    view(key, REMOTE_ADDR="198.51.100.7")
    assert total(key) == 3

    bucket = ListingViewBucket.objects.first()
    assert len(bucket.key_hash) == 64 and BROWSER not in bucket.key_hash

    later = timezone.now() + services.VIEW_WINDOW + timedelta(seconds=1)
    services.record_view(user(make_account(random_email())), key, "one", now=later)
    services.record_view(user(make_account(random_email())), key, "one", now=later)
    assert total(key) == 4

    buckets = ListingViewBucket.objects.count()
    assert retention.view_buckets(timezone.now(), False) == 0
    assert retention.view_buckets(later + timedelta(hours=1), True) == buckets
    assert retention.view_buckets(later + timedelta(hours=1), False) == buckets
    assert not ListingViewBucket.objects.exists() and total(key) == 4


def test_owner_admin_and_bots_are_not_counted():
    client, agency = sign_in()
    key = listing(agency)
    token = client.get("/api/v1/csrf").json()["csrf_token"]
    assert view(key, client, HTTP_X_CSRFTOKEN=token).status_code == 204
    admin, _ = sign_in("admin")
    token = admin.get("/api/v1/csrf").json()["csrf_token"]
    assert view(key, admin, HTTP_X_CSRFTOKEN=token).status_code == 204
    for agent in ("", "Googlebot/2.1", "curl/8.5", "Mozilla/5.0 HeadlessChrome/140"):
        assert view(key, agent=agent).status_code == 204
    client.get(f"/api/v1/public/listings/{key}", HTTP_USER_AGENT=BROWSER)  # a read never counts
    assert total(key) == 0 and not ListingViewBucket.objects.exists()

    buyer, _ = sign_in("individual")
    assert view(key, buyer).status_code == 403  # signed in: the CSRF token is needed
    token = buyer.get("/api/v1/csrf").json()["csrf_token"]
    assert view(key, buyer, HTTP_X_CSRFTOKEN=token).status_code == 204
    assert total(key) == 1


def test_only_a_public_listing_is_counted_and_beacons_are_limited():
    agency = make_account(random_email(), "agency")
    draft = listing(agency, status="draft")
    for key in (draft, uuid.uuid4(), "nonsense"):
        assert view(key).status_code == 404
    RateLimitPolicy.objects.filter(scope="listing_view").update(limit=1)
    first, second = listing(agency), listing(agency)
    other = "198.51.100.9"
    assert view(first, REMOTE_ADDR=other).status_code == 204
    assert view(second, REMOTE_ADDR=other).status_code == 204
    assert (total(first), total(second)) == (1, 0)
