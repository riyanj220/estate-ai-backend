"""Preview, delete and open tracking of Drafts over HTTP (Story 2.13), with CSRF checks on."""

import json
import uuid

import pytest
import yaml
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command

from apps.identity import services as identity_services
from apps.identity.models import IndividualProfile
from apps.kernel import runtime
from apps.kernel.errors import get_error
from apps.kernel.models import AuditRecord, MediaAsset, StorageUsage
from apps.listings.models import ListingImage, ListingRevision, PropertyListing
from tests.api.test_listings_api import (
    ADMIN_PASSWORD,
    CREATE,
    CSRF,
    MINE,
    code,
    detail,
    post,
    sign_in,
    signed_in,
)
from tests.imaging import make_image
from tests.listings.support import (
    SYSTEM,
    make_account,
    seed_cap,
    seed_media_policies,
    set_account_state,
    set_listing_visibility,
)

JPEG = make_image("JPEG", (1200, 800))
PHONE = "+923001234567"
STREET, HOUSE_NUMBER, LAT, LNG = "Mango Lane", "Plum Twelve", "31.512345", "74.398765"
PUBLIC_KEYS = {
    "id", "purpose", "category", "subtype", "title", "description", "fields", "media",
    "registry_version", "estimate",
}  # fmt: skip
IMAGE_KEYS = {"id", "position", "is_cover", "content_type", "width", "height", "url"}


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


def preview_url(listing_id):
    return f"{detail(listing_id)}/preview"


def csrf(client) -> dict:
    return {"HTTP_X_CSRFTOKEN": client.get(CSRF).json()["csrf_token"]}


def delete(client, listing_id, token=True):
    return client.delete(detail(listing_id), **(csrf(client) if token else {}))


def patch(client, listing_id, body):
    return client.patch(
        detail(listing_id), json.dumps(body), content_type="application/json", **csrf(client)
    )


def upload(client, listing_id):
    body = {"file": SimpleUploadedFile("p.jpg", JPEG, content_type="image/jpeg")}
    return client.post(f"{detail(listing_id)}/images", body, **csrf(client))


def new_draft(client):
    return post(client, CREATE).json()


def filled_draft(client):
    listing = new_draft(client)
    response = patch(
        client,
        listing["id"],
        {
            "base_revision": 1,
            "fields": {
                "title": "Quiet corner house",
                "bedrooms": 3,
                "street": STREET,
                "house_number": HOUSE_NUMBER,
                "latitude": LAT,
                "longitude": LNG,
            },
        },
    )
    assert response.status_code == 200, response.content
    assert upload(client, listing["id"]).status_code == 201
    return listing


def expected_error(response, error):
    spec = get_error(error)
    return {
        "error": {
            "code": error,
            "message": spec.message,
            "fields": {},
            "request_id": response["X-Request-ID"],
        }
    }


def refused(response, status, error):
    assert response.status_code == status, response.content
    assert response.json() == expected_error(response, error)
    assert response["Cache-Control"] == "no-store"


# --- preview -----------------------------------------------------------------------------------


def test_the_owner_previews_a_draft_in_the_public_shape_with_a_null_estimate():
    client = signed_in()
    IndividualProfile.objects.filter(account_id=client.account_id).update(phone=PHONE)
    listing = filled_draft(client)
    response = client.get(preview_url(listing["id"]))
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    body = response.json()
    assert set(body) == PUBLIC_KEYS and body["estimate"] is None
    assert (body["id"], body["title"]) == (listing["id"], "Quiet corner house")
    assert body["fields"]["bedrooms"] == 3
    [image] = body["media"]
    assert set(image) == IMAGE_KEYS
    assert image["url"] == f"/api/v1/listings/{listing['id']}/images/{image['id']}/file"
    # the owner's own image route serves that image to the owner
    served = client.get(image["url"])
    assert served.status_code == 200 and served["Cache-Control"] == "private, no-store"


def test_the_preview_never_carries_a_private_value_a_phone_or_owner_data():
    client = signed_in()
    IndividualProfile.objects.filter(account_id=client.account_id).update(phone=PHONE)
    listing = filled_draft(client)
    response = client.get(preview_url(listing["id"]))
    text = response.content.decode()
    for value in (STREET, HOUSE_NUMBER, LAT, LNG, PHONE, "923001234567", str(client.account_id)):
        assert value not in text, value
    body = response.json()
    for key in ("street", "house_number", "latitude", "longitude", "phone"):
        assert key not in body["fields"] and key not in body
    for key in ("status", "revision", "provenance", "visibility", "owner_account_id"):
        assert key not in body
    for link in ListingImage.objects.filter(listing_id=listing["id"]):
        assert str(link.asset_id) not in text
    assert MediaAsset.objects.get().storage_key not in text


