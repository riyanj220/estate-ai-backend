"""`listings.selectors.preview_of` (Story 2.13): the owner's view of the public projection."""

import dataclasses
import json

import pytest

from apps.identity.models import IndividualProfile
from apps.kernel import runtime
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.listings import selectors, services
from apps.listings.models import ListingImage
from tests.imaging import make_image
from tests.listings.support import (
    HOUSE,
    make_account,
    random_email,
    seed_cap,
    seed_media_policies,
    set_account_state,
    set_listing_visibility,
    user,
)
from tests.listings.test_public_projection import plain

PNG = make_image("PNG", (400, 300))
STREET, HOUSE_NUMBER, LAT, LNG = "Mango Lane", "Plum Twelve", "31.512345", "74.398765"
PHONE = "+923001234567"
PRIVATE_VALUES = (STREET, HOUSE_NUMBER, LAT, LNG, PHONE, "923001234567", "03001234567")
PRIVATE_KEYS = ("street", "house_number", "latitude", "longitude", "phone", "revision", "status")


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


def draft_with_everything(images=2):
    owner = make_account(random_email())
    IndividualProfile.objects.filter(account_id=owner.id).update(phone=PHONE)
    view = services.create_draft(user(owner), *HOUSE)
    services.apply_updates(
        user(owner),
        view.id,
        view.revision,
        {
            "title": "Quiet corner house",
            "bedrooms": 3,
            "asking_price": "1500000.00",
            "street": STREET,
            "house_number": HOUSE_NUMBER,
            "latitude": LAT,
            "longitude": LNG,
            "society": "Gulberg",
        },
    )
    for _ in range(images):
        services.upload_image(user(owner), view.id, PNG)
    return owner, view


def code_of(call, *args):
    with pytest.raises(DomainError) as caught:
        call(*args)
    return caught.value.code


def test_the_preview_of_a_draft_is_exactly_the_public_projection_of_it():
    owner, view = draft_with_everything()
    shown = selectors.preview_of(user(owner), view.id)
    assert isinstance(shown, selectors.PublicListingView)
    set_listing_visibility(view.id, "published")  # now the public sees the very same thing
    public = selectors.public_listing(Actor.visitor("r"), view.id)
    assert plain(shown) == plain(public)
    assert {f.name for f in dataclasses.fields(shown)} == {
        f.name for f in dataclasses.fields(public)
    }
    assert set(shown.fields) == set(public.fields)


def test_the_preview_of_a_published_listing_is_allowed_even_when_it_is_not_public():
    owner, view = draft_with_everything(images=0)
    set_listing_visibility(view.id, "published", restricted=True)
    assert code_of(selectors.public_listing, Actor.visitor("r"), view.id) == "not_found"
    assert selectors.preview_of(user(owner), view.id).id == view.id


def test_no_private_value_phone_or_internal_key_is_in_the_preview():
    owner, view = draft_with_everything()
    shown = selectors.preview_of(user(owner), view.id)
    text = json.dumps(plain(shown), default=str)
    for value in PRIVATE_VALUES:
        assert value not in text, value
    for name in PRIVATE_KEYS:
        assert f'"{name}"' not in text, name
    assert str(owner.id) not in text
    for link in ListingImage.objects.filter(listing_id=view.id):
        assert str(link.asset_id) not in text
    assert shown.fields["bedrooms"] == 3 and shown.title == "Quiet corner house"


def test_the_preview_never_reads_the_private_address(django_assert_max_num_queries):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    owner, view = draft_with_everything()
    with CaptureQueriesContext(connection) as queries:
        selectors.preview_of(user(owner), view.id)
    assert not any("listings_privateaddress" in q["sql"] for q in queries.captured_queries)


def test_the_preview_uses_the_public_projection_function(monkeypatch):
    owner, view = draft_with_everything(images=0)
    calls = []

    def marker(values, definition=None):
        calls.append(set(values))
        return {"marker": 1}

    monkeypatch.setattr(selectors, "public_fields", marker)
    assert dict(selectors.preview_of(user(owner), view.id).fields) == {"marker": 1}
    assert calls  # the one field rule, not a copy of it


def test_the_images_are_in_the_public_shape_with_the_media_route():
    owner, view = draft_with_everything(images=2)
    shown = selectors.preview_of(user(owner), view.id)
    assert [(m.position, m.is_cover) for m in shown.media] == [(0, True), (1, False)]
    for image in shown.media:
        assert isinstance(image, selectors.PublicImageView)
        assert image.url == f"/api/v1/listings/{view.id}/images/{image.id}/file"
        # the owner-only route serves a Draft's image to the owner, and to nobody else
        served = selectors.image_for_serving(user(owner), view.id, image.id)
        assert served.public is False
        assert (
            code_of(selectors.image_for_serving, Actor.visitor("r"), view.id, image.id)
            == "not_found"
        )


@pytest.mark.parametrize("state", ["active", "unverified", "suspended"])
def test_active_unverified_and_suspended_owners_may_preview(state):
    owner = make_account(random_email())
    view = services.create_draft(user(owner), *HOUSE)
    set_account_state(owner.id, state)
    assert selectors.preview_of(user(owner), view.id).id == view.id


def test_a_deactivated_owner_is_not_found():
    owner, view = draft_with_everything(images=0)
    set_account_state(owner.id, "deactivated")
    assert code_of(selectors.preview_of, user(owner), view.id) == "not_found"


def test_everyone_else_gets_the_same_not_found():
    owner, view = draft_with_everything(images=0)
    stranger = make_account(random_email())
    for actor in (
        user(stranger),
        Actor.admin(stranger.id, "r"),
        Actor.visitor("r"),
        Actor.system("r"),
    ):
        assert code_of(selectors.preview_of, actor, view.id) == "not_found"
    for bad in ("not-a-uuid", "", None, 5, "00000000-0000-4000-8000-000000000000"):
        assert code_of(selectors.preview_of, user(owner), bad) == "not_found"


def test_a_status_the_owner_may_not_see_is_not_found(monkeypatch):
    from apps.listings import policy

    owner, view = draft_with_everything(images=0)
    monkeypatch.setattr(policy, "OWNER_VISIBLE_STATUSES", frozenset({"published"}))
    assert code_of(selectors.preview_of, user(owner), view.id) == "not_found"


def test_a_preview_of_an_incomplete_draft_shows_nulls_not_errors():
    owner = make_account(random_email())
    view = services.create_draft(user(owner), *HOUSE)
    shown = selectors.preview_of(user(owner), view.id)
    assert shown.title is None and shown.description is None and shown.media == ()
    assert all(value is None for value in shown.fields.values())
