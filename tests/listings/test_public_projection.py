"""The public projection never carries owner-only data (Story 2.12, AD-5, AD-24, AD-26)."""

import dataclasses
import json
from collections.abc import Mapping
from dataclasses import replace

import pytest

from apps.catalog import registry
from apps.kernel import runtime
from apps.kernel.actor import Actor
from apps.listings import selectors, services
from apps.listings.models import ListingImage
from tests.imaging import make_image
from tests.listings.support import (
    HOUSE,
    make_account,
    random_email,
    seed_cap,
    seed_media_policies,
    set_listing_visibility,
    user,
)

PNG = make_image("PNG", (400, 300))
FORBIDDEN_NAMES = (
    "owner",
    "account",
    "asset",
    "storage",
    "status",
    "restricted",
    "provenance",
    "revision",
    "phone",
    "address",
)
TOP_LEVEL = {
    "id", "purpose", "category", "subtype", "title", "description", "fields", "media",
    "registry_version",
}  # fmt: skip


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


def plain(value):
    """A dataclass tree as plain dicts and lists (`asdict` cannot copy a read-only mapping)."""
    if dataclasses.is_dataclass(value):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [plain(v) for v in value]
    return value


def public_one():
    owner = make_account(random_email())
    view = services.create_draft(user(owner), *HOUSE)
    services.apply_updates(
        user(owner), view.id, view.revision, {"title": "Quiet corner house", "bedrooms": 3}
    )
    services.upload_image(user(owner), view.id, PNG)
    services.upload_image(user(owner), view.id, PNG)
    set_listing_visibility(view.id)
    return owner, view


def test_the_projection_has_exactly_the_public_fields():
    owner, view = public_one()
    shown = selectors.public_listing(Actor.visitor("r"), view.id)
    assert {f.name for f in dataclasses.fields(shown)} == TOP_LEVEL
    assert shown.title == "Quiet corner house" and shown.fields["bedrooms"] == 3
    assert (shown.purpose, shown.category, shown.subtype) == HOUSE
    assert [(m.position, m.is_cover) for m in shown.media] == [(0, True), (1, False)]
    for image in shown.media:
        assert {f.name for f in dataclasses.fields(image)} == {
            "id", "position", "is_cover", "content_type", "width", "height", "url",
        }  # fmt: skip
        assert image.url == f"/api/v1/listings/{view.id}/images/{image.id}/file"
    for name in shown.fields:
        assert registry.field(name).field_class == "public"
    # the identity and text fields are top-level only
    assert not {"purpose", "category", "subtype", "title", "description"} & set(shown.fields)


def test_the_projection_serialises_without_owner_or_storage_data():
    owner, view = public_one()
    shown = selectors.public_listing(Actor.visitor("r"), view.id)
    text = json.dumps(plain(shown), default=str)
    for link in ListingImage.objects.filter(listing_id=view.id):
        assert str(link.asset_id) not in text
    assert str(owner.id) not in text
    for name in FORBIDDEN_NAMES:
        assert name not in {f.name for f in dataclasses.fields(shown)}
        assert f'"{name}' not in text, name


def test_the_projection_is_frozen_and_not_a_model():
    _, view = public_one()
    shown = selectors.public_listing(Actor.visitor("r"), view.id)
    with pytest.raises(dataclasses.FrozenInstanceError):
        shown.title = "x"  # type: ignore[misc]
    assert set(vars(shown)) == TOP_LEVEL
    with pytest.raises(TypeError):
        shown.fields["bedrooms"] = 9  # a read-only mapping


def test_every_registry_field_of_class_private_is_dropped():
    """The guard can fail: given a registry that marks a field private, it is not copied."""
    values = {"title": "a", "bedrooms": 3, "bathrooms": 2}
    real = registry.field

    def definition(name):
        found = real(name)
        return replace(found, field_class="private") if name == "bathrooms" else found

    assert selectors.public_fields(values, definition) == {"bedrooms": 3}
    assert selectors.public_fields(values) == {"bedrooms": 3, "bathrooms": 2}


def test_a_field_without_a_registry_definition_fails_loudly():
    with pytest.raises(RuntimeError, match="mystery"):
        selectors.public_fields({"bedrooms": 3, "mystery": 1})
    with pytest.raises(RuntimeError, match="owner_phone"):
        selectors.public_fields({"owner_phone": "x"}, lambda name: None)


def test_the_projection_carries_the_price_and_area_fields_with_their_values():
    owner = make_account(random_email())
    view = services.create_draft(user(owner), *HOUSE)
    services.apply_updates(
        user(owner),
        view.id,
        view.revision,
        {
            "asking_price": "9500000",
            "price_basis": "total",
            "area_value": "1200",
            "area_unit": "sqft",
        },
    )
    set_listing_visibility(view.id)
    shown = selectors.public_listing(Actor.visitor("r"), view.id)
    assert shown.fields["asking_price"] == "9500000.00"
    assert shown.fields["price_basis"] == "total"
    assert shown.fields["area_value"] == "1200.0000" and shown.fields["area_unit"] == "sqft"
    # the derived area is public in the registry, so it is projected
    assert registry.field("area_sqft").field_class == "public"
    assert shown.fields["area_sqft"] == "1200.0000"


def test_a_field_the_registry_marks_private_is_omitted_end_to_end(monkeypatch):
    """Through `public_listing`: change one carried field's class to private in the registry and
    it vanishes from the projection while its neighbours stay."""
    owner = make_account(random_email())
    view = services.create_draft(user(owner), *HOUSE)
    services.apply_updates(
        user(owner), view.id, view.revision, {"asking_price": "9500000", "price_basis": "total"}
    )
    set_listing_visibility(view.id)
    visitor = Actor.visitor("r")
    assert selectors.public_listing(visitor, view.id).fields["asking_price"] == "9500000.00"
    real = registry.field

    def patched(name):
        found = real(name)
        return replace(found, field_class="private") if name == "asking_price" else found

    monkeypatch.setattr(registry, "field", patched)
    shown = selectors.public_listing(visitor, view.id)
    assert "asking_price" not in shown.fields
    assert shown.fields["price_basis"] == "total"
    assert "9500000" not in json.dumps(plain(shown), default=str)