def test_the_preview_fields_are_the_public_fields_of_the_registry():
    from apps.catalog import registry
    from apps.listings.fields import WIRE_FIELDS

    client = signed_in()
    body = client.get(preview_url(new_draft(client)["id"])).json()
    expected = {
        name
        for name in WIRE_FIELDS
        if name not in ("purpose", "category", "subtype", "title", "description")
        and registry.field(name).field_class == "public"
    }
    assert set(body["fields"]) == expected
    assert not {"street", "house_number", "latitude", "longitude"} & set(body["fields"])


def test_the_preview_of_a_published_listing_is_allowed():
    client = signed_in()
    listing = new_draft(client)
    set_listing_visibility(listing["id"], "published")
    assert client.get(preview_url(listing["id"])).status_code == 200


def test_everyone_else_gets_the_same_404_and_a_visitor_401():
    client = signed_in()
    listing = new_draft(client)
    make_account("other.owner@example.com")
    other = sign_in("other.owner@example.com")
    identity_services.provision_admin(SYSTEM, "root.admin@example.com", ADMIN_PASSWORD)
    admin = sign_in("root.admin@example.com", admin=True)
    missing = preview_url(uuid.uuid4())
    answers = [
        other.get(preview_url(listing["id"])),
        admin.get(preview_url(listing["id"])),
        client.get(missing),
        client.get(preview_url("not-a-uuid")),
    ]
    for response in answers:
        assert response.status_code == 404 and response["Cache-Control"] == "no-store"
        assert response.json()["error"]["code"] == "not_found"
        assert response.json()["error"]["message"] == get_error("not_found").message
    assert len({json.dumps({**r.json()["error"], "request_id": ""}) for r in answers}) == 1
    from django.test import Client

    visitor = Client(enforce_csrf_checks=True).get(preview_url(listing["id"]))
    refused(visitor, 401, "unauthenticated")


@pytest.mark.parametrize("state", ["unverified", "suspended"])
def test_unverified_and_suspended_owners_may_preview(state):
    client = signed_in(state="active")
    listing = new_draft(client)
    set_account_state(client.account_id, state)
    assert client.get(preview_url(listing["id"])).status_code == 200


def test_a_deactivated_owner_is_refused_like_the_rest():
    client = signed_in()
    listing = new_draft(client)
    set_account_state(client.account_id, "deactivated")
    # a Deactivated account's session is gone: the same 401 as a Visitor
    refused(client.get(preview_url(listing["id"])), 401, "unauthenticated")


def test_the_preview_is_get_only():
    client = signed_in()
    listing = new_draft(client)
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)(preview_url(listing["id"]), **csrf(client))
        assert response.status_code == 405


# --- opening ------------------------------------------------------------------------------------


def test_reading_and_previewing_record_an_open_without_touching_the_revision():
    client = signed_in()
    listing = new_draft(client)
    key = uuid.UUID(listing["id"])
    before = PropertyListing.objects.get(id=key)
    assert before.last_opened_at is None
    assert client.get(detail(listing["id"])).status_code == 200
    first = PropertyListing.objects.get(id=key)
    assert first.last_opened_at is not None
    assert (first.revision, first.updated_at) == (before.revision, before.updated_at)
    assert client.get(preview_url(listing["id"])).status_code == 200
    assert PropertyListing.objects.get(id=key).last_opened_at == first.last_opened_at  # hourly
    PropertyListing.objects.filter(id=key).update(last_opened_at=None)
    assert client.get(preview_url(listing["id"])).status_code == 200
    assert PropertyListing.objects.get(id=key).last_opened_at is not None
    assert ListingRevision.objects.filter(listing_id=key).count() == 1


def test_a_refused_read_records_nothing():
    client = signed_in()
    listing = new_draft(client)
    make_account("other.owner@example.com")
    other = sign_in("other.owner@example.com")
    assert other.get(detail(listing["id"])).status_code == 404
    assert other.get(preview_url(listing["id"])).status_code == 404
    assert PropertyListing.objects.get(id=listing["id"]).last_opened_at is None


def test_the_open_never_leaks_into_the_wire_shape():
    client = signed_in()
    body = client.get(detail(new_draft(client)["id"])).json()
    text = json.dumps(body)
    for name in ("last_opened_at", "expiry_notice_sent_at", "delete_after"):
        assert name not in text


# --- delete -------------------------------------------------------------------------------------


