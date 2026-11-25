"""The Listing lifecycle over HTTP (Story 2.17), with Django's CSRF checks on: the status route,
the counts route, the status filter, editing a Published Listing and the owner shape."""

import json
import uuid
from types import SimpleNamespace

import pytest
import yaml
from django.core.management import call_command
from django.test import Client

from apps.identity import services as identity_services
from apps.kernel.errors import FIELD_MESSAGES, field_message, get_error
from apps.listings import publication
from apps.listings.models import ListingImage, PropertyListing, PublicationEvent
from tests.api.test_listings_api import (
    ADMIN_PASSWORD,
    CSRF,
    MINE,
    WIRE_KEYS,
    detail,
    sign_in,
    signed_in,
)
from tests.listings.support import (
    HOUSE,
    RENT_HOUSE,
    SYSTEM,
    ready_draft,
    seed_cap,
    seed_media_policies,
    set_account_state,
    set_listing_visibility,
)

COUNTS = f"{MINE}/counts"
ENVELOPE = {"code", "message", "fields", "request_id"}
ALL = ("draft", "published", "archived", "sold", "rented", "removed")
_keys = iter(f"key-{n:04d}" for n in range(1, 10_000))


@pytest.fixture(autouse=True)
def setup(db):
    seed_cap()
    seed_media_policies()


def csrf(client) -> dict:
    return {"HTTP_X_CSRFTOKEN": client.get(CSRF).json()["csrf_token"]}


def status_url(listing_id):
    return f"{detail(listing_id)}/status"


def change(client, listing_id, base, target, token=True, body=None):
    extra = csrf(client) if token else {}
    body = {"base_revision": base, "status": target} if body is None else body
    return client.post(
        status_url(listing_id), json.dumps(body), content_type="application/json", **extra
    )


def published(client, values=HOUSE):
    """A Published Listing of the signed-in account, as the owner resource."""
    view = ready_draft(SimpleNamespace(id=client.account_id), values)
    publication.publish(SimpleNamespace_actor(client), view.id, view.revision, next(_keys))
    return client.get(detail(view.id)).json()


def SimpleNamespace_actor(client):
    from tests.listings.support import user

    return user(SimpleNamespace(id=client.account_id))


def refused(response, status, error_code):
    assert (response.status_code, response.json()["error"]["code"]) == (status, error_code)
    assert set(response.json()["error"]) == ENVELOPE
    assert response["Cache-Control"] == "no-store"
    return response.json()


def test_archiving_returns_the_owner_listing_and_takes_it_off_the_public_route():
    client = signed_in()
    listing = published(client)
    anonymous = Client()
    image = listing["media"][0]["url"]  # (the file itself is not stored by these fixtures)
    response = change(client, listing["id"], listing["revision"], "archived")
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    body = response.json()
    assert set(body) == WIRE_KEYS
    assert (body["status"], body["visibility"], body["revision"]) == (
        "archived",
        "private",
        listing["revision"] + 1,
    )
    assert (body["restricted"], body["restriction_reason"]) == (False, None)
    assert client.get(detail(listing["id"])).json() == body
    image = body["media"][0]
    assert anonymous.get(image["url"]).status_code == 404  # no longer public
    assert client.get(detail(listing["id"])).status_code == 200  # the owner still reads it


@pytest.mark.parametrize("target", ["sold", "rented"])
def test_sold_and_rented_are_recorded_and_can_then_be_archived(target):
    client = signed_in()
    listing = published(client, RENT_HOUSE if target == "rented" else HOUSE)
    done = change(client, listing["id"], listing["revision"], target).json()
    assert done["status"] == target
    again = change(client, listing["id"], done["revision"], "archived").json()
    assert again["status"] == "archived"
    assert PublicationEvent.objects.count() == 1  # the publication only


