"""Listing routes over HTTP (Story 2.1), with Django's CSRF checks on."""

import json
import uuid
from decimal import Decimal

import pytest
import yaml
from django.core.management import call_command
from django.test import Client

from apps.catalog import registry
from apps.identity import services as identity_services
from apps.kernel.errors import field_message
from apps.kernel.models import AuditRecord
from apps.listings.models import (
    ListingArea,
    ListingCommercial,
    ListingPlot,
    ListingResidential,
    ListingRevision,
    ListingTerms,
    PropertyListing,
)
from tests.listings.support import (
    DETAIL_IDS,
    PASSWORD,
    RENT_TERMS,
    SYSTEM,
    TAIL_IDS,
    TERMS_IDS,
    make_account,
    seed_cap,
)

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
    "provenance",
    "media",
    "restricted",
    "restriction_reason",
}
NEW_FIELDS = {
    "title": None,
    "description": None,
    "asking_price": None,
    "price_basis": None,
    "rental_period": None,
    "area_value": None,
    "area_unit": None,
    "marla_basis": None,
    "area_sqft": None,
    **dict.fromkeys(TAIL_IDS),
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
    assert (body["revision"], body["registry_version"]) == (1, 10)
    assert body["fields"] == {**BODY, **NEW_FIELDS} and body["provenance"] == {}
    assert body["media"] == [] and body["fields"]["amenities"] is None
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
    bad_status = client.get(MINE, {"status": "pending"})
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
        "provenance",
        "media",
        "restricted",
        "restriction_reason",
    ]
    assert list(body["fields"]) == [
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
        *TAIL_IDS,
        "area_sqft",
    ]


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


# --- edit: PATCH /api/v1/listings/{id} (Story 2.2) -------------------------------------------

TITLE = "Family house near the park"
SAVE = {"title": TITLE, "description": "Three bedrooms.", "asking_price": "1500000"}


def patch(client, listing_id, body, token=True, **extra):
    if token:
        extra["HTTP_X_CSRFTOKEN"] = client.get(CSRF).json()["csrf_token"]
    return client.patch(
        detail(listing_id), json.dumps(body), content_type="application/json", **extra
    )


def edit(client, listing, fields, base=None):
    base = listing["revision"] if base is None else base
    return patch(client, listing["id"], {"base_revision": base, "fields": fields})


def draft(client, body=BODY):
    return post(client, CREATE, body).json()


def body_text(response) -> str:
    """The whole body as text without the volatile request id, so an echoed value anywhere else
    (message, fields) is found and a random id cannot collide with a needle."""
    body = response.json()
    if isinstance(body.get("error"), dict):
        body["error"].pop("request_id", None)
    return json.dumps(body, ensure_ascii=False)


def field_codes(response):
    return response.json()["error"]["fields"]


def test_patch_saves_and_returns_the_listing_resource():
    client = signed_in()
    created = draft(client)
    response = edit(client, created, SAVE)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert list(body) == list(created) and set(body) == WIRE_KEYS
    assert body["revision"] == 2 and body["id"] == created["id"]
    assert body["fields"] == {
        **BODY,
        **NEW_FIELDS,
        "title": TITLE,
        "description": "Three bedrooms.",
        "asking_price": "1500000.00",
    }
    assert body["provenance"] == {name: {"origin": "manual", "confirmed": True} for name in SAVE}
    assert client.get(detail(created["id"])).json() == body
    revision = ListingRevision.objects.get(revision=2)
    assert revision.origin == "manual" and revision.actor_account_id == client.account_id
    assert revision.changes == {**SAVE, "asking_price": "1500000.00"}


def test_the_own_list_carries_the_edited_fields_in_the_same_shape():
    client = signed_in()
    created = draft(client)
    saved = edit(client, created, SAVE).json()
    item = client.get(MINE).json()["results"][0]
    assert item == saved and set(item) == WIRE_KEYS


def test_partial_clear_and_unchanged_saves():
    client = signed_in()
    created = draft(client)
    first = edit(client, created, {"title": TITLE}).json()
    assert first["revision"] == 2 and first["fields"]["asking_price"] is None
    same = edit(client, first, {"title": TITLE})
    assert same.status_code == 200 and same.json() == first
    assert ListingRevision.objects.count() == 2
    cleared = edit(client, first, {"title": None}).json()
    assert cleared["revision"] == 3 and cleared["fields"]["title"] is None
    assert cleared["provenance"] == {}


def test_rent_terms_and_the_purpose_rules():
    client = signed_in()
    rent = draft(client, {**BODY, "purpose": "rent"})
    ok = edit(
        client,
        rent,
        {"asking_price": "85000.50", "price_basis": "rental_period", "rental_period": "month"},
    )
    assert ok.status_code == 200 and ok.json()["fields"]["asking_price"] == "85000.50"
    assert ok.json()["fields"]["rental_period"] == "month"
    sale = draft(client)
    bad = edit(client, sale, {"rental_period": "month"})
    assert (bad.status_code, code(bad)) == (400, "validation_error")
    assert field_codes(bad) == {"rental_period": ["This field does not apply to this listing."]}
    mismatch = edit(client, sale, {"price_basis": "rental_period"})
    assert field_codes(mismatch) == {"price_basis": ["Select a valid choice."]}
    assert field_codes(edit(client, ok.json(), {"price_basis": "total"})) == {
        "price_basis": ["Select a valid choice."]
    }
    # saving never needs complete data: a basis without its period, or a period alone, saves
    cleared = edit(client, ok.json(), {"rental_period": None})
    assert cleared.status_code == 200 and cleared.json()["fields"]["rental_period"] is None
    other = draft(client, {**BODY, "purpose": "rent"})
    assert edit(client, other, {"price_basis": "rental_period"}).status_code == 200
    third = draft(client, {**BODY, "purpose": "rent"})
    assert edit(client, third, {"rental_period": "week"}).status_code == 200
    assert PropertyListing.objects.get(id=sale["id"]).revision == 1


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("0", "Enter an amount within the allowed range with at most two decimals."),
        ("-5", "Enter an amount within the allowed range with at most two decimals."),
        ("1.234", "Enter an amount within the allowed range with at most two decimals."),
        ("1000000000000", "Enter an amount within the allowed range with at most two decimals."),
        ("12abc", "Enter a valid value."),
    ],
)
def test_money_errors(value, message):
    client = signed_in()
    created = draft(client)
    response = edit(client, created, {"asking_price": value})
    assert (response.status_code, code(response)) == (400, "validation_error")
    assert field_codes(response) == {"asking_price": [message]}
    assert len(value) < 3 or value not in body_text(response)


def test_a_json_number_for_money_is_refused_as_a_float():
    client = signed_in()
    created = draft(client)
    raw = '{"base_revision": 1, "fields": {"asking_price": 1500000.5}}'
    response = client.patch(
        detail(created["id"]),
        raw,
        content_type="application/json",
        HTTP_X_CSRFTOKEN=client.get(CSRF).json()["csrf_token"],
    )
    assert field_codes(response) == {"asking_price": ["Enter a valid value."]}
    assert ListingTerms.objects.get().asking_price is None


