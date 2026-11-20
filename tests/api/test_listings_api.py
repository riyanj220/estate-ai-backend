"""Listing routes over HTTP (Story 2.1), with Django's CSRF checks on."""

import json
import uuid

import pytest
import yaml
from django.core.management import call_command
from django.test import Client

from apps.catalog import registry
from apps.identity import services as identity_services
from apps.kernel.models import AuditRecord
from apps.listings.models import ListingRevision, PropertyListing
from tests.listings.support import PASSWORD, SYSTEM, make_account, seed_cap

CREATE = "/api/v1/listings"
MINE = "/api/v1/me/listings"
SESSION = "/api/v1/session"
ADMIN_SESSION = "/api/v1/admin-session"
CSRF = "/api/v1/csrf"
BODY = {"purpose": "sale", "category": "house", "subtype": "house"}
WIRE_KEYS = {
    "id",
    "status",
    "visibility",
    "revision",
    "registry_version",
    "fields",
    "media",
    "amenities",
}


@pytest.fixture(autouse=True)
def cap(db):
    seed_cap()


def detail(listing_id):
    return f"/api/v1/listings/{listing_id}"


def post(client, url, body=BODY, token=True, **extra):
    if token:
        extra["HTTP_X_CSRFTOKEN"] = client.get(CSRF).json()["csrf_token"]
    return client.post(url, json.dumps(body), content_type="application/json", **extra)


ADMIN_PASSWORD = "correct horse battery staple 42"


def sign_in(email, admin=False):
    client = Client(enforce_csrf_checks=True)
    response = post(
        client,
        ADMIN_SESSION if admin else SESSION,
        {"email": email, "password": ADMIN_PASSWORD if admin else PASSWORD},
    )
    assert response.status_code == 200
    return client


def signed_in(email="jane.doe@example.com", account_type="individual", state="active"):
    account = make_account(email, account_type, state)
    client = sign_in(email)
    client.account_id = account.id
    return client


def code(response):
    return response.json()["error"]["code"]


# --- create ---------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["individual", "agency"])
def test_create_returns_the_wire_shape(kind):
    client = signed_in(account_type=kind)
    response = post(client, CREATE)
    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert set(body) == WIRE_KEYS
    assert body["status"] == "draft" and body["visibility"] == "private"
    assert (body["revision"], body["registry_version"]) == (1, 1)
    assert body["fields"] == BODY and body["media"] == [] and body["amenities"] == []
    uuid.UUID(body["id"])
    row = PropertyListing.objects.get()
    assert str(row.id) == body["id"] and row.owner_account_id == client.account_id
    assert ListingRevision.objects.get().revision == 1
    assert AuditRecord.objects.filter(action="listing.draft_created").count() == 1


def test_an_agency_draft_is_readable_from_a_second_session():
    owner = signed_in(account_type="agency")
    created = post(owner, CREATE).json()
    second = sign_in("jane.doe@example.com")
    assert second.get(detail(created["id"])).json() == created


def test_email_unverified_owner_may_create():
    assert post(signed_in(state="unverified"), CREATE).status_code == 201


def test_plot_file_and_subtype_mismatch():
    client = signed_in()
    ok = post(client, CREATE, {"purpose": "rent", "category": "plot", "subtype": "plot_file"})
    assert ok.status_code == 201 and ok.json()["fields"]["subtype"] == "plot_file"
    bad = post(client, CREATE, {"purpose": "sale", "category": "apartment", "subtype": "shop"})
    assert (bad.status_code, code(bad)) == (400, "validation_error")
    assert list(bad.json()["error"]["fields"]) == ["subtype"]
    mismatch = bad.json()["error"]["fields"]["subtype"]
    assert mismatch == ["This subtype does not belong to the selected category."]
    invalid_choice = post(client, CREATE, {**BODY, "subtype": "tent"}).json()["error"]["fields"]
    invalid_type = post(client, CREATE, {**BODY, "subtype": 5}).json()["error"]["fields"]
    assert mismatch not in (invalid_choice["subtype"], invalid_type["subtype"])
    assert PropertyListing.objects.count() == 1


def test_suspended_owner_may_not_create_but_may_read_and_list():
    client = signed_in()
    created = post(client, CREATE).json()
    from django.utils import timezone

    from apps.identity.models import Account

    Account.objects.filter(id=client.account_id).update(
        suspended_at=timezone.now(), suspension_reason="r"
    )
    refused = post(client, CREATE)
    assert (refused.status_code, code(refused)) == (403, "forbidden")
    assert PropertyListing.objects.count() == 1
    assert client.get(detail(created["id"])).status_code == 200
    assert len(client.get(MINE).json()["results"]) == 1


