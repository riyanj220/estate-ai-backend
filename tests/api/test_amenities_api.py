"""Amenities over HTTP (Story 2.8), with Django's CSRF checks on: the registry type, the owner
resource, replacement and clearing, the error envelope with static messages, the 409 current, the
non-owner paths and the OpenAPI contract. Codes are catalogue codes or low-entropy fake words."""

import json

import pytest
import yaml
from django.core.management import call_command
from django.test import Client

from apps.kernel.errors import field_message
from apps.listings.models import ListingAmenity, ListingRevision
from tests.api.test_listings_api import (
    body_text,
    code,
    detail,
    draft,
    edit,
    field_codes,
    signed_in,
)
from tests.listings.support import seed_cap, set_listing_visibility

ELEVATOR, LOBBY, POOL, SCHOOL = "elevator", "lobby", "swimming_pool", "nearby_school"
MAP = {ELEVATOR: "present", LOBBY: "none", SCHOOL: "present"}


@pytest.fixture(autouse=True)
def setup(db):
    seed_cap()


def test_the_registry_serves_the_amenities_field_with_its_type_and_section():
    body = Client().get("/api/v1/registry").json()
    served = {f["id"]: f for f in body["fields"]}
    field = served["amenities"]
    assert (field["type"], field["section"], field["class"]) == ("amenities", "amenities", "public")
    assert field["requirement"] == "optional" and field["searchable"] and not field["sortable"]
    assert field["applicability"] == {"purposes": [], "categories": [], "subtypes": []}
    assert field["allowed_values"] is None and field["reference"] is None
    assert body["fields"][-1]["id"] == "amenities"
    assert "distance" not in json.dumps(body) and "verified" not in json.dumps(body["fields"][-1])


def test_the_catalogue_endpoint_is_public_and_matches_what_the_listing_accepts():
    items = Client().get("/api/v1/reference/amenities").json()["items"]
    assert len(items) == 26 and {i["kind"] for i in items} == {"on_property", "community", "nearby"}
    client = signed_in()
    created = draft(client)
    everything = {item["code"]: "present" for item in items[:26]}
    response = edit(client, created, {"amenities": everything})
    assert response.status_code == 200
    assert response.json()["fields"]["amenities"] == everything


def test_the_owner_saves_replaces_and_clears_the_map():
    client = signed_in()
    created = draft(client)
    assert created["fields"]["amenities"] is None
    assert "amenities" not in created  # the old top-level placeholder is gone
    response = edit(client, created, {"amenities": MAP})
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    saved = response.json()
    assert saved["revision"] == 2 and saved["fields"]["amenities"] == MAP
    assert saved["provenance"]["amenities"] == {"origin": "manual", "confirmed": True}
    assert client.get(detail(created["id"])).json() == saved
    assert client.get("/api/v1/me/listings").json()["results"][0]["fields"]["amenities"] == MAP
    replaced = edit(client, saved, {"amenities": {POOL: "none"}}).json()
    assert replaced["fields"]["amenities"] == {POOL: "none"} and replaced["revision"] == 3
    cleared = edit(client, replaced, {"amenities": {}}).json()
    assert cleared["fields"]["amenities"] is None and "amenities" not in cleared["provenance"]
    again = edit(client, cleared, {"amenities": MAP}).json()
    nulled = edit(client, again, {"amenities": None}).json()
    assert nulled["fields"]["amenities"] is None and nulled["revision"] == 6
    assert ListingAmenity.objects.count() == 0


def test_a_code_left_out_is_not_in_the_map_and_is_never_none():
    client = signed_in()
    created = draft(client)
    saved = edit(client, created, {"amenities": {ELEVATOR: "present"}}).json()
    assert list(saved["fields"]["amenities"]) == [ELEVATOR]
    assert set(ListingAmenity.objects.values_list("state", flat=True)) == {"present"}


def test_invalid_values_are_400_with_a_static_message_and_never_echo_what_was_sent():
    client = signed_in()
    created = draft(client)
    bad = (
        {"plum_secret_code": "present"},
        {ELEVATOR: "maybe_plum"},
        {ELEVATOR: True},
        {ELEVATOR: {"state": "present", "distance_m": 300}},
        ["plum_secret_code"],
        "plum_secret_code",
        5,
    )
    for value in bad:
        response = edit(client, created, {"amenities": value})
        assert response.status_code == 400 and code(response) == "validation_error"
        assert field_codes(response) == {"amenities": [field_message("invalid")]}
        text = body_text(response)
        assert "plum_secret_code" not in text and "maybe_plum" not in text and "300" not in text
    assert ListingRevision.objects.count() == 1 and ListingAmenity.objects.count() == 0