def test_text_errors_and_the_contact_detail_explanation_without_echo(caplog):
    client = signed_in()
    created = draft(client)
    phone = "Call 0300 1234567 now please"
    response = edit(client, created, {"title": phone, "description": "x" * 5001})
    assert (response.status_code, code(response)) == (400, "validation_error")
    assert field_codes(response) == {
        "title": ["This text cannot contain phone numbers, email addresses or street addresses."],
        "description": ["This text is too long."],
    }
    assert "0300" not in body_text(response)
    assert "0300" not in caplog.text
    assert field_codes(edit(client, created, {"title": "short"})) == {
        "title": ["This text is too short."]
    }
    assert field_codes(edit(client, created, {"title": "Nice\x00 family home"})) == {
        "title": ["Enter a valid value."]
    }
    assert PropertyListing.objects.get().revision == 1


def test_unknown_and_non_editable_fields_are_refused_without_echo():
    client = signed_in()
    created = draft(client)
    response = edit(
        client,
        created,
        {"purpose": "rent", "status": "published", "owner_account_id": "x", "title": TITLE},
    )
    assert (response.status_code, code(response)) == (400, "validation_error")
    assert field_codes(response) == {
        "purpose": ["This field cannot be changed here."],
        "non_field_errors": ["The request contains fields that are not accepted."],
    }
    assert "owner_account_id" not in response.content.decode()
    row = PropertyListing.objects.get()
    assert (row.purpose, row.title, row.revision) == ("sale", None, 1)


@pytest.mark.parametrize(
    ("body", "fields"),
    [
        ({"fields": {"title": TITLE}}, {"base_revision": ["This field is required."]}),
        ({"base_revision": 1}, {"fields": ["This field is required."]}),
        (
            {"base_revision": "1", "fields": {"title": TITLE}},
            {"base_revision": ["Enter a valid value."]},
        ),
        (
            {"base_revision": 0, "fields": {"title": TITLE}},
            {"base_revision": ["Enter a valid value."]},
        ),
        (
            {"base_revision": 1.5, "fields": {"title": TITLE}},
            {"base_revision": ["Enter a valid value."]},
        ),
        (
            {"base_revision": True, "fields": {"title": TITLE}},
            {"base_revision": ["Enter a valid value."]},
        ),
        ({"base_revision": 1, "fields": {}}, {"fields": ["This field may not be empty."]}),
        ({"base_revision": 1, "fields": [TITLE]}, {"fields": ["Expected an object."]}),
        ({"base_revision": 1, "fields": TITLE}, {"fields": ["Expected an object."]}),
        ({"base_revision": 1, "fields": None}, {"fields": ["This field is required."]}),
    ],
)
def test_bad_bodies_are_validation_errors(body, fields):
    client = signed_in()
    created = draft(client)
    response = patch(client, created["id"], body)
    assert (response.status_code, code(response)) == (400, "validation_error")
    assert field_codes(response) == fields
    assert PropertyListing.objects.get().revision == 1


@pytest.mark.parametrize(
    "extra", [{"origin": "ai"}, {"confirmed": True}, {"owner_account_id": "x"}, {"revision": 3}]
)
def test_an_extra_top_level_key_is_refused_and_origin_is_never_client_input(extra):
    client = signed_in()
    created = draft(client)
    body = {"base_revision": 1, "fields": {"title": TITLE}, **extra}
    response = patch(client, created["id"], body)
    assert (response.status_code, code(response)) == (400, "validation_error")
    assert field_codes(response) == {
        "non_field_errors": ["The request contains fields that are not accepted."]
    }
    assert PropertyListing.objects.get().title is None
    inside = edit(client, created, {"title": TITLE, **extra})
    assert inside.status_code == 400 and PropertyListing.objects.get().title is None


@pytest.mark.parametrize("body", [[], "text", 5, None])
def test_a_non_object_patch_body_is_a_validation_error(body):
    client = signed_in()
    created = draft(client)
    response = patch(client, created["id"], body)
    assert (response.status_code, code(response)) == (400, "validation_error")


def test_the_origin_is_fixed_to_manual_by_the_api():
    client = signed_in()
    created = draft(client)
    edit(client, created, {"title": TITLE})
    assert ListingRevision.objects.get(revision=2).origin == "manual"
    row = PropertyListing.objects.get()
    assert row.provenance == {"title": {"origin": "manual", "confirmed": True}}


# --- edit: conflicts -----------------------------------------------------------------------------


def test_a_stale_revision_is_409_with_the_current_listing():
    client = signed_in()
    created = draft(client)
    saved = edit(client, created, {"title": TITLE}).json()
    response = edit(client, created, {"title": "Someone else's title"}, base=created["revision"])
    assert (response.status_code, code(response)) == (409, "stale_revision")
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert list(body) == ["error", "current"]
    assert set(body["error"]) == {"code", "message", "fields", "request_id"}
    assert body["error"]["message"] == "The resource changed since it was loaded."
    assert body["error"]["fields"] == {} and body["error"]["request_id"]
    assert body["current"] == saved and set(body["current"]) == WIRE_KEYS
    assert PropertyListing.objects.get().title == TITLE and ListingRevision.objects.count() == 2


def test_replaying_a_successful_patch_is_409_never_a_double_write():
    client = signed_in()
    created = draft(client)
    first = edit(client, created, {"asking_price": "100"})
    second = edit(client, created, {"asking_price": "100"})
    assert (first.status_code, second.status_code) == (200, 409)
    assert second.json()["current"] == first.json()
    assert ListingRevision.objects.count() == 2


def test_a_second_session_of_the_same_account_gets_the_conflict():
    first = signed_in()
    created = draft(first)
    second = sign_in("jane.doe@example.com")
    assert edit(first, created, {"title": TITLE}).status_code == 200
    stale = edit(second, created, {"title": "Another title here"})
    assert (stale.status_code, code(stale)) == (409, "stale_revision")
    assert stale.json()["current"]["fields"]["title"] == TITLE


def test_the_conflict_comes_before_value_validation_and_leaks_nothing_foreign():
    client = signed_in()
    created = draft(client)
    edit(client, created, {"title": TITLE})
    response = edit(client, created, {"title": "x", "asking_price": "-1"}, base=1)
    assert (response.status_code, code(response)) == (409, "stale_revision")
    assert str(client.account_id) not in response.content.decode()


# --- edit: who may -------------------------------------------------------------------------------


def test_another_account_and_unknown_ids_are_404():
    owner = signed_in()
    created = draft(owner)
    other = signed_in("other@example.com")
    body = {"base_revision": 1, "fields": {"title": TITLE}}
    foreign = patch(other, created["id"], body)
    unknown = patch(other, str(uuid.uuid4()), body)
    assert (foreign.status_code, code(foreign)) == (404, "not_found")
    assert foreign.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert foreign.json()["error"]["fields"] == unknown.json()["error"]["fields"]
    for lookup in ("not-a-uuid", created["id"].upper(), created["id"].replace("-", "")):
        assert patch(owner, lookup, body).status_code == 404
    stale = patch(other, created["id"], {"base_revision": 9, "fields": {"title": TITLE}})
    assert stale.status_code == 404  # no conflict body for a foreigner
    assert PropertyListing.objects.get().title is None


