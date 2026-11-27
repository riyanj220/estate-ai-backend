"""Story 11.4: the floor plan route, its place beside the images and its removal."""

from types import SimpleNamespace

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart

from apps.identity.models import Account
from apps.kernel import runtime
from apps.kernel.models import MediaAsset, StorageUsage
from apps.listings import publication, selectors, services
from apps.listings.models import ListingFloorPlan, ListingImage, PropertyListing
from tests.api.test_listing_images_api import csrf, new_draft, upload
from tests.api.test_listings_api import detail, signed_in
from tests.imaging import make_image
from tests.listings.support import (
    add_images,
    ready_draft,
    seed_cap,
    seed_media_policies,
    user,
)

PNG = make_image("PNG", (500, 400))
JPEG = make_image("JPEG", (1200, 800))


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


def url(listing_id):
    return f"/api/v1/listings/{listing_id}/floor-plan"


def put(client, listing_id, data=PNG, token=True):
    body = encode_multipart(BOUNDARY, {"file": SimpleUploadedFile("plan.png", data)})
    extra = csrf(client) if token else {}
    return client.put(url(listing_id), body, content_type=MULTIPART_CONTENT, **extra)


def used():
    return StorageUsage.objects.get().bytes_used


def test_add_replace_serve_and_remove(django_capture_on_commit_callbacks, media_root):
    client = signed_in()
    listing = new_draft(client)
    assert listing["floor_plan"] is None
    first = put(client, listing["id"])
    assert first.status_code == 200
    plan = first.json()
    assert set(plan) == {"id", "content_type", "width", "height", "bytes", "url"}
    assert used() == plan["bytes"]
    read = client.get(detail(listing["id"])).json()
    assert read["floor_plan"] == plan and read["media"] == [] and read["revision"] == 2
    served = client.get(plan["url"])
    assert served.status_code == 200 and served["Cache-Control"] == "private, no-store"
    assert Client().get(plan["url"]).status_code == 404  # a Draft: the owner only

    with django_capture_on_commit_callbacks(execute=True):
        second = put(client, listing["id"], JPEG).json()
    assert second["id"] != plan["id"] and used() == second["bytes"]
    assert MediaAsset.objects.count() == ListingFloorPlan.objects.count() == 1
    assert len([p for p in media_root.rglob("*") if p.is_file()]) == 1
    assert client.get(plan["url"]).status_code == 404

    photo = upload(client, listing["id"], PNG).json()
    assert photo["is_cover"] and photo["position"] == 0  # the plan is never the first photo
    preview = client.get(f"{detail(listing['id'])}/preview").json()
    assert preview["floor_plan"]["url"] == second["url"] and "bytes" not in preview["floor_plan"]
    assert [item["id"] for item in preview["media"]] == [photo["id"]]

    with django_capture_on_commit_callbacks(execute=True):
        assert client.delete(url(listing["id"]), **csrf(client)).status_code == 204
    assert client.delete(url(listing["id"]), **csrf(client)).status_code == 404
    assert ListingFloorPlan.objects.count() == 0 and used() == photo["bytes"]
    assert ListingImage.objects.count() == 1


def test_deleting_the_draft_removes_the_floor_plan(django_capture_on_commit_callbacks, media_root):
    client = signed_in()
    listing = new_draft(client)
    assert put(client, listing["id"]).status_code == 200
    with django_capture_on_commit_callbacks(execute=True):
        assert client.delete(detail(listing["id"]), **csrf(client)).status_code == 204
    assert not ListingFloorPlan.objects.exists() and not MediaAsset.objects.exists()
    assert used() == 0 and not [p for p in media_root.rglob("*") if p.is_file()]


def test_refusals_write_nothing():
    owner = signed_in()
    listing = new_draft(owner)
    stranger = signed_in("other.owner@example.com")
    assert put(stranger, listing["id"]).status_code == 404
    assert put(Client(), listing["id"], token=False).status_code == 401
    assert put(owner, listing["id"], token=False).status_code == 403
    bad = put(owner, listing["id"], b"not an image")
    assert bad.status_code == 400 and "file" in bad.json()["error"]["fields"]
    big = put(owner, listing["id"], b"x" * (7 * 1024 * 1024))
    assert big.status_code == 400 and "file" in big.json()["error"]["fields"]
    assert not ListingFloorPlan.objects.exists()
    assert PropertyListing.objects.get(id=listing["id"]).revision == 1


def test_over_the_storage_cap_keeps_the_old_floor_plan():
    client = signed_in()
    listing = new_draft(client)
    kept = put(client, listing["id"]).json()
    seed_media_policies(individual=kept["bytes"] + 10)
    response = put(client, listing["id"], make_image("JPEG", (2000, 1500)))
    assert (response.status_code, response.json()["error"]["code"]) == (
        409,
        "storage_cap_exceeded",
    )
    assert client.get(detail(listing["id"])).json()["floor_plan"] == kept
    assert used() == kept["bytes"]


def test_it_is_public_only_with_a_public_listing_and_is_not_an_image_for_publication():
    client = signed_in()
    account = SimpleNamespace(id=Account.objects.get().id)
    view = ready_draft(account, images=0)
    plan = put(client, view.id).json()
    current = selectors.get_owned_listing(user(account), view.id)
    with pytest.raises(services.FieldsInvalid) as refused:
        publication.publish(user(account), view.id, current.revision, "key-floor-1")
    assert refused.value.fields == {"images": "images_required"}
    add_images(view.id, 1)
    publication.publish(user(account), view.id, current.revision, "key-floor-2")

    public = Client().get(f"/api/v1/public/listings/{view.id}").json()
    assert public["floor_plan"]["url"] == plan["url"]
    served = Client().get(plan["url"])
    assert served.status_code == 200 and "public" in served["Cache-Control"]
    PropertyListing.objects.filter(id=view.id).update(status="archived")
    assert Client().get(f"/api/v1/public/listings/{view.id}").status_code == 404
    assert Client().get(plan["url"]).status_code == 404
