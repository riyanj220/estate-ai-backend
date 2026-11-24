"""The contract of `selectors.image_for_serving` for Drafts (Story 2.10). Since Story 2.12 the
seam is the single visibility policy: these refusals and grants still hold for a Draft, and
`test_visibility.py` covers the full matrix."""

import uuid

import pytest
from django.utils import timezone

from apps.identity.models import Account
from apps.kernel import runtime, storage
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import MediaAsset
from apps.listings import selectors, services
from apps.listings.models import ListingImage
from tests.imaging import make_image
from tests.listings.support import (
    HOUSE,
    SYSTEM,
    make_account,
    random_email,
    seed_cap,
    seed_media_policies,
    user,
)

PNG = make_image("PNG", (400, 300))


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


def draft_with_image(state="active"):
    account = make_account(random_email(), state=state)
    view = services.create_draft(user(account), *HOUSE)
    image = services.upload_image(user(account), view.id, PNG)
    return account, view, image


def refused(actor, listing_id, image_id, **kw):
    with pytest.raises(DomainError) as caught:
        selectors.image_for_serving(actor, listing_id, image_id, **kw)
    return caught.value.code


def test_the_owner_gets_the_stored_bytes_and_type():
    account, view, image = draft_with_image()
    served = selectors.image_for_serving(user(account), view.id, image.id)
    asset = MediaAsset.objects.get()
    assert served.data == storage.get_storage().get(asset.storage_key)
    assert served.content_type == "image/png"
    assert served.size == len(served.data) == image.bytes


def test_a_headers_only_lookup_reads_no_file(monkeypatch):
    account, view, image = draft_with_image()

    def boom(self, key):
        raise AssertionError("the file was read")

    monkeypatch.setattr(storage.FilesystemStorage, "get", boom)
    served = selectors.image_for_serving(user(account), view.id, image.id, with_bytes=False)
    assert served.data == b"" and served.size == image.bytes and served.content_type == "image/png"
    other = make_account(random_email())
    assert refused(user(other), view.id, image.id, with_bytes=False) == "not_found"


@pytest.mark.parametrize("state", ["suspended", "unverified"])
def test_a_suspended_or_unverified_owner_may_view_their_own(state):
    account, view, image = draft_with_image(state="active")
    if state == "suspended":
        Account.objects.filter(id=account.id).update(
            suspended_at=timezone.now(), suspension_reason="r"
        )
    else:
        Account.objects.filter(id=account.id).update(email_verified_at=None)
    assert selectors.image_for_serving(user(account), view.id, image.id).size > 0


def test_a_deactivated_owner_cannot():
    account, view, image = draft_with_image()
    Account.objects.filter(id=account.id).update(deactivated_at=timezone.now())
    assert refused(user(account), view.id, image.id) == "not_found"


def test_everyone_else_is_not_found():
    owner, view, image = draft_with_image()
    other = make_account(random_email())
    admin = make_account(random_email())
    Account.objects.filter(id=admin.id).update(account_type="admin")
    for actor in (
        Actor.visitor("r"),
        user(other),
        Actor.admin(admin.id, "r"),
        Actor.admin(owner.id, "r"),  # even the owner's id, as an administrator
        SYSTEM,
        Actor.user(uuid.uuid4(), "r"),
    ):
        assert refused(actor, view.id, image.id) == "not_found"


def test_wrong_listing_and_malformed_ids_are_not_found():
    owner, view, image = draft_with_image()
    other_view = services.create_draft(user(owner), *HOUSE)
    for listing, picture in (
        (other_view.id, image.id),  # the image of another Listing
        (view.id, uuid.uuid4()),
        (uuid.uuid4(), image.id),
        ("nope", image.id),
        (view.id, "nope"),
        (None, None),
        (str(view.id).upper(), image.id),
        (view.id, str(image.id).upper()),
    ):
        assert refused(user(owner), listing, picture) == "not_found"


def test_a_lost_file_or_asset_row_is_not_found():
    account, view, image = draft_with_image()
    asset = MediaAsset.objects.get()
    storage.get_storage().delete(asset.storage_key)
    assert refused(user(account), view.id, image.id) == "not_found"
    MediaAsset.objects.all().delete()
    assert refused(user(account), view.id, image.id) == "not_found"
    assert refused(user(account), view.id, image.id, with_bytes=False) == "not_found"
    assert ListingImage.objects.filter(pk=image.id).exists()
