"""Location and the private address over HTTP (Story 2.7), with Django's CSRF checks on. Values are
low-entropy fake words; the canary test drives the owner path and every non-owner path."""

import io
import json
import logging

import pytest
import yaml
from django.core.management import call_command
from django.test import Client

from apps.catalog import registry
from apps.catalog.models import City, Locality
from apps.identity import services as identity_services
from apps.kernel import runtime
from apps.kernel.errors import field_message
from apps.kernel.request_logging import JsonFormatter
from apps.listings.models import ListingRevision
from tests.api.test_listing_images_api import upload
from tests.api.test_listings_api import (
    ADMIN_PASSWORD,
    MINE,
    SYSTEM,
    body_text,
    code,
    detail,
    draft,
    edit,
    field_codes,
    sign_in,
    signed_in,
)
from tests.imaging import make_image
from tests.listings.support import (
    PRIVATE_ADDRESS_IDS,
    PUBLIC_LOCATION_IDS,
    seed_cap,
    seed_media_policies,
    set_listing_visibility,
)

STREET, HOUSE_NO = "Mango Lane", "Plum 12"
LAT, LON = "24.860000", "67.010000"
PRIVATE = {"street": STREET, "house_number": HOUSE_NO, "latitude": LAT, "longitude": LON}
NEEDLES = (STREET, HOUSE_NO, LAT, LON, "24.86", "67.01")


PNG = make_image("PNG", (400, 300))


@pytest.fixture(autouse=True)
def setup(db):
    seed_cap()
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


@pytest.fixture
def places(db):
    karachi, _ = City.objects.get_or_create(name="Karachi")
    lahore, _ = City.objects.get_or_create(name="Lahore")
    k1, _ = Locality.objects.get_or_create(city=karachi, name="Testville North", kind="area")
    l1, _ = Locality.objects.get_or_create(city=lahore, name="Lahoreville Block", kind="area")
    return {
        "karachi": str(karachi.id),
        "lahore": str(lahore.id),
        "k1": str(k1.id),
        "l1": str(l1.id),
    }


def test_the_registry_lists_the_location_fields_with_type_reference_and_class():
    body = Client().get("/api/v1/registry").json()
    served = {f["id"]: f for f in body["fields"]}
    assert body["version"] == 8
    assert (served["city"]["type"], served["city"]["reference"]) == ("reference", "city")
    assert (served["locality"]["type"], served["locality"]["reference"]) == (
        "reference",
        "locality",
    )
    assert served["locality"]["requirement"] == "required_for_publication"
    for name in PUBLIC_LOCATION_IDS:
        assert served[name]["class"] == "public" and served[name]["section"] == "location"
    for name in PRIVATE_ADDRESS_IDS:
        assert served[name]["class"] == "private" and served[name]["section"] == "location"
        assert not (served[name]["searchable"] or served[name]["ai_extractable"])
        assert served[name]["reference"] is None
    assert (served["latitude"]["min"], served["latitude"]["max"], served["latitude"]["scale"]) == (
        "-90",
        "90",
        6,
    )
    assert (served["longitude"]["min"], served["longitude"]["max"]) == ("-180", "180")
    assert all(f["reference"] is None for n, f in served.items() if f["type"] != "reference")
    # a registry value never carries an owner value
    assert not any(needle in json.dumps(body) for needle in NEEDLES)