def test_a_deactivated_account_has_no_session():
    client = signed_in()
    from django.utils import timezone

    from apps.identity.models import Account

    Account.objects.filter(id=client.account_id).update(deactivated_at=timezone.now())
    response = post(client, CREATE)
    assert (response.status_code, code(response)) == (401, "unauthenticated")
    assert PropertyListing.objects.count() == 0
    read = client.get(MINE)
    assert (read.status_code, code(read)) == (401, "unauthenticated")
    assert client.get(detail(uuid.uuid4())).status_code == 401


def test_administrator_create_is_forbidden_and_reads_are_not_found():
    owner = signed_in()
    created = post(owner, CREATE).json()
    identity_services.provision_admin(SYSTEM, "root.admin@example.com", ADMIN_PASSWORD)
    admin = sign_in("root.admin@example.com", admin=True)
    refused = post(admin, CREATE)
    assert (refused.status_code, code(refused)) == (403, "forbidden")
    missing = admin.get(detail(created["id"]))
    assert (missing.status_code, code(missing)) == (404, "not_found")
    assert admin.get(MINE).status_code == 403
    assert PropertyListing.objects.count() == 1


def test_visitor_is_401_on_every_route():
    anonymous = Client(enforce_csrf_checks=True)
    for response in (
        anonymous.post(CREATE, "{}", content_type="application/json"),
        anonymous.get(detail(uuid.uuid4())),
        anonymous.get(MINE),
    ):
        assert (response.status_code, code(response)) == (401, "unauthenticated")
        assert response.headers["Cache-Control"] == "no-store"


def test_post_without_csrf_is_403_and_creates_nothing():
    client = signed_in()
    response = post(client, CREATE, token=False)
    assert (response.status_code, code(response)) == (403, "forbidden")
    assert PropertyListing.objects.count() == 0


@pytest.mark.parametrize(
    "extra",
    [
        {"owner_account_id": str(uuid.uuid4())},
        {"status": "published"},
        {"revision": 9},
        {"anything": 1},
    ],
)
def test_unknown_fields_are_refused_and_nothing_is_created(extra):
    client = signed_in()
    response = post(client, CREATE, {**BODY, **extra})
    assert (response.status_code, code(response)) == (400, "validation_error")
    assert response.json()["error"]["fields"] == {
        "non_field_errors": ["The request contains fields that are not accepted."]
    }
    assert PropertyListing.objects.count() == 0


def test_missing_and_invalid_values_are_reported_at_once_without_echo():
    client = signed_in()
    response = post(client, CREATE, {"purpose": "lease-me@example.com", "subtype": 7})
    assert (response.status_code, code(response)) == (400, "validation_error")
    fields = response.json()["error"]["fields"]
    assert set(fields) == {"purpose", "category", "subtype"}
    assert fields["purpose"] == ["Select a valid choice."]
    assert fields["category"] == ["This field is required."]
    assert fields["subtype"] == ["Enter a valid value."]
    assert "lease-me" not in response.content.decode()
    assert PropertyListing.objects.count() == 0


@pytest.mark.parametrize("body", [[], "text", 5, None])
def test_a_non_object_body_is_a_validation_error(body):
    response = post(signed_in(), CREATE, body)
    assert (response.status_code, code(response)) == (400, "validation_error")


def test_the_twentieth_plus_one_draft_is_429_in_the_envelope():
    client = signed_in()
    seed_cap(3)
    for _ in range(3):
        assert post(client, CREATE).status_code == 201
    response = post(client, CREATE)
    assert (response.status_code, code(response)) == (429, "throttled")
    assert "Retry-After" not in response.headers
    assert response.headers["Cache-Control"] == "no-store"
    assert PropertyListing.objects.count() == 3
    other = signed_in("other@example.com")
    assert post(other, CREATE).status_code == 201


# --- read one -------------------------------------------------------------------------------


def test_owner_reads_their_listing():
    client = signed_in()
    created = post(client, CREATE).json()
    response = client.get(detail(created["id"]))
    assert response.status_code == 200 and response.json() == created
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("lookup", [lambda: str(uuid.uuid4()), lambda: "not-a-uuid", lambda: "1"])
def test_unknown_and_malformed_ids_are_404(lookup):
    response = signed_in().get(detail(lookup()))
    assert (response.status_code, code(response)) == (404, "not_found")


def test_another_accounts_listing_is_404_like_an_unknown_one():
    owner = signed_in()
    created = post(owner, CREATE).json()
    other = signed_in("other@example.com")
    foreign = other.get(detail(created["id"]))
    unknown = other.get(detail(uuid.uuid4()))
    assert (foreign.status_code, code(foreign)) == (404, "not_found")
    assert foreign.json()["error"]["fields"] == unknown.json()["error"]["fields"]
    assert foreign.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert foreign.headers["Cache-Control"] == "no-store"