def test_the_body_is_checked_and_nothing_is_written_on_a_refusal():
    client = signed_in()
    listing = published(client)
    for body in (
        {"base_revision": listing["revision"], "status": "published"},
        {"base_revision": listing["revision"], "status": "removed"},
        {"base_revision": listing["revision"], "status": "nope"},
        {"base_revision": listing["revision"]},
        {"status": "archived"},
        {"base_revision": "x", "status": "archived"},
        {"base_revision": listing["revision"], "status": "archived", "extra": 1},
        [],
    ):
        response = change(client, listing["id"], None, None, body=body)
        assert response.status_code == 400, body
        assert response.json()["error"]["code"] == "validation_error"
    row = PropertyListing.objects.get()
    assert (row.status, row.revision) == ("published", listing["revision"])


def test_a_stale_revision_is_409_with_the_current_listing():
    client = signed_in()
    listing = published(client)
    body = refused(
        change(client, listing["id"], listing["revision"] + 5, "archived"), 409, "stale_revision"
    )
    assert body["current"]["revision"] == listing["revision"]
    assert body["current"]["status"] == "published"


def test_a_forbidden_move_is_409_conflict_with_nothing_written():
    client = signed_in()
    listing = published(client)
    done = change(client, listing["id"], listing["revision"], "archived").json()
    refused(change(client, listing["id"], done["revision"], "archived"), 409, "conflict")
    refused(change(client, listing["id"], done["revision"], "sold"), 409, "conflict")
    assert PropertyListing.objects.get().revision == done["revision"]


def test_csrf_and_sign_in_are_required():
    client = signed_in()
    listing = published(client)
    refused(
        change(client, listing["id"], listing["revision"], "archived", token=False),
        403,
        "forbidden",
    )
    anonymous = Client(enforce_csrf_checks=True)
    response = anonymous.post(
        status_url(listing["id"]),
        json.dumps({"base_revision": 1, "status": "archived"}),
        content_type="application/json",
    )
    refused(response, 401, "unauthenticated")
    assert PropertyListing.objects.get().status == "published"


def test_another_account_and_an_administrator_get_404():
    client = signed_in()
    listing = published(client)
    other = signed_in("other.owner@example.com")
    refused(change(other, listing["id"], 1, "archived"), 404, "not_found")
    identity_services.provision_admin(SYSTEM, "root.admin@example.com", ADMIN_PASSWORD)
    admin = sign_in("root.admin@example.com", admin=True)
    refused(change(admin, listing["id"], 1, "archived"), 404, "not_found")
    refused(change(client, "nope", 1, "archived"), 404, "not_found")
    assert PropertyListing.objects.get().status == "published"


def test_a_suspended_owner_may_only_archive():
    client = signed_in()
    listing = published(client)
    set_account_state(client.account_id, "suspended")
    refused(change(client, listing["id"], listing["revision"], "sold"), 403, "forbidden")
    refused(change(client, listing["id"], listing["revision"], "rented"), 403, "forbidden")
    assert change(client, listing["id"], listing["revision"], "archived").status_code == 200


def test_an_email_unverified_owner_may_use_all_three():
    client = signed_in()
    listing = published(client, RENT_HOUSE)
    set_account_state(client.account_id, "unverified")
    assert change(client, listing["id"], listing["revision"], "rented").status_code == 200


# --- counts and the list filter ---------------------------------------------------------------


def test_the_counts_cover_every_status_for_the_caller_only():
    client = signed_in()
    a, b = published(client), published(client)
    change(client, b["id"], b["revision"], "sold")
    set_listing_visibility(ready_draft(SimpleNamespace(id=client.account_id)).id, "removed")
    response = client.get(COUNTS)
    assert response.status_code == 200 and response["Cache-Control"] == "no-store"
    assert response.json() == {
        "draft": 0,
        "published": 1,
        "archived": 0,
        "sold": 1,
        "rented": 0,
        "removed": 1,
    }
    assert list(response.json()) == list(ALL)
    other = signed_in("other.owner@example.com")
    assert set(other.get(COUNTS).json().values()) == {0}
    assert a["status"] == "published"


def test_the_counts_follow_the_same_guards_as_the_list():
    anonymous = Client(enforce_csrf_checks=True)
    refused(anonymous.get(COUNTS), 401, "unauthenticated")
    identity_services.provision_admin(SYSTEM, "root.admin@example.com", ADMIN_PASSWORD)
    admin = sign_in("root.admin@example.com", admin=True)
    refused(admin.get(COUNTS), 403, "forbidden")
    suspended = signed_in("sus.owner@example.com", state="suspended")
    assert suspended.get(COUNTS).status_code == 200