def test_an_administrator_gets_404_and_a_visitor_401():
    owner = signed_in()
    created = draft(owner)
    identity_services.provision_admin(SYSTEM, "root.admin@example.com", ADMIN_PASSWORD)
    admin = sign_in("root.admin@example.com", admin=True)
    body = {"base_revision": 1, "fields": {"title": TITLE}}
    assert patch(admin, created["id"], body).status_code == 404
    anonymous = Client(enforce_csrf_checks=True)
    response = anonymous.patch(
        detail(created["id"]), json.dumps(body), content_type="application/json"
    )
    assert (response.status_code, code(response)) == (401, "unauthenticated")
    assert response.headers["Cache-Control"] == "no-store"
    assert PropertyListing.objects.get().title is None


def test_a_suspended_owner_is_403_and_nothing_is_written():
    client = signed_in()
    created = draft(client)
    from django.utils import timezone

    from apps.identity.models import Account

    Account.objects.filter(id=client.account_id).update(
        suspended_at=timezone.now(), suspension_reason="r"
    )
    response = edit(client, created, {"title": TITLE})
    assert (response.status_code, code(response)) == (403, "forbidden")
    assert PropertyListing.objects.get().title is None and ListingRevision.objects.count() == 1


def test_editing_with_a_deactivated_account_has_no_session():
    client = signed_in()
    created = draft(client)
    from django.utils import timezone

    from apps.identity.models import Account

    Account.objects.filter(id=client.account_id).update(deactivated_at=timezone.now())
    response = edit(client, created, {"title": TITLE})
    assert (response.status_code, code(response)) == (401, "unauthenticated")


def test_patch_without_csrf_is_403_and_writes_nothing():
    client = signed_in()
    created = draft(client)
    response = patch(
        client, created["id"], {"base_revision": 1, "fields": {"title": TITLE}}, token=False
    )
    assert (response.status_code, code(response)) == (403, "forbidden")
    assert PropertyListing.objects.get().title is None


def test_an_email_unverified_owner_may_edit():
    client = signed_in(state="unverified")
    created = draft(client)
    assert edit(client, created, {"title": TITLE}).status_code == 200


# --- edit: privacy and the contract --------------------------------------------------------------


def test_no_edit_response_carries_the_owner_account_id():
    client = signed_in()
    owner = str(client.account_id)
    created = draft(client)
    responses = [
        edit(client, created, SAVE),
        edit(client, created, SAVE),  # 409
        edit(client, created, {"title": "x"}, base=2),
        edit(client, created, {"owner_account_id": owner}, base=2),
        patch(client, owner, {"base_revision": 1, "fields": {"title": TITLE}}),
    ]
    assert [r.status_code for r in responses] == [200, 409, 400, 400, 404]
    for response in responses:
        assert owner not in response.content.decode()


def test_openapi_describes_the_patch_route_and_the_409_with_current(tmp_path):
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    schema = yaml.safe_load(target.read_text())
    operation = schema["paths"]["/api/v1/listings/{listing_id}"]["patch"]
    assert operation["operationId"] == "update_listing"
    request = operation["requestBody"]["content"]["application/json"]["schema"]
    assert request == {"$ref": "#/components/schemas/ListingUpdateRequest"}
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Listing"
    }
    assert operation["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ListingConflict"
    }
    for status in ("400", "401", "403", "404", "429", "500"):
        assert status in operation["responses"]
    schemas = schema["components"]["schemas"]
    conflict = schemas["ListingConflict"]
    assert set(conflict["properties"]) == {"error", "current"}
    assert set(conflict["required"]) == {"error", "current"}
    assert conflict["properties"]["current"]["allOf"] == [{"$ref": "#/components/schemas/Listing"}]
    update = schemas["ListingUpdateRequest"]
    assert set(update["properties"]) == {"base_revision", "fields"}
    assert set(update["required"]) == {"base_revision", "fields"}
    assert set(schemas["ListingFieldsUpdateRequest"]["properties"]) == {
        "title",
        "description",
        "asking_price",
        "price_basis",
        "rental_period",
        "area_value",
        "area_unit",
        "marla_basis",
        *TAIL_IDS,
    }
    fields = schemas["ListingFields"]["properties"]
    assert set(fields) == {"purpose", "category", "subtype", *NEW_FIELDS}
    assert "provenance" in schemas["Listing"]["properties"]
    assert schemas["PriceBasisEnum"]["enum"] == list(registry.field("price_basis").allowed_values)
    assert schemas["RentalPeriodEnum"]["enum"] == list(
        registry.field("rental_period").allowed_values
    )
    assert schemas["AreaUnitEnum"]["enum"] == list(registry.field("area_unit").allowed_values)
    assert schemas["MarlaBasisEnum"]["enum"] == ["225", "250", "272.25"]
    assert "area_sqft" not in schemas["ListingFieldsUpdateRequest"]["properties"]
    assert "owner_account_id" not in schemas["Listing"]["properties"]
    assert set(schemas["ProvenanceEntry"]["properties"]) == {"origin", "confirmed"}


# --- review additions ----------------------------------------------------------------------------


def test_the_409_carries_the_request_id_no_store_and_is_logged(caplog):
    import logging

    client = signed_in()
    created = draft(client)
    edit(client, created, {"title": TITLE})
    with caplog.at_level(logging.INFO):
        response = edit(client, created, {"title": "Another title entirely"})
    assert response.status_code == 409
    assert response.headers["Cache-Control"] == "no-store"
    request_id = response.headers["X-Request-ID"]
    assert response.json()["error"]["request_id"] == request_id
    logged = [r for r in caplog.records if getattr(r, "status", None) == 409]
    assert logged and all(getattr(r, "request_id", request_id) == request_id for r in logged)


def test_a_stale_base_with_a_bad_shape_gets_the_shape_error_first():
    client = signed_in()
    created = draft(client)
    saved = edit(client, created, {"title": TITLE}).json()
    stale = saved["revision"] - 1
    not_editable = edit(client, saved, {"purpose": "rent"}, base=stale)
    assert (not_editable.status_code, code(not_editable)) == (400, "validation_error")
    assert "current" not in not_editable.json()
    unknown = edit(client, saved, {"owner_account_id": "x"}, base=stale)
    assert (unknown.status_code, code(unknown)) == (400, "validation_error")
    bad_base = patch(client, saved["id"], {"base_revision": "1", "fields": {"title": TITLE}})
    assert (bad_base.status_code, code(bad_base)) == (400, "validation_error")
    well_formed = edit(client, saved, {"title": "Another title entirely"}, base=stale)
    assert (well_formed.status_code, code(well_formed)) == (409, "stale_revision")


@pytest.mark.parametrize(
    "value",
    [
        "Call 0300 1234567 now please",
        "Write to canary.person@example.com please",
        "Find House 12, Street 5, Gulberg",
        "Ring 0300,1234567 any time",
    ],
)
@pytest.mark.parametrize("name", ["title", "description"])
def test_contact_values_are_never_echoed_or_logged(name, value, caplog):
    import logging

    client = signed_in()
    created = draft(client)
    with caplog.at_level(logging.DEBUG):
        response = edit(client, created, {name: value})
    assert response.status_code == 400
    assert field_codes(response) == {
        name: ["This text cannot contain phone numbers, email addresses or street addresses."]
    }
    probes = [value, *value.split()[1:3]]
    for probe in probes:
        assert probe not in response.content.decode() and probe not in caplog.text
    assert PropertyListing.objects.get().revision == 1