# --- list -----------------------------------------------------------------------------------


def test_my_listings_newest_first_with_paging_and_no_foreign_rows():
    client = signed_in()
    ids = [post(client, CREATE).json()["id"] for _ in range(3)]
    post(signed_in("other@example.com"), CREATE)
    page = client.get(MINE, {"page_size": 2})
    assert page.status_code == 200 and page.headers["Cache-Control"] == "no-store"
    body = page.json()
    assert set(body) == {"next", "previous", "results"} and body["previous"] is None
    assert [r["id"] for r in body["results"]] == [ids[2], ids[1]]
    assert all(set(r) == WIRE_KEYS for r in body["results"])
    follow = client.get(body["next"]).json()
    assert [r["id"] for r in follow["results"]] == [ids[0]] and follow["next"] is None


def test_my_listings_status_filter_and_page_size_fallback():
    client = signed_in()
    post(client, CREATE)
    assert len(client.get(MINE, {"status": "draft"}).json()["results"]) == 1
    assert len(client.get(MINE, {"page_size": "abc"}).json()["results"]) == 1
    assert len(client.get(MINE, {"page_size": "0"}).json()["results"]) == 1


def test_bad_status_and_bad_cursor_are_validation_errors():
    client = signed_in()
    post(client, CREATE)
    bad_status = client.get(MINE, {"status": "published"})
    assert (bad_status.status_code, code(bad_status)) == (400, "validation_error")
    assert list(bad_status.json()["error"]["fields"]) == ["status"]
    for cursor in ("garbage", "e30", "a" * 400):
        bad = client.get(MINE, {"cursor": cursor})
        assert (bad.status_code, code(bad)) == (400, "validation_error")
        assert list(bad.json()["error"]["fields"]) == ["cursor"]
        assert cursor not in bad.content.decode()


# --- contract and privacy -------------------------------------------------------------------


def test_no_response_carries_the_owner_account_id():
    client = signed_in()
    owner = str(client.account_id)
    created = post(client, CREATE)
    responses = [
        created,
        client.get(detail(created.json()["id"])),
        client.get(MINE),
        post(client, CREATE, {**BODY, "owner_account_id": owner}),
        post(client, CREATE, {"purpose": owner}),
        client.get(detail(owner)),
    ]
    for response in responses:
        assert owner not in response.content.decode()


def test_listing_resource_is_exactly_the_wire_shape():
    client = signed_in()
    body = post(client, CREATE).json()
    assert list(body) == [
        "id",
        "status",
        "visibility",
        "revision",
        "registry_version",
        "fields",
        "media",
        "amenities",
    ]
    assert list(body["fields"]) == ["purpose", "category", "subtype"]


def test_openapi_describes_the_routes_and_generates_enums_from_the_registry(tmp_path):
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    schema = yaml.safe_load(target.read_text())
    paths = schema["paths"]
    assert "post" in paths["/api/v1/listings"]
    assert "get" in paths["/api/v1/listings/{listing_id}"]
    assert "get" in paths["/api/v1/me/listings"]
    assert "get" in paths["/api/v1/registry"]
    enums = schema["components"]["schemas"]
    assert enums["PurposeEnum"]["enum"] == list(registry.purposes())
    assert enums["CategoryEnum"]["enum"] == [c.id for c in registry.categories()]
    assert enums["SubtypeEnum"]["enum"] == list(registry.all_subtypes())
    listing = enums["Listing"]
    assert set(listing["properties"]) == WIRE_KEYS
    assert "owner_account_id" not in listing["properties"]


def test_cursor_edge_cases_over_http():
    client = signed_in()
    ids = [post(client, CREATE).json()["id"] for _ in range(3)]
    empty = client.get(MINE + "?cursor=")
    assert empty.status_code == 200 and [r["id"] for r in empty.json()["results"]] == ids[::-1]
    first = client.get(MINE, {"page_size": 1}).json()
    other_filter = client.get(first["next"] + "&status=draft")
    assert (other_filter.status_code, code(other_filter)) == (400, "validation_error")
    assert list(other_filter.json()["error"]["fields"]) == ["cursor"]
    for size in ("9" * 5000, "٣", "1" * 4, "-1", "2.5"):
        assert client.get(MINE, {"page_size": size}).status_code == 200
    for cursor in ("e30", "A" * 500, "%00", "%ff"):
        assert client.get(MINE + "?cursor=" + cursor).status_code == 400


def test_non_canonical_ids_are_404():
    client = signed_in()
    created = post(client, CREATE).json()["id"]
    for variant in (created.upper(), created.replace("-", "")):
        assert client.get(detail(variant)).status_code == 404
