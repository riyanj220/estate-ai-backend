"""Story 2.21: what suspending an owner does to their Listings, through the real services."""

import uuid

import pytest

from apps.identity import services as identity
from apps.kernel import runtime
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.listings import publication, selectors, services, status
from apps.listings.models import PropertyListing
from tests.imaging import make_image
from tests.listings.support import (
    HOUSE,
    make_account,
    random_email,
    ready_draft,
    seed_cap,
    seed_media_policies,
    user,
)

ADMIN = Actor.admin(uuid.UUID(int=7), "req")
VISITOR = Actor.visitor("req")
PNG = make_image("PNG", (400, 300))


@pytest.fixture(autouse=True)
def setup(db):
    seed_cap()
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


def code(call, *args):
    with pytest.raises(DomainError) as caught:
        call(*args)
    return caught.value.code


def fresh(owner, view):
    return selectors.get_owned_listing(user(owner), view.id)


def listing(owner):
    view = ready_draft(owner, images=0)
    image = services.upload_image(user(owner), view.id, PNG)
    return fresh(owner, view), image


def published(owner, key):
    view, image = listing(owner)
    publication.publish(user(owner), view.id, view.revision, key)
    return fresh(owner, view), image


def rows():
    return list(PropertyListing.objects.order_by("id").values())


def test_a_suspended_owner_may_only_view_and_archive_and_listings_return():
    owner = make_account(random_email(), "agency")
    actor = user(owner)
    live, image = published(owner, "key-live-0001")
    other, _ = published(owner, "key-sold-0001")
    old, _ = published(owner, "key-arch-0001")
    status.change_status(actor, old.id, old.revision, "archived")
    old = fresh(owner, old)
    draft, draft_image = listing(owner)
    before = rows()

    identity.suspend_account(ADMIN, owner.id, "policy breach")

    assert rows() == before  # no Listing row changes
    assert code(selectors.public_listing, VISITOR, live.id) == "not_found"
    assert code(selectors.image_for_serving, VISITOR, live.id, image.id) == "not_found"
    assert not selectors.public_listings().exists()
    assert fresh(owner, live).visibility == "private"
    assert selectors.image_for_serving(actor, live.id, image.id).public is False
    assert selectors.preview_of(actor, draft.id).id == draft.id

    refusals = [
        (publication.publish, draft.id, draft.revision, "key-new-00001"),
        (publication.republish, old.id, old.revision, "key-re-000001"),
        (publication.correct, live.id, live.revision, {"area_value": "11"}),
        (services.apply_updates, draft.id, draft.revision, {"title": "Another quiet house"}),
        (services.apply_updates, live.id, live.revision, {"title": "Another quiet house"}),
        (services.upload_image, draft.id, PNG),
        (services.remove_image, draft.id, draft_image.id),
        (services.delete_draft, draft.id),
        (status.change_status, other.id, other.revision, "sold"),
        (status.change_status, other.id, other.revision, "rented"),
    ]
    for call, *args in refusals:
        expected = "publishing_unavailable" if call.__name__ in ("publish", "republish") else None
        assert code(call, actor, *args) == (expected or "forbidden"), call.__name__
    assert code(services.create_draft, actor, *HOUSE) == "forbidden"
    assert rows() == before

    status.change_status(actor, other.id, other.revision, "archived")
    assert fresh(owner, other).status == "archived"

    identity.reactivate_account(ADMIN, owner.id, "appeal upheld")

    assert selectors.public_listing(VISITOR, live.id).id == live.id
    assert selectors.image_for_serving(VISITOR, live.id, image.id).public is True
    assert fresh(owner, live).visibility == "public"


def test_an_unverified_owner_stays_private_after_reactivation():
    owner = make_account(random_email())
    view, image = published(owner, "key-live-0002")
    from apps.identity.models import Account

    Account.objects.filter(id=owner.id).update(email_verified_at=None)
    identity.suspend_account(ADMIN, owner.id, "policy breach")
    identity.reactivate_account(ADMIN, owner.id, "appeal upheld")
    assert code(selectors.public_listing, VISITOR, view.id) == "not_found"
    assert code(selectors.image_for_serving, VISITOR, view.id, image.id) == "not_found"