@pytest.mark.parametrize("state", ALL)
def test_the_list_filters_by_each_status_and_refuses_an_unknown_one(state):
    client = signed_in()
    listing = published(client)
    set_listing_visibility(listing["id"], state)
    other = ready_draft(SimpleNamespace(id=client.account_id))
    response = client.get(MINE, {"status": state})
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["results"]]
    assert (listing["id"] in ids) and ((str(other.id) in ids) == (state == "draft"))
    bad = client.get(MINE, {"status": "pending"})
    assert bad.status_code == 400 and bad.json()["error"]["fields"] == {
        "status": [field_message("invalid_choice")]
    }


def test_a_restricted_and_a_removed_listing_show_their_reason_to_the_owner_only():
    client = signed_in()
    listing = published(client)
    PropertyListing.objects.filter(id=listing["id"]).update(
        status="removed", restricted=True, restriction_reason="short reason"
    )
    for url in (detail(listing["id"]),):
        body = client.get(url).json()
        assert (body["status"], body["restricted"], body["restriction_reason"]) == (
            "removed",
            True,
            "short reason",
        )
    [item] = client.get(MINE, {"status": "removed"}).json()["results"]
    assert item["restriction_reason"] == "short reason"
    preview = client.get(f"{detail(listing['id'])}/preview")
    assert preview.status_code == 200 and "short reason" not in preview.content.decode()
    anonymous = Client()
    image = item["media"][0]["url"]
    assert anonymous.get(image).status_code == 404
    response = change(client, listing["id"], item["revision"], "archived")
    refused(response, 403, "forbidden")


# --- editing over HTTP ------------------------------------------------------------------------


def patch(client, listing, fields, base=None):
    body = {"base_revision": listing["revision"] if base is None else base, "fields": fields}
    return client.patch(
        detail(listing["id"]), json.dumps(body), content_type="application/json", **csrf(client)
    )


def test_a_published_listing_is_edited_over_http_and_the_public_sees_it():
    client = signed_in()
    listing = published(client)
    response = patch(client, listing, {"title": "A different family house"})
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["visibility"], body["revision"]) == (
        "published",
        "public",
        listing["revision"] + 1,
    )
    assert PublicationEvent.objects.count() == 1


def test_an_identity_edit_of_a_published_listing_is_400_not_editable_on_that_field():
    client = signed_in()
    listing = published(client)
    response = patch(client, listing, {"area_value": "11", "title": "A different family house"})
    body = refused(response, 400, "validation_error")
    assert body["error"]["fields"] == {"area_value": [field_message("not_editable")]}
    assert FIELD_MESSAGES["not_editable"] == field_message("not_editable")
    assert client.get(detail(listing["id"])).json()["revision"] == listing["revision"]


def test_an_edit_that_would_leave_a_published_listing_invalid_names_every_item():
    client = signed_in()
    listing = published(client)
    body = refused(
        patch(client, listing, {"title": None, "asking_price": None}), 400, "validation_error"
    )
    assert body["error"]["fields"] == {
        "title": [field_message("required")],
        "asking_price": [field_message("required")],
    }
    assert client.get(detail(listing["id"])).json()["revision"] == listing["revision"]


def test_the_last_image_of_a_published_listing_cannot_be_removed_over_http():
    client = signed_in()
    listing = published(client)
    [image] = listing["media"]
    response = client.delete(f"{detail(listing['id'])}/images/{image['id']}", **csrf(client))
    body = refused(response, 400, "validation_error")
    assert body["error"]["fields"] == {"images": [field_message("images_required")]}
    assert ListingImage.objects.filter(listing_id=listing["id"]).count() == 1


@pytest.mark.parametrize("state", ["sold", "rented", "removed"])
def test_a_view_only_listing_refuses_edits_and_image_changes_with_403(state):
    client = signed_in()
    listing = published(client)
    set_listing_visibility(listing["id"], state)
    refused(patch(client, listing, {"title": "A different family house"}), 403, "forbidden")
    [image] = listing["media"]
    response = client.delete(f"{detail(listing['id'])}/images/{image['id']}", **csrf(client))
    refused(response, 403, "forbidden")