def test_the_owner_deletes_a_draft_with_its_images(media_root, django_capture_on_commit_callbacks):
    client = signed_in()
    listing = filled_draft(client)
    with django_capture_on_commit_callbacks(execute=True):
        response = delete(client, listing["id"])
    assert response.status_code == 204 and response.content == b""
    assert response["Cache-Control"] == "no-store"
    assert not PropertyListing.objects.exists() and not ListingImage.objects.exists()
    assert not MediaAsset.objects.exists() and not ListingRevision.objects.exists()
    assert StorageUsage.objects.get(pk=client.account_id).bytes_used == 0
    assert [p for p in media_root.rglob("*") if p.is_file()] == []
    record = AuditRecord.objects.get(action="listing.draft_deleted")
    assert record.metadata == {"reason": "owner"} and record.target_id == uuid.UUID(listing["id"])
    for value in ("Quiet corner house", STREET, HOUSE_NUMBER):
        assert value not in str(record.__dict__)


def test_a_repeat_is_404_and_so_is_every_other_caller():
    client = signed_in()
    listing = new_draft(client)
    make_account("other.owner@example.com")
    other = sign_in("other.owner@example.com")
    identity_services.provision_admin(SYSTEM, "root.admin@example.com", ADMIN_PASSWORD)
    admin = sign_in("root.admin@example.com", admin=True)
    for caller in (other, admin):
        refused(delete(caller, listing["id"]), 404, "not_found")
    refused(delete(client, str(uuid.uuid4())), 404, "not_found")
    refused(delete(client, "not-a-uuid"), 404, "not_found")
    assert PropertyListing.objects.filter(id=listing["id"]).exists()
    assert delete(client, listing["id"]).status_code == 204
    refused(delete(client, listing["id"]), 404, "not_found")
    refused(client.get(detail(listing["id"])), 404, "not_found")


def test_a_visitor_is_401_and_nothing_changes():
    from django.test import Client

    client = signed_in()
    listing = new_draft(client)
    visitor = Client(enforce_csrf_checks=True)
    refused(delete(visitor, listing["id"]), 401, "unauthenticated")
    assert PropertyListing.objects.filter(id=listing["id"]).exists()


def test_delete_without_csrf_is_403_and_deletes_nothing():
    client = signed_in()
    listing = new_draft(client)
    response = delete(client, listing["id"], token=False)
    assert (response.status_code, code(response)) == (403, "forbidden")
    assert PropertyListing.objects.filter(id=listing["id"]).exists()


def test_a_published_listing_is_a_409_conflict_and_nothing_changes(media_root):
    client = signed_in()
    listing = filled_draft(client)
    set_listing_visibility(listing["id"], "published")
    response = delete(client, listing["id"])
    refused(response, 409, "conflict")
    assert get_error("conflict").status == 409
    row = PropertyListing.objects.get(id=listing["id"])
    assert row.status == "published" and ListingImage.objects.count() == 1
    assert len([p for p in media_root.rglob("*") if p.is_file()]) == 1
    assert not AuditRecord.objects.filter(action="listing.draft_deleted").exists()


@pytest.mark.parametrize(
    ("state", "status", "error"),
    [("suspended", 403, "forbidden"), ("deactivated", 401, "unauthenticated")],
)
def test_a_suspended_or_deactivated_owner_cannot_delete(state, status, error):
    client = signed_in()
    listing = new_draft(client)
    set_account_state(client.account_id, state)
    refused(delete(client, listing["id"]), status, error)
    assert PropertyListing.objects.filter(id=listing["id"]).exists()


def test_deleting_frees_a_slot_of_the_open_draft_cap():
    seed_cap(1)
    client = signed_in()
    listing = new_draft(client)
    assert post(client, CREATE).status_code == 429
    assert delete(client, listing["id"]).status_code == 204
    assert post(client, CREATE).status_code == 201


# --- an incomplete Draft is saved, listed, reopened and edited -----------------------------------


def test_an_incomplete_draft_with_only_a_purpose_and_a_title_is_listed_reopened_and_edited():
    client = signed_in()
    listing = new_draft(client)
    assert (
        patch(
            client, listing["id"], {"base_revision": 1, "fields": {"title": "Just a title"}}
        ).status_code
        == 200
    )
    listed = client.get(f"{MINE}?status=draft").json()["results"]
    assert [item["id"] for item in listed] == [listing["id"]]
    assert (
        listed[0]["fields"]["title"] == "Just a title" and listed[0]["fields"]["bedrooms"] is None
    )
    reopened = client.get(detail(listing["id"])).json()
    assert reopened["revision"] == 2 and reopened["status"] == "draft"
    assert (
        reopened["fields"]["title"] == "Just a title" and reopened["fields"]["asking_price"] is None
    )
    edited = patch(
        client, listing["id"], {"base_revision": 2, "fields": {"title": "A better title"}}
    )
    assert edited.status_code == 200 and edited.json()["revision"] == 3
    assert client.get(detail(listing["id"])).json()["fields"]["title"] == "A better title"