@pytest.mark.parametrize("value", ["0", "-5", "12.345", "abc"])
def test_money_values_are_never_echoed_or_logged(value, caplog):
    import logging

    client = signed_in()
    created = draft(client)
    with caplog.at_level(logging.DEBUG):
        response = edit(client, created, {"asking_price": value})
    assert response.status_code == 400
    assert list(field_codes(response)) == ["asking_price"]
    assert value not in json.dumps(field_codes(response))
    assert f'"{value}"' not in response.content.decode() and f'"{value}"' not in caplog.text
    assert f"asking_price={value}" not in caplog.text


# --- area and units (Story 2.3) -------------------------------------------------------------


def test_area_is_saved_and_returned_as_strings_with_the_derived_square_feet():
    client = signed_in()
    created = draft(client)
    response = edit(
        client, created, {"area_value": "5", "area_unit": "marla", "marla_basis": "225"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 2
    assert {
        k: body["fields"][k] for k in ("area_value", "area_unit", "marla_basis", "area_sqft")
    } == {
        "area_value": "5.0000",
        "area_unit": "marla",
        "marla_basis": "225",
        "area_sqft": "1125.0000",
    }
    assert set(body["provenance"]) == {"area_value", "area_unit", "marla_basis"}
    assert client.get(detail(created["id"])).json() == body
    assert client.get(MINE).json()["results"][0] == body
    assert ListingRevision.objects.get(revision=2).changes["area_sqft"] == "1125.0000"
    raw = response.content.decode()
    assert '"area_value":"5.0000"' in raw.replace(" ", "")  # a string, never a number


def test_a_marla_without_a_basis_saves_and_the_basis_can_come_later():
    client = signed_in()
    created = draft(client)
    first = edit(client, created, {"area_value": "5", "area_unit": "marla"}).json()
    assert first["revision"] == 2 and first["fields"]["area_sqft"] is None
    second = edit(client, first, {"marla_basis": "272.25"}).json()
    assert second["fields"]["area_sqft"] == "1361.2500" and second["revision"] == 3


def test_changing_the_unit_away_shows_the_cleared_basis_as_null():
    client = signed_in()
    created = draft(client)
    first = edit(
        client, created, {"area_value": "1", "area_unit": "kanal", "marla_basis": "250"}
    ).json()
    assert first["fields"]["area_sqft"] == "5000.0000"
    second = edit(client, first, {"area_unit": "sqft"}).json()
    assert second["fields"]["marla_basis"] is None and second["fields"]["area_sqft"] == "1.0000"
    assert "marla_basis" not in second["provenance"]


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"area_value": "0"}, {"area_value": "invalid_area"}),
        ({"area_value": "-5"}, {"area_value": "invalid_area"}),
        ({"area_value": "1.12345"}, {"area_value": "invalid_area"}),
        ({"area_value": "99999999999"}, {"area_value": "invalid_area"}),
        ({"area_value": "12,5"}, {"area_value": "invalid"}),
        ({"area_value": "1e3"}, {"area_value": "invalid"}),
        ({"area_value": "abc"}, {"area_value": "invalid"}),
        ({"area_value": 5}, {"area_value": "invalid"}),
        ({"area_value": 5.5}, {"area_value": "invalid"}),
        ({"area_unit": "hectare"}, {"area_unit": "invalid_choice"}),
        ({"area_unit": "marla", "marla_basis": "300"}, {"marla_basis": "invalid_choice"}),
        ({"area_unit": "marla", "marla_basis": 225}, {"marla_basis": "invalid_choice"}),
        ({"area_unit": "sqft", "marla_basis": "225"}, {"marla_basis": "not_applicable"}),
        ({"area_sqft": "100"}, {"area_sqft": "not_editable"}),
    ],
)
def test_area_errors_are_codes_keyed_by_field_and_nothing_is_written(fields, expected):
    client = signed_in()
    created = draft(client)
    response = edit(client, created, fields)
    assert response.status_code == 400 and code(response) == "validation_error"
    assert field_codes(response) == {name: [field_message(c)] for name, c in expected.items()}
    assert not ListingArea.objects.exists() and ListingRevision.objects.count() == 1
    assert "hectare" not in response.content.decode()


def test_the_area_error_messages_are_catalogue_text_without_the_value():
    client = signed_in()
    created = draft(client)
    response = edit(client, created, {"area_value": "-5"})
    message = field_codes(response)
    assert message == {"area_value": [field_message("invalid_area")]}
    assert message["area_value"] == [
        "Enter an area above zero with at most four decimals, using digits only."
    ]
    assert "-5" not in body_text(response)


def test_a_stale_area_edit_is_409_with_the_current_area_and_a_replay_is_too():
    client = signed_in()
    created = draft(client)
    winner = edit(client, created, {"area_value": "3", "area_unit": "acre"})
    assert winner.status_code == 200
    stale = edit(client, created, {"area_value": "4"})
    assert stale.status_code == 409 and code(stale) == "stale_revision"
    assert stale.json()["current"] == winner.json()
    assert stale.json()["current"]["fields"]["area_sqft"] == "130680.0000"
    replay = edit(client, created, {"area_value": "3", "area_unit": "acre"})
    assert replay.status_code == 409


def test_clearing_the_area_over_http():
    client = signed_in()
    created = draft(client)
    saved = edit(client, created, {"area_value": "3", "area_unit": "sqyd"}).json()
    cleared = edit(client, saved, {"area_value": None}).json()
    assert cleared["fields"]["area_value"] is None and cleared["fields"]["area_sqft"] is None
    assert "area_value" not in cleared["provenance"]


def test_other_accounts_cannot_edit_or_see_the_area():
    owner = signed_in()
    created = draft(owner)
    edit(owner, created, {"area_value": "3", "area_unit": "sqft"})
    other = signed_in("john.roe@example.com")
    assert edit(other, created, {"area_value": "9"}).status_code == 404
    assert other.get(detail(created["id"])).status_code == 404
    assert ListingArea.objects.get().area_value == Decimal("3")


def test_openapi_describes_the_area_fields_and_the_registry_serves_them(tmp_path):
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    schemas = yaml.safe_load(target.read_text())["components"]["schemas"]
    fields = schemas["ListingFields"]["properties"]
    for name in ("area_value", "area_sqft"):
        assert fields[name]["type"] == "string" and fields[name]["nullable"] is True
    assert "Computed by the server" in fields["area_sqft"]["description"]
    registry_fields = {f["id"]: f for f in Client().get("/api/v1/registry").json()["fields"]}
    assert registry_fields["area_sqft"]["derived"] is True
    assert registry_fields["area_value"]["scale"] == 4
    assert registry_fields["marla_basis"]["allowed_values"] == ["225", "250", "272.25"]