def test_an_archived_listing_may_be_edited_incomplete():
    client = signed_in()
    listing = published(client)
    done = change(client, listing["id"], listing["revision"], "archived").json()
    response = patch(client, done, {"title": None, "area_value": "11"})
    assert response.status_code == 200 and response.json()["status"] == "archived"


# --- the contract ------------------------------------------------------------------------------


def test_the_openapi_documents_the_new_routes_and_shapes(tmp_path):
    out = tmp_path / "schema.yaml"
    call_command("spectacular", "--file", str(out))
    spec = yaml.safe_load(out.read_text())
    status_op = spec["paths"]["/api/v1/listings/{listing_id}/status"]["post"]
    assert {"200", "400", "401", "403", "404", "409"} <= set(status_op["responses"])
    assert "/api/v1/me/listings/counts" in spec["paths"]
    listing = spec["components"]["schemas"]["Listing"]
    assert {"restricted", "restriction_reason"} <= set(listing["properties"])
    assert set(spec["components"]["schemas"]["ListingStatusEnum"]["enum"]) == set(ALL)


def test_no_new_error_code_and_no_new_field_sentence_was_needed():
    # every code and sentence the new behaviour uses already exists (the frontend mirrors them)
    for code in ("conflict", "stale_revision", "forbidden", "validation_error", "not_found"):
        assert get_error(code)
    for sentence in ("not_editable", "images_required", "required", "invalid_choice"):
        assert sentence in FIELD_MESSAGES


# --- review batch: events over HTTP and the Suspended corners -----------------------------------


@pytest.mark.parametrize("target", ["archived", "sold", "rented"])
def test_each_move_over_http_emits_one_event_after_commit(
    target, monkeypatch, django_capture_on_commit_callbacks
):
    from apps.kernel import events
    from apps.listings.events import ListingStatusChanged

    monkeypatch.setattr(events, "_registry", {})
    seen = []
    events.subscribe(ListingStatusChanged, seen.append)
    client = signed_in()
    listing = published(client, RENT_HOUSE if target == "rented" else HOUSE)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response = change(client, listing["id"], listing["revision"], target)
    assert response.status_code == 200 and len(callbacks) == 1
    assert [(e.listing_id, e.account_id, e.status) for e in seen] == [
        (uuid.UUID(listing["id"]), client.account_id, target)
    ]


def test_a_refused_move_over_http_schedules_no_event(
    monkeypatch, django_capture_on_commit_callbacks
):
    client = signed_in()
    listing = published(client)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        refused(change(client, listing["id"], 99, "archived"), 409, "stale_revision")
        refused(change(client, listing["id"], listing["revision"], "rented"), 409, "conflict")
    assert callbacks == []


def test_a_suspended_owner_reads_lists_and_previews_but_cannot_edit_any_editable_status():
    client = signed_in()
    live = published(client)
    archived = published(client)
    archived = change(client, archived["id"], archived["revision"], "archived").json()
    draft = ready_draft(SimpleNamespace(id=client.account_id))
    set_account_state(client.account_id, "suspended")
    for listing in (live, archived):
        assert client.get(detail(listing["id"])).status_code == 200
        assert client.get(f"{detail(listing['id'])}/preview").status_code == 200
    assert client.get(f"{detail(draft.id)}/preview").status_code == 200
    ids = {item["id"] for item in client.get(MINE).json()["results"]}
    assert {live["id"], archived["id"], str(draft.id)} <= ids
    assert client.get(COUNTS).status_code == 200
    for listing_id, revision in (
        (live["id"], live["revision"]),
        (archived["id"], archived["revision"] + 0),
        (str(draft.id), draft.revision),
    ):
        response = patch(client, {"id": listing_id, "revision": revision}, {"title": "A title"})
        refused(response, 403, "forbidden")
    refused(change(client, live["id"], live["revision"], "sold"), 403, "forbidden")
    done = change(client, live["id"], live["revision"], "archived")
    assert done.status_code == 200 and done.json()["status"] == "archived"