def test_too_many_entries_are_a_400_and_write_nothing():
    client = signed_in()
    created = draft(client)
    many = {f"fake_word_{i:02d}": "present" for i in range(41)}
    response = edit(client, created, {"amenities": many})
    assert response.status_code == 400
    assert field_codes(response) == {"amenities": [field_message("invalid")]}
    assert "fake_word_00" not in body_text(response)
    assert ListingAmenity.objects.count() == 0


def test_a_stale_write_returns_the_owners_current_map_and_a_stranger_gets_404():
    client = signed_in()
    created = draft(client)
    saved = edit(client, created, {"amenities": MAP}).json()
    stale = edit(client, created, {"amenities": {POOL: "present"}}, base=1)
    assert (stale.status_code, code(stale)) == (409, "stale_revision")
    assert (
        stale.json()["current"] == saved and stale.json()["current"]["fields"]["amenities"] == MAP
    )
    assert ListingAmenity.objects.count() == 3
    other = signed_in("other.person@example.com")
    refused = edit(other, created, {"amenities": {POOL: "present"}}, base=1)
    assert refused.status_code == 404 and ELEVATOR not in body_text(refused)
    assert other.get(detail(created["id"])).status_code == 404


def test_a_visitor_cannot_edit_and_a_draft_is_not_public():
    client = signed_in()
    created = draft(client)
    edit(client, created, {"amenities": MAP})
    anonymous = Client(enforce_csrf_checks=True)
    token = anonymous.get("/api/v1/csrf").json()["csrf_token"]
    response = anonymous.patch(
        detail(created["id"]),
        json.dumps({"base_revision": 2, "fields": {"amenities": {POOL: "present"}}}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 401 and code(response) == "unauthenticated"
    assert anonymous.get(detail(created["id"])).status_code == 401
    assert set(ListingAmenity.objects.values_list("amenity_code", flat=True)) == set(MAP)


def test_openapi_describes_the_map_as_an_object_of_plain_strings_and_has_no_distance(tmp_path):
    target = tmp_path / "o.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    text = target.read_text()
    schema = yaml.safe_load(text)
    components = schema["components"]["schemas"]
    for name in ("ListingFields", "ListingFieldsUpdateRequest"):
        prop = components[name]["properties"]["amenities"]
        assert prop["type"] == "object" and prop["additionalProperties"]["type"] == "string"
        assert prop["nullable"] is True and "enum" not in json.dumps(prop)
    assert "amenities" not in components["Listing"]["properties"]  # no top-level placeholder
    assert "amenities" not in components["Listing"]["required"]
    # no distance anywhere in the contract, and no property of an amenity entry beyond the four
    for name, component in components.items():
        for prop in component.get("properties") or {}:
            assert "distance" not in prop, f"{name}.{prop}"
    assert set(components["Amenity"]["properties"]) == {"code", "label", "kind", "group"}
    assert "distance" not in text.lower().replace("no distance", "").replace("distance and", "")


def test_the_whole_listing_response_has_no_distance_or_verification_key():
    client = signed_in()
    created = draft(client)
    edit(client, created, {"amenities": {SCHOOL: "present"}})
    set_listing_visibility(created["id"], "published")
    saved = client.get(detail(created["id"])).json()  # re-read after publishing
    assert saved["status"] == "published" and saved["fields"]["amenities"] == {SCHOOL: "present"}

    def keys(node):
        if isinstance(node, dict):
            for key, value in node.items():
                yield key
                yield from keys(value)
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)

    forbidden = ("distance", "verified", "verification", "score", "metres", "minutes")
    for key in keys(saved):
        assert not any(word in key for word in forbidden), key


@pytest.mark.parametrize(
    "state", [" present", "present ", " present ", "\tnone", "none\n", None, 1.5, True, 0, ""]
)
def test_the_exact_state_rule_holds_at_the_http_boundary(state):
    client = signed_in()
    created = draft(client)
    response = edit(client, created, {"amenities": {ELEVATOR: state}})
    assert response.status_code == 400 and code(response) == "validation_error"
    assert field_codes(response) == {"amenities": [field_message("invalid")]}
    assert ListingAmenity.objects.count() == 0 and ListingRevision.objects.count() == 1


def test_a_padded_code_key_is_not_trimmed_either():
    client = signed_in()
    created = draft(client)
    response = edit(client, created, {"amenities": {" elevator": "present"}})
    assert response.status_code == 400 and ListingAmenity.objects.count() == 0