# --- House and Apartment details (Story 2.4) ------------------------------------------------------

APARTMENT_BODY = {"purpose": "sale", "category": "apartment", "subtype": "apartment"}


def test_house_details_over_http_use_native_json_types_and_stay_null_when_unknown():
    client = signed_in()
    created = draft(client)
    assert all(created["fields"][name] is None for name in DETAIL_IDS)
    response = edit(
        client,
        created,
        {"bedrooms": 4, "bathrooms": 3, "basement": False, "covered_area_sqft": "2400"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 2 and body["registry_version"] == 10
    assert body["fields"]["bedrooms"] == 4 and type(body["fields"]["bedrooms"]) is int
    assert body["fields"]["basement"] is False
    assert body["fields"]["covered_area_sqft"] == "2400.0000"
    assert body["fields"]["kitchens"] is None and body["fields"]["lift"] is None
    assert set(body["provenance"]) == {"bedrooms", "bathrooms", "basement", "covered_area_sqft"}
    assert client.get(detail(created["id"])).json() == body
    assert ListingResidential.objects.get().bedrooms == 4


def test_an_apartment_over_http_and_the_floor_rule():
    client = signed_in()
    created = draft(client, APARTMENT_BODY)
    bad = edit(client, created, {"floor_number": 10, "building_floors": 5})
    assert (bad.status_code, code(bad)) == (400, "validation_error")
    assert field_codes(bad) == {
        "floor_number": [field_message("invalid_floor")],
        "building_floors": [field_message("invalid_floor")],
    }
    assert ListingResidential.objects.count() == 0
    good = edit(
        client,
        created,
        {"floor_number": 5, "building_floors": 12, "lift": True, "maintenance_charges": "5000"},
    ).json()
    assert good["fields"]["lift"] is True and good["fields"]["maintenance_charges"] == "5000.00"


@pytest.mark.parametrize(
    ("body", "fields", "expected"),
    [
        (BODY, {"bedrooms": "3"}, "invalid"),
        (BODY, {"bedrooms": 3.0}, "invalid"),
        (BODY, {"bedrooms": True}, "invalid"),
        (BODY, {"bedrooms": 51}, "invalid_range"),
        (BODY, {"bedrooms": -1}, "invalid_range"),
        (BODY, {"construction_year": 1899}, "invalid_range"),
        (BODY, {"basement": "yes"}, "invalid"),
        (BODY, {"basement": 1}, "invalid"),
        (BODY, {"condition": "mint"}, "invalid_choice"),
        (BODY, {"covered_area_sqft": "0"}, "invalid_area"),
        (BODY, {"covered_area_sqft": "abc"}, "invalid"),
        (BODY, {"lift": True}, "not_applicable"),
        (APARTMENT_BODY, {"kitchens": 1}, "not_applicable"),
        (APARTMENT_BODY, {"maintenance_charges": "-1"}, "invalid_price"),
        (APARTMENT_BODY, {"maintenance_charges": "abc"}, "invalid"),
        (
            {"purpose": "sale", "category": "plot", "subtype": "residential_plot"},
            {"bedrooms": 2},
            "not_applicable",
        ),
        (
            {"purpose": "rent", "category": "commercial", "subtype": "shop"},
            {"bedrooms": 2},
            "not_applicable",
        ),
    ],
)
def test_residential_error_codes_over_http(body, fields, expected):
    client = signed_in()
    created = draft(client, body)
    response = edit(client, created, fields)
    assert (response.status_code, code(response)) == (400, "validation_error")
    name = next(iter(fields))
    assert field_codes(response) == {name: [field_message(expected)]}
    if isinstance(fields[name], str) and len(fields[name]) >= 3:
        assert fields[name] not in body_text(response)  # the value is never echoed
    assert not ListingResidential.objects.exists()
    assert client.get(detail(created["id"])).json()["revision"] == 1


def test_the_new_error_messages_are_catalogue_text():
    assert field_message("invalid_range") == "Enter a whole number within the allowed range."
    assert field_message("invalid_floor").startswith("The floor cannot be above")


def test_a_clear_over_http_and_a_stale_edit_is_409_with_the_details():
    client = signed_in()
    created = draft(client)
    saved = edit(client, created, {"bedrooms": 3, "kitchens": 2}).json()
    stale = edit(client, created, {"bedrooms": 9})
    assert (stale.status_code, code(stale)) == (409, "stale_revision")
    assert stale.json()["current"] == saved and stale.json()["current"]["fields"]["kitchens"] == 2
    cleared = edit(client, saved, {"kitchens": None}).json()
    assert cleared["fields"]["kitchens"] is None and "kitchens" not in cleared["provenance"]
    assert ListingResidential.objects.get().kitchens is None


def test_a_never_edited_draft_has_no_row_after_clears_and_reads():
    client = signed_in()
    created = draft(client)
    response = edit(client, created, {"bedrooms": None, "basement": None})
    assert response.status_code == 200 and response.json()["revision"] == 1
    client.get(detail(created["id"]))
    client.get(MINE)
    assert not ListingResidential.objects.exists()


def test_openapi_describes_the_residential_fields_with_native_types(tmp_path):
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    schemas = yaml.safe_load(target.read_text())["components"]["schemas"]
    for schema in ("ListingFields", "ListingFieldsUpdateRequest"):
        props = schemas[schema]["properties"]
        assert props["bedrooms"]["type"] == "integer" and props["bedrooms"]["nullable"] is True
        assert (props["bedrooms"]["minimum"], props["bedrooms"]["maximum"]) == (0, 50)
        assert props["construction_year"]["minimum"] == 1900
        assert props["lift"]["type"] == "boolean" and props["lift"]["nullable"] is True
        assert props["covered_area_sqft"]["type"] == "string"
        assert props["maintenance_charges"]["type"] == "string"
        assert "never text" in props["bedrooms"]["description"]
    assert schemas["ConditionEnum"]["enum"] == list(registry.field("condition").allowed_values)
    update = yaml.safe_load(target.read_text())["paths"]["/api/v1/listings/{listing_id}"]["patch"]
    assert "invalid_floor" in update["description"] and "invalid_range" in update["description"]


# --- Plot, Plot File, Shop and Office details (Story 2.5) -----------------------------------------

PLOT_BODY = {"purpose": "sale", "category": "plot", "subtype": "residential_plot"}
PLOT_FILE_BODY = {"purpose": "sale", "category": "plot", "subtype": "plot_file"}
SHOP_BODY = {"purpose": "rent", "category": "commercial", "subtype": "shop"}
OFFICE_BODY = {"purpose": "rent", "category": "commercial", "subtype": "office"}


def detail_rows():
    return (
        ListingPlot.objects.exists()
        or ListingCommercial.objects.exists()
        or ListingResidential.objects.exists()
    )


def test_plot_details_over_http_use_native_types_and_decimal_strings():
    client = signed_in()
    created = draft(client, PLOT_BODY)
    assert all(created["fields"][name] is None for name in DETAIL_IDS)
    response = edit(
        client,
        created,
        {
            "plot_width": "35",
            "plot_depth": "70.5",
            "possession_status": "possession_ready",
            "corner_plot": True,
            "road_width_ft": "40",
            "development_status": "developed",
            "approval_authority": "lda",
            "water": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 2 and body["registry_version"] == 10
    assert body["fields"]["plot_width"] == "35.00" and body["fields"]["plot_depth"] == "70.50"
    assert body["fields"]["road_width_ft"] == "40.00"
    assert body["fields"]["corner_plot"] is True and body["fields"]["water"] is False
    assert body["fields"]["sewerage"] is None and body["fields"]["frontage_ft"] is None
    assert set(body["provenance"]) == {
        "plot_width",
        "plot_depth",
        "possession_status",
        "corner_plot",
        "road_width_ft",
        "development_status",
        "approval_authority",
        "water",
    }
    assert client.get(detail(created["id"])).json() == body
    assert ListingPlot.objects.get().plot_width == Decimal("35.00")
    assert not ListingCommercial.objects.exists()


def test_a_plot_file_over_http_takes_file_only_and_refuses_possession_ready():
    client = signed_in()
    created = draft(client, PLOT_FILE_BODY)
    bad = edit(client, created, {"possession_status": "possession_ready"})
    assert (bad.status_code, code(bad)) == (400, "validation_error")
    assert field_codes(bad) == {"possession_status": [field_message("invalid_choice")]}
    assert "possession_ready" not in body_text(bad)
    assert not detail_rows()
    good = edit(
        client, created, {"possession_status": "file_only", "plot_file_status": "allocation_letter"}
    )
    assert good.status_code == 200
    assert good.json()["fields"]["possession_status"] == "file_only"
    assert good.json()["fields"]["plot_file_status"] == "allocation_letter"


def test_a_plot_refuses_file_only_and_plot_file_status():
    client = signed_in()
    created = draft(client, PLOT_BODY)
    response = edit(
        client, created, {"possession_status": "file_only", "plot_file_status": "open_file"}
    )
    assert field_codes(response) == {
        "possession_status": [field_message("invalid_choice")],
        "plot_file_status": [field_message("not_applicable")],
    }
    assert not detail_rows()


def test_shop_and_office_details_over_http():
    client = signed_in()
    shop = draft(client, SHOP_BODY)
    saved = edit(
        client,
        shop,
        {
            "business_use": "retail",
            "frontage_ft": "18.25",
            "road_access": "main_road",
            "floor_number": 2,
            "building_floors": 3,
            "condition": "good",
        },
    ).json()
    assert saved["fields"]["frontage_ft"] == "18.25" and saved["fields"]["floor_number"] == 2
    assert ListingCommercial.objects.get().business_use == "retail"
    assert ListingResidential.objects.get().floor_number == 2
    bad = edit(client, saved, {"floor_number": 5, "building_floors": 3})
    assert field_codes(bad) == {
        "floor_number": [field_message("invalid_floor")],
        "building_floors": [field_message("invalid_floor")],
    }
    office = draft(client, OFFICE_BODY)
    saved = edit(client, office, {"conference_rooms": 3, "lift": True}).json()
    assert saved["fields"]["conference_rooms"] == 3 and saved["fields"]["lift"] is True


@pytest.mark.parametrize(
    ("body", "fields", "expected"),
    [
        (PLOT_BODY, {"plot_width": "0"}, "invalid_measure"),
        (PLOT_BODY, {"plot_width": "-5.5"}, "invalid_measure"),
        (PLOT_BODY, {"plot_width": "12.345"}, "invalid_measure"),
        (PLOT_BODY, {"plot_width": "100000"}, "invalid_measure"),
        (PLOT_BODY, {"road_width_ft": "0.99"}, "invalid_measure"),
        (PLOT_BODY, {"plot_width": "12,5"}, "invalid"),
        (PLOT_BODY, {"plot_width": "1e3"}, "invalid"),
        (PLOT_BODY, {"plot_width": "0012"}, "invalid"),
        (PLOT_BODY, {"plot_width": 35}, "invalid"),
        (PLOT_BODY, {"plot_width": 35.5}, "invalid"),
        (PLOT_BODY, {"plot_width": True}, "invalid"),
        (PLOT_BODY, {"corner_plot": "yes"}, "invalid"),
        (PLOT_BODY, {"corner_plot": 1}, "invalid"),
        (PLOT_BODY, {"approval_authority": "xyz"}, "invalid_choice"),
        (PLOT_BODY, {"orientation": "up"}, "invalid_choice"),
        (PLOT_BODY, {"possession_status": "file_only"}, "invalid_choice"),
        (PLOT_BODY, {"frontage_ft": "10"}, "not_applicable"),
        (PLOT_BODY, {"bedrooms": 2}, "not_applicable"),
        (PLOT_FILE_BODY, {"possession_status": "possession_pending"}, "invalid_choice"),
        (SHOP_BODY, {"conference_rooms": 2}, "not_applicable"),
        (SHOP_BODY, {"lift": True}, "not_applicable"),
        (SHOP_BODY, {"kitchens": 1}, "not_applicable"),
        (SHOP_BODY, {"frontage_ft": "10000"}, "invalid_measure"),
        (SHOP_BODY, {"frontage_ft": "abc"}, "invalid"),
        (OFFICE_BODY, {"frontage_ft": "10"}, "not_applicable"),
        (OFFICE_BODY, {"conference_rooms": 51}, "invalid_range"),
        (OFFICE_BODY, {"conference_rooms": "3"}, "invalid"),
        (OFFICE_BODY, {"power_load_kw": "0"}, "invalid_measure"),
        (BODY, {"business_use": "retail"}, "not_applicable"),
        (BODY, {"plot_width": "10"}, "not_applicable"),
    ],
)
def test_plot_and_commercial_error_codes_over_http(body, fields, expected):
    client = signed_in()
    created = draft(client, body)
    response = edit(client, created, fields)
    assert (response.status_code, code(response)) == (400, "validation_error")
    name = next(iter(fields))
    assert field_codes(response) == {name: [field_message(expected)]}
    sent = fields[name]
    if isinstance(sent, str) and len(sent) >= 3:
        assert sent not in body_text(response)  # the value is never echoed, anywhere in the body
    assert not detail_rows()
    assert client.get(detail(created["id"])).json()["revision"] == 1


def test_the_measure_message_is_catalogue_text_without_the_value():
    client = signed_in()
    created = draft(client, PLOT_BODY)
    response = edit(client, created, {"plot_width": "-77.123"})
    assert field_codes(response) == {
        "plot_width": [
            "Enter a number above zero within the allowed range, "
            "using digits and an optional decimal point."
        ]
    }
    assert "-77.123" not in body_text(response)
    office = draft(client, OFFICE_BODY)
    power = edit(client, office, {"power_load_kw": "0"})
    assert field_codes(power) == {"power_load_kw": [field_message("invalid_measure")]}
    assert "feet" not in field_codes(power)["power_load_kw"][0]


@pytest.mark.parametrize(
    "name",
    [
        "installment_available",
        "down_payment",
        "title_document_type",
        "mortgage_declaration",
        "approval_reference_number",
    ],
)
def test_unsupported_attributes_are_unknown_fields_over_http(name):
    client = signed_in()
    created = draft(client, PLOT_BODY)
    response = edit(client, created, {name: "anything", "plot_width": "10"})
    assert (response.status_code, code(response)) == (400, "validation_error")
    assert response.json()["error"]["fields"].get("non_field_errors") is not None
    assert "anything" not in body_text(response)
    assert not detail_rows()
    assert client.get(detail(created["id"])).json()["revision"] == 1


def test_a_clear_a_null_on_a_not_applicable_field_and_reads_never_create_rows():
    client = signed_in()
    created = draft(client, SHOP_BODY)
    response = edit(client, created, {"plot_width": None, "frontage_ft": None, "water": None})
    assert response.status_code == 200 and response.json()["revision"] == 1
    client.get(detail(created["id"]))
    client.get(MINE)
    assert not detail_rows()
    saved = edit(client, created, {"frontage_ft": "10"}).json()
    cleared = edit(client, saved, {"frontage_ft": None}).json()
    assert cleared["fields"]["frontage_ft"] is None and "frontage_ft" not in cleared["provenance"]
    assert ListingCommercial.objects.get().frontage_ft is None


def test_a_stale_edit_is_409_with_the_plot_details_and_a_replay_is_too():
    client = signed_in()
    created = draft(client, PLOT_BODY)
    saved = edit(client, created, {"plot_width": "30", "corner_plot": True}).json()
    for _ in range(2):
        stale = edit(client, created, {"plot_width": "99"})
        assert (stale.status_code, code(stale)) == (409, "stale_revision")
        assert stale.json()["current"] == saved
    assert stale.json()["current"]["fields"]["plot_width"] == "30.00"
    assert ListingPlot.objects.get().plot_width == Decimal("30.00")


def test_openapi_describes_the_plot_and_commercial_fields(tmp_path):
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    document = yaml.safe_load(target.read_text())
    schemas = document["components"]["schemas"]
    for schema in ("ListingFields", "ListingFieldsUpdateRequest"):
        props = schemas[schema]["properties"]
        for name in TAIL_IDS:
            assert name in props, (schema, name)
        assert props["plot_width"]["type"] == "string" and props["plot_width"]["nullable"] is True
        assert "never a JSON number" in props["plot_width"]["description"]
        assert props["corner_plot"]["type"] == "boolean"
        assert props["conference_rooms"]["type"] == "integer"
        assert (props["conference_rooms"]["minimum"], props["conference_rooms"]["maximum"]) == (
            0,
            50,
        )
        assert "Seller-declared" in props["possession_status"]["description"]
        assert "Seller-declared" in props["approval_authority"]["description"]
        assert "Seller-declared" not in props["business_use"].get("description", "")
    update = document["paths"]["/api/v1/listings/{listing_id}"]["patch"]["description"]
    assert "invalid_measure" in update and "seller-declared" in update
    registry_schema = schemas["RegistryField"]["properties"]
    assert "seller_declared" in registry_schema and "allowed_values_by_subtype" in registry_schema
    assert "invalid_measure" in update


# --- Sale and rent terms (Story 2.6) -------------------------------------------------------------

SALE_BODY = {"purpose": "sale", "category": "apartment", "subtype": "apartment"}
RENT_BODY = {"purpose": "rent", "category": "apartment", "subtype": "apartment"}
RENT_TERMS_SAVE = {
    "asking_price": "25000",
    "price_basis": "rental_period",
    "rental_period": "month",
    "security_deposit": "50000",
    "availability_date": "2027-03-01",
    "advance_rent": "25000.50",
    "minimum_lease_months": 12,
    "notice_period_days": 30,
    "utility_electricity_included": False,
    "utility_gas_included": True,
    "pets_allowed": False,
    "occupant_restrictions": "families_only",
    "price_negotiable": True,
}


def test_rent_terms_over_http_use_native_types_and_a_plain_date():
    client = signed_in()
    created = draft(client, RENT_BODY)
    assert all(created["fields"][name] is None for name in TERMS_IDS)
    response = edit(client, created, RENT_TERMS_SAVE)
    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 2 and body["registry_version"] == 10
    wire = body["fields"]
    assert wire["availability_date"] == "2027-03-01"  # a date, never a timestamp
    assert wire["security_deposit"] == "50000.00" and wire["advance_rent"] == "25000.50"
    assert wire["minimum_lease_months"] == 12 and wire["notice_period_days"] == 30
    assert wire["pets_allowed"] is False and wire["utility_electricity_included"] is False
    assert wire["utility_gas_included"] is True and wire["utility_water_included"] is None
    assert wire["occupant_restrictions"] == "families_only" and wire["price_negotiable"] is True
    assert wire["possession_availability"] is None
    assert set(body["provenance"]) == set(RENT_TERMS_SAVE)
    assert client.get(detail(created["id"])).json() == body
    assert client.get(MINE).json()["results"][0] == body
    assert list(wire) == list(created["fields"])  # the wire order never depends on what is set
    assert ListingTerms.objects.count() == 1 and not detail_rows()


def test_sale_terms_over_http_and_the_rent_terms_stay_null():
    client = signed_in()
    created = draft(client, SALE_BODY)
    saved = edit(
        client,
        created,
        {"price_negotiable": False, "possession_availability": "within_three_months"},
    ).json()
    assert saved["fields"]["price_negotiable"] is False
    assert saved["fields"]["possession_availability"] == "within_three_months"
    assert all(saved["fields"][name] is None for name in RENT_TERMS)
    assert ListingTerms.objects.get().possession_availability == "within_three_months"


def test_the_terms_come_after_the_details_in_the_json_and_the_area_stays_last():
    client = signed_in()
    names = list(draft(client, RENT_BODY)["fields"])
    assert names[-len(TAIL_IDS) - 1 : -1] == list(TAIL_IDS) and names[-1] == "area_sqft"


@pytest.mark.parametrize(
    ("body", "fields", "expected"),
    [
        (RENT_BODY, {"security_deposit": "-1"}, "invalid_price"),
        (RENT_BODY, {"security_deposit": "1.234"}, "invalid_price"),
        (RENT_BODY, {"advance_rent": "1000000000000"}, "invalid_price"),
        (RENT_BODY, {"advance_rent": "letters"}, "invalid"),
        (RENT_BODY, {"advance_rent": 1.5}, "invalid"),
        (RENT_BODY, {"availability_date": "2027-02-30"}, "invalid"),
        (RENT_BODY, {"availability_date": "2027-3-1"}, "invalid"),
        (RENT_BODY, {"availability_date": "2027-03-01T00:00:00"}, "invalid"),
        (RENT_BODY, {"availability_date": "2027-03-01T00:00:00+05:00"}, "invalid"),
        (RENT_BODY, {"availability_date": 20270301}, "invalid"),
        (RENT_BODY, {"availability_date": 1.5}, "invalid"),
        (RENT_BODY, {"availability_date": "٢٠٢٧-٠٣-٠١"}, "invalid"),
        (RENT_BODY, {"availability_date": ["2027-03-01"]}, "invalid"),
        (RENT_BODY, {"availability_date": "1999-12-31"}, "invalid_date"),
        (RENT_BODY, {"availability_date": "2101-01-01"}, "invalid_date"),
        (RENT_BODY, {"minimum_lease_months": 0}, "invalid_range"),
        (RENT_BODY, {"minimum_lease_months": 121}, "invalid_range"),
        (RENT_BODY, {"notice_period_days": -1}, "invalid_range"),
        (RENT_BODY, {"notice_period_days": 366}, "invalid_range"),
        (RENT_BODY, {"minimum_lease_months": "12"}, "invalid"),
        (RENT_BODY, {"notice_period_days": 12.0}, "invalid"),
        (RENT_BODY, {"pets_allowed": "yes"}, "invalid"),
        (RENT_BODY, {"utility_water_included": 1}, "invalid"),
        (RENT_BODY, {"occupant_restrictions": "any"}, "invalid_choice"),
        (SALE_BODY, {"possession_availability": "tomorrow"}, "invalid_choice"),
        (SALE_BODY, {"price_negotiable": "yes"}, "invalid"),
        (SALE_BODY, {"security_deposit": "10"}, "not_applicable"),
        (SALE_BODY, {"availability_date": "2027-03-01"}, "not_applicable"),
        (SALE_BODY, {"pets_allowed": True}, "not_applicable"),
        (SALE_BODY, {"minimum_lease_months": 6}, "not_applicable"),
        (RENT_BODY, {"possession_availability": "available_now"}, "not_applicable"),
    ],
)
def test_terms_error_codes_over_http(body, fields, expected):
    client = signed_in()
    created = draft(client, body)
    response = edit(client, created, fields)
    assert (response.status_code, code(response)) == (400, "validation_error")
    name = next(iter(fields))
    assert field_codes(response) == {name: [field_message(expected)]}
    sent = fields[name]
    if isinstance(sent, str) and len(sent) >= 3:
        assert sent not in body_text(response)  # the value is never echoed, anywhere in the body
    stored = ListingTerms.objects.get()
    assert getattr(stored, name) is None and client.get(detail(created["id"])).json() == created


def test_the_terms_messages_are_catalogue_texts_without_the_value():
    client = signed_in()
    created = draft(client, RENT_BODY)
    response = edit(
        client, created, {"availability_date": "2101-01-01", "security_deposit": "-7.5"}
    )
    assert field_codes(response) == {
        "availability_date": ["Enter a date within the allowed range."],
        "security_deposit": ["Enter an amount within the allowed range with at most two decimals."],
    }
    assert "2101" not in body_text(response) and "7.5" not in body_text(response)


@pytest.mark.parametrize(
    "name",
    [
        "payment_type",
        "down_payment",
        "total_installment_price",
        "monthly_installment",
        "remaining_installments",
        "ownership_document_type",
        "title_document_type",
        "approval_information",
        "installment_available",
    ],
)
@pytest.mark.parametrize("body", [SALE_BODY, RENT_BODY])
def test_deferred_terms_are_unknown_fields_over_http(name, body):
    client = signed_in()
    created = draft(client, body)
    response = edit(client, created, {name: "secretword", "price_negotiable": True})
    assert (response.status_code, code(response)) == (400, "validation_error")
    assert field_codes(response) == {"non_field_errors": [field_message("unknown_field")]}
    assert "secretword" not in body_text(response) and name not in body_text(response)
    assert ListingTerms.objects.get().price_negotiable is None
    assert client.get(detail(created["id"])).json()["revision"] == 1


def test_a_null_on_a_not_applicable_term_is_a_no_op_and_a_clear_removes_provenance():
    client = signed_in()
    sale = draft(client, SALE_BODY)
    response = edit(client, sale, {"security_deposit": None, "availability_date": None})
    assert response.status_code == 200 and response.json() == sale
    assert ListingRevision.objects.count() == 1
    rent = draft(client, RENT_BODY)
    saved = edit(client, rent, {"security_deposit": "5", "availability_date": "2027-03-01"}).json()
    cleared = edit(client, saved, {"availability_date": None}).json()
    assert cleared["revision"] == 3 and cleared["fields"]["availability_date"] is None
    assert "availability_date" not in cleared["provenance"]
    assert cleared["fields"]["security_deposit"] == "5.00"
    assert ListingTerms.objects.get(listing_id=rent["id"]).availability_date is None


def test_a_rent_draft_without_a_period_saves_and_serves_a_null_period():
    client = signed_in()
    created = draft(client, RENT_BODY)
    saved = edit(client, created, {"price_basis": "rental_period"}).json()
    assert saved["fields"]["price_basis"] == "rental_period"
    assert saved["fields"]["rental_period"] is None
    assert client.get(detail(created["id"])).json()["fields"]["rental_period"] is None
    served = {f["id"]: f for f in Client().get("/api/v1/registry").json()["fields"]}
    assert served["rental_period"]["requirement"] == "required_for_publication"
    assert served["rental_period"]["applicability"]["purposes"] == ["rent"]


def test_a_stale_terms_edit_is_409_with_the_current_terms_and_a_replay_is_too():
    client = signed_in()
    created = draft(client, RENT_BODY)
    saved = edit(client, created, {"availability_date": "2027-03-01", "pets_allowed": True}).json()
    for _ in range(2):
        stale = edit(client, created, {"availability_date": "2027-04-01"})
        assert (stale.status_code, code(stale)) == (409, "stale_revision")
        assert stale.json()["current"] == saved
    assert stale.json()["current"]["fields"]["availability_date"] == "2027-03-01"
    assert ListingTerms.objects.get().availability_date.isoformat() == "2027-03-01"


def test_another_owner_cannot_edit_terms():
    owner = signed_in()
    created = draft(owner, RENT_BODY)
    other = signed_in("john.roe@example.com")
    response = edit(other, created, {"security_deposit": "5"})
    assert (response.status_code, code(response)) == (404, "not_found")
    assert ListingTerms.objects.get().security_deposit is None


def test_openapi_describes_the_date_and_the_terms(tmp_path):
    target = tmp_path / "openapi.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    document = yaml.safe_load(target.read_text())
    schemas = document["components"]["schemas"]
    for schema in ("ListingFields", "ListingFieldsUpdateRequest"):
        props = schemas[schema]["properties"]
        for name in TERMS_IDS:
            assert name in props, (schema, name)
        date = props["availability_date"]
        assert date["type"] == "string" and date["format"] == "date" and date["nullable"] is True
        assert "YYYY-MM-DD" in date["description"] and "never a timestamp" in date["description"]
        assert props["security_deposit"]["type"] == "string"
        assert (
            props["minimum_lease_months"]["minimum"],
            props["minimum_lease_months"]["maximum"],
        ) == (1, 120)
        assert props["pets_allowed"]["type"] == "boolean"
        assert "Seller-declared" in props["possession_availability"]["description"]
        assert "Seller-declared" not in props["occupant_restrictions"].get("description", "")
    update = document["paths"]["/api/v1/listings/{listing_id}"]["patch"]["description"]
    assert "invalid_date" in update and "availability_date" in update
    assert "installment" in update and "unknown_field" in update
    assert schemas["TypeEnum"]["enum"] == list(registry.FIELD_TYPES)
    assert "date" in schemas["TypeEnum"]["enum"]