def test_the_owner_saves_a_location_and_reads_it_back_with_the_private_part(places):
    client = signed_in()
    created = draft(client)
    response = edit(
        client,
        created,
        {
            "city": places["karachi"],
            "locality": places["k1"],
            "society": "Plum Society",
            **PRIVATE,
        },
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    saved = response.json()
    assert saved["revision"] == 2 and saved["registry_version"] == 8
    assert saved["fields"]["city"] == places["karachi"]
    assert saved["fields"]["locality"] == places["k1"]
    assert all(saved["fields"][n] == v for n, v in PRIVATE.items())
    assert saved["provenance"]["street"] == {"origin": "manual", "confirmed": True}
    again = client.get(detail(created["id"]))
    assert again.json() == saved and again.headers["Cache-Control"] == "no-store"
    assert client.get(MINE).json()["results"][0]["fields"]["street"] == STREET


def test_field_errors_are_codes_keyed_by_field_and_never_echo_a_value(places):
    client = signed_in()
    created = draft(client)
    response = edit(
        client,
        created,
        {
            "city": "Karachi",
            "locality": places["l1"],
            "society": "call 0300 1234567",
            "street": "mail plum.fake@example.com",
            "latitude": "91.5",
            "longitude": 5,
            "block": "x" * 61,
        },
    )
    assert response.status_code == 400 and code(response) == "validation_error"
    fields = field_codes(response)
    assert fields["city"] == [field_message("invalid")]
    assert fields["society"] == [field_message("contains_contact_details")]
    assert fields["street"] == [field_message("contains_contact_details")]
    assert fields["latitude"] == [field_message("out_of_range")]
    assert fields["longitude"] == [field_message("invalid")]
    assert fields["block"] == [field_message("too_long")]
    text = body_text(response)
    for echoed in ("Karachi", "0300", "plum.fake", "91.5", "x" * 61):
        assert echoed not in text
    assert ListingRevision.objects.count() == 1


def test_a_locality_of_another_city_has_its_own_code_and_message(places):
    client = signed_in()
    created = draft(client)
    response = edit(client, created, {"city": places["karachi"], "locality": places["l1"]})
    assert response.status_code == 400
    assert field_codes(response) == {
        "locality": ["Choose a locality that belongs to the selected city."]
    }
    assert field_message("locality_not_in_city") != field_message("invalid")
    assert places["l1"] not in body_text(response)


def test_changing_the_city_clears_the_locality_over_http(places):
    client = signed_in()
    created = draft(client)
    saved = edit(client, created, {"city": places["karachi"], "locality": places["k1"]}).json()
    moved = edit(client, saved, {"city": places["lahore"]}).json()
    assert moved["fields"]["city"] == places["lahore"] and moved["fields"]["locality"] is None


def test_a_stale_write_returns_the_owners_current_with_the_private_values_only_to_the_owner(places):
    client = signed_in()
    created = draft(client)
    saved = edit(client, created, PRIVATE).json()
    stale = edit(client, created, {"street": "Plum Road"}, base=1)
    assert (stale.status_code, code(stale)) == (409, "stale_revision")
    assert (
        stale.json()["current"] == saved and stale.json()["current"]["fields"]["street"] == STREET
    )
    other = signed_in("other.person@example.com")
    refused = edit(other, created, {"street": "Plum Road"}, base=1)
    assert refused.status_code == 404
    assert not any(n in body_text(refused) for n in NEEDLES)


def admin_client():
    identity_services.provision_admin(SYSTEM, "root.admin@example.com", ADMIN_PASSWORD)
    return sign_in("root.admin@example.com", admin=True)


def test_canary_private_values_stay_out_of_every_non_owner_http_path_and_the_logs(places, caplog):
    """HTTP-level canary: the owner saves private values, the Listing is then published, and every
    other viewer's reads, the real public image and the logs carry none of them. (That the public
    PROJECTION omits them is proven at the service level, `tests/listings/test_location.py`.)"""
    owner = signed_in()
    created = draft(owner)
    image = upload(owner, created["id"], PNG).json()
    created = owner.get(detail(created["id"])).json()  # the upload moved the revision
    sink = io.StringIO()
    stream = logging.StreamHandler(sink)
    stream.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(stream)
    caplog.set_level(logging.DEBUG)
    try:
        saved = edit(owner, created, {**PRIVATE, "city": places["karachi"]})
        assert saved.status_code == 200
        saved = saved.json()
        invalid = edit(
            owner,
            saved,
            {"street": f"{STREET} 0300 1234567", "latitude": LAT + "9", "longitude": LON},
        )
        stale = edit(owner, saved, {"street": STREET, "block": "B"}, base=1)
        other = signed_in("other.person@example.com")
        admin = admin_client()
        visitor = Client()
        listing_url = detail(created["id"])
        set_listing_visibility(created["id"], "published")  # the owner is Active
        reads = {
            "other get": other.get(listing_url),
            "other patch": edit(other, saved, {"street": STREET}),
            "admin get": admin.get(listing_url),
            "visitor get": visitor.get(listing_url),
            "other list": other.get(MINE),
            "admin list": admin.get(MINE),
            "visitor list": visitor.get(MINE),
        }
        public_image = visitor.get(image["url"])
    finally:
        root.removeHandler(stream)
    assert invalid.status_code == 400 and stale.status_code == 409
    # exactly 404 for another account and an administrator, 401 for a Visitor (the route needs a
    # session), whatever the body
    expected = {
        "other get": 404,
        "other patch": 404,
        "admin get": 404,
        "visitor get": 401,
        "other list": 200,  # their own, empty, list
        "admin list": 403,
        "visitor list": 401,
    }
    for label, response in reads.items():
        assert response.status_code == expected[label], label
        assert not any(n in body_text(response) for n in NEEDLES), label
        assert not any(n in str(dict(response.items())) for n in NEEDLES), label
    assert reads["other list"].json()["results"] == []
    # the REAL public image of the now published Listing: served to a Visitor, nothing private
    assert public_image.status_code == 200
    assert public_image["Cache-Control"] == "public, max-age=60"
    assert not any(n in str(dict(public_image.items())) for n in NEEDLES)
    assert not any(n.encode() in public_image.content for n in NEEDLES)
    assert not any(n in body_text(invalid) for n in NEEDLES)
    # logs (every record's whole __dict__ too), revision rows
    records = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    for surface in (sink.getvalue(), caplog.text, records):
        assert not any(n in surface for n in NEEDLES)
    assert not any(n in str([r.changes for r in ListingRevision.objects.all()]) for n in NEEDLES)
    # the owner still sees the values
    assert owner.get(detail(created["id"])).json()["fields"]["street"] == STREET


def test_openapi_documents_the_location_without_examples_of_private_values(tmp_path):
    target = tmp_path / "o.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    text = target.read_text()
    schema = yaml.safe_load(text)
    for needle in NEEDLES:
        assert needle not in text
    fields = schema["components"]["schemas"]["ListingFields"]["properties"]
    assert fields["city"]["format"] == "uuid" and fields["locality"]["format"] == "uuid"
    for name in PRIVATE_ADDRESS_IDS:
        assert name in fields
    registry_field = schema["components"]["schemas"]["RegistryField"]["properties"]
    assert "reference" in registry_field
    for name in ("city", "locality"):
        assert "enum" not in fields[name]
    assert registry.field("city").reference == "city"