# --- the contract --------------------------------------------------------------------------------


def test_the_contract_describes_the_preview_and_the_delete(tmp_path):
    out = tmp_path / "schema.yaml"
    call_command("spectacular", "--file", str(out))
    spec = yaml.safe_load(out.read_text())
    paths = spec["paths"]
    preview = paths["/api/v1/listings/{listing_id}/preview"]["get"]
    assert preview["operationId"] == "preview_listing"
    schema = spec["components"]["schemas"]["ListingPreview"]
    assert set(schema["properties"]) == PUBLIC_KEYS
    assert set(schema["required"]) == PUBLIC_KEYS
    assert not {"status", "revision", "provenance", "visibility"} & set(schema["properties"])
    removal = paths["/api/v1/listings/{listing_id}"]["delete"]
    assert removal["operationId"] == "delete_listing"
    assert set(removal["responses"]) >= {"204", "404", "409"}


# --- exact values, drift and a failing bookkeeping write -----------------------------------------


def test_the_preview_values_are_exactly_the_serialised_selector_output():
    from rest_framework.renderers import JSONRenderer

    from apps.kernel.actor import Actor
    from apps.listings import selectors
    from apps.presentation.api.listings import preview_data

    client = signed_in()
    listing = new_draft(client)
    response = patch(
        client,
        listing["id"],
        {
            "base_revision": 1,
            "fields": {
                "title": "Quiet corner house",
                "description": "Near the park.",
                "asking_price": "1500000.00",
                "area_value": "10",
                "area_unit": "marla",
                "marla_basis": "225",
                "society": "Gulberg",
                "street": STREET,
                "amenities": {"elevator": "present", "swimming_pool": "none"},
            },
        },
    )
    assert response.status_code == 200, response.content
    assert upload(client, listing["id"]).status_code == 201
    body = client.get(preview_url(listing["id"])).json()
    shown = selectors.preview_of(Actor.user(client.account_id, "r"), listing["id"])
    assert body == json.loads(JSONRenderer().render(preview_data(shown)))
    assert body["fields"]["asking_price"] == "1500000.00"
    assert body["fields"]["area_value"] == "10.0000" and body["fields"]["area_sqft"] == "2250.0000"
    assert body["fields"]["society"] == "Gulberg"
    assert body["fields"]["amenities"] == {"elevator": "present", "swimming_pool": "none"}
    assert body["description"] == "Near the park." and body["estimate"] is None


def test_the_preview_serializer_fields_equal_the_projection_of_a_full_view():
    from apps.listings import selectors
    from apps.presentation.api.listings import PUBLIC_FIELD_IDS

    client = signed_in()
    listing = new_draft(client)
    from apps.kernel.actor import Actor

    full = selectors.get_owned_listing(Actor.user(client.account_id, "r"), listing["id"])
    assert set(PUBLIC_FIELD_IDS) == set(selectors.public_fields(full.fields))
    assert len(PUBLIC_FIELD_IDS) == len(set(PUBLIC_FIELD_IDS))


@pytest.mark.parametrize("route", ["detail", "preview"])
def test_a_failing_open_write_never_fails_the_read(route, monkeypatch, caplog):
    import logging

    from apps.listings import services

    client = signed_in()
    listing = new_draft(client)

    def boom(*args, **kwargs):
        raise RuntimeError(f"db down {listing['id']}")

    monkeypatch.setattr(services, "record_open", boom)
    url = detail(listing["id"]) if route == "detail" else preview_url(listing["id"])
    with caplog.at_level(logging.ERROR, logger="estate.listings"):
        response = client.get(url)
    assert response.status_code == 200 and response.json()["id"] == listing["id"]
    [record] = [r for r in caplog.records if r.name == "estate.listings"]
    assert record.exception_class == "RuntimeError"
    assert listing["id"] not in f"{record.getMessage()} {record.__dict__}"


def test_a_failing_update_statement_never_fails_the_read(monkeypatch):
    from django.db.models.query import QuerySet

    client = signed_in()
    listing = new_draft(client)
    real = QuerySet.update

    def boom(self, **kwargs):
        if "last_opened_at" in kwargs:
            raise RuntimeError("update failed")
        return real(self, **kwargs)

    monkeypatch.setattr(QuerySet, "update", boom)
    assert client.get(detail(listing["id"])).status_code == 200
    assert client.get(preview_url(listing["id"])).status_code == 200
    assert PropertyListing.objects.get(id=listing["id"]).last_opened_at is None
