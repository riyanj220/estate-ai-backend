"""The visibility policy (Story 2.12): one table over status x restricted x owner state x viewer,
for `can_view`, `public_listings()`, `public_listing` and `image_for_serving`."""

import itertools
import uuid

import pytest
from django.db import connection

from apps.identity import selectors as identity
from apps.identity.models import Account
from apps.kernel import runtime
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.listings import policy, selectors, services
from apps.listings.lifecycle import ListingStatus, Visibility, visibility_of
from apps.listings.models import PropertyListing
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

PNG = make_image("PNG", (400, 300))
STATUSES = ("draft", "published")
RESTRICTED = (False, True)
OWNER_STATES = ("active", "unverified", "suspended", "deactivated")
VIEWERS = ("visitor", "other", "owner", "admin")


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap()
    seed_media_policies()
    runtime.shutdown()
    yield
    runtime.shutdown()


def expected(status, restricted, owner_state, viewer):
    """Written out from the rules, not from the code under test."""
    public = status == "published" and not restricted and owner_state == "active"
    sees_own = viewer == "owner" and owner_state != "deactivated"
    return public or sees_own


def build(status, restricted, owner_state):
    owner = make_account(random_email())
    view = services.create_draft(user(owner), *HOUSE)
    image = services.upload_image(user(owner), view.id, PNG)
    set_listing_visibility(view.id, status, restricted)
    set_account_state(owner.id, owner_state)
    return owner, view, image


def viewer_actor(kind, owner):
    if kind == "visitor":
        return Actor.visitor("r")
    if kind == "owner":
        return user(owner)
    if kind == "other":
        return user(make_account(random_email()))
    admin = make_account(random_email())
    Account.objects.filter(id=admin.id).update(account_type="admin")
    return Actor.admin(admin.id, "r")


CELLS = list(itertools.product(STATUSES, RESTRICTED, OWNER_STATES, VIEWERS))


@pytest.mark.parametrize(("status", "restricted", "owner_state", "viewer"), CELLS)
def test_the_matrix(status, restricted, owner_state, viewer):
    owner, view, image = build(status, restricted, owner_state)
    actor = viewer_actor(viewer, owner)
    row = PropertyListing.objects.get(pk=view.id)
    allowed = expected(status, restricted, owner_state, viewer)
    public = status == "published" and not restricted and owner_state == "active"

    assert policy.can_view(actor, row) is allowed
    assert policy.is_public(row) is public
    assert PropertyListing.objects.filter(pk=view.id).count() == 1
    assert (selectors.public_listings().filter(pk=view.id).exists()) is public

    if public:
        assert selectors.public_listing(actor, view.id).id == view.id
    else:
        with pytest.raises(DomainError) as caught:
            selectors.public_listing(actor, view.id)
        assert caught.value.code == "not_found"

    for with_bytes in (True, False):
        if allowed:
            served = selectors.image_for_serving(actor, view.id, image.id, with_bytes=with_bytes)
            assert served.public is public
            assert served.size > 0 and bool(served.data) is with_bytes
        else:
            with pytest.raises(DomainError) as caught:
                selectors.image_for_serving(actor, view.id, image.id, with_bytes=with_bytes)
            assert caught.value.code == "not_found"


@pytest.mark.parametrize(
    ("status", "restricted", "owner_state"),
    list(itertools.product(STATUSES, RESTRICTED, OWNER_STATES)),
)
def test_can_view_for_a_visitor_agrees_with_public_listings(status, restricted, owner_state):
    """The two implementations cannot drift: a Visitor sees exactly the `public_listings()` rows."""
    _, view, _ = build(status, restricted, owner_state)
    row = PropertyListing.objects.get(pk=view.id)
    assert (
        policy.can_view(Actor.visitor("r"), row)
        is selectors.public_listings().filter(pk=view.id).exists()
    )


def test_public_listings_holds_only_the_public_rows():
    cases = {}
    for status, restricted, owner_state in itertools.product(STATUSES, RESTRICTED, OWNER_STATES):
        _, view, _ = build(status, restricted, owner_state)
        cases[view.id] = (status, restricted, owner_state)
    shown = set(selectors.public_listings().values_list("id", flat=True))
    assert shown == {k for k, v in cases.items() if v == ("published", False, "active")}
    assert len(shown) == 1


def test_email_unverified_is_not_an_active_owner_so_its_listings_are_not_public():
    """Verified in `identity`: Email-unverified is its own state, distinct from Active."""
    owner, view, _ = build("published", False, "unverified")
    assert identity.is_active_owner(owner.id) is False
    assert not selectors.public_listings().filter(pk=view.id).exists()
    # ... yet the unverified owner still sees their own
    assert policy.can_view(user(owner), PropertyListing.objects.get(pk=view.id)) is True


@pytest.mark.parametrize("account_type", ["individual", "agency", "admin"])
@pytest.mark.parametrize("state", OWNER_STATES)
def test_the_active_owner_filter_agrees_with_is_active_owner(account_type, state):
    account = make_account(
        random_email(), account_type if account_type != "admin" else "individual"
    )
    set_account_state(account.id, state)
    if account_type == "admin":
        Account.objects.filter(id=account.id).update(account_type="admin")
    in_filter = identity.active_owner_account_ids().filter(id=account.id).exists()
    assert in_filter is identity.is_active_owner(account.id)
    assert in_filter is (state == "active" and account_type != "admin")


def test_suspended_then_reactivated_is_hidden_then_visible_again():
    owner, view, image = build("published", False, "active")
    visitor = Actor.visitor("r")
    assert selectors.public_listing(visitor, view.id).id == view.id
    set_account_state(owner.id, "suspended")
    with pytest.raises(DomainError):
        selectors.public_listing(visitor, view.id)
    with pytest.raises(DomainError):
        selectors.image_for_serving(visitor, view.id, image.id)
    assert selectors.image_for_serving(user(owner), view.id, image.id).public is False
    set_account_state(owner.id, "active")
    assert selectors.public_listing(visitor, view.id).id == view.id
    assert selectors.image_for_serving(visitor, view.id, image.id).public is True


def test_a_listing_that_stops_being_public_is_refused_on_the_next_request():
    owner, view, image = build("published", False, "active")
    visitor = Actor.visitor("r")
    assert selectors.image_for_serving(visitor, view.id, image.id).public is True
    set_listing_visibility(view.id, "published", True)
    with pytest.raises(DomainError):
        selectors.image_for_serving(visitor, view.id, image.id)
    assert selectors.image_for_serving(user(owner), view.id, image.id).public is False


def test_unknown_malformed_and_foreign_ids_are_not_found():
    owner, view, image = build("published", False, "active")
    other_view = services.create_draft(user(owner), *HOUSE)
    visitor = Actor.visitor("r")
    for listing, picture in (
        (other_view.id, image.id),
        (view.id, uuid.uuid4()),
        (uuid.uuid4(), image.id),
        ("nope", image.id),
        (view.id, "nope"),
        (None, None),
        (str(view.id).upper(), image.id),
    ):
        with pytest.raises(DomainError) as caught:
            selectors.image_for_serving(visitor, listing, picture)
        assert caught.value.code == "not_found"
    for bad in (uuid.uuid4(), "nope", None, str(view.id).upper()):
        with pytest.raises(DomainError) as caught:
            selectors.public_listing(visitor, bad)
        assert caught.value.code == "not_found"


def test_visibility_of_status_is_public_only_for_a_published_listing_that_is_public():
    assert visibility_of(ListingStatus.DRAFT, public=False) is Visibility.PRIVATE
    assert visibility_of(ListingStatus.DRAFT, public=True) is Visibility.PRIVATE
    assert visibility_of(ListingStatus.PUBLISHED, public=False) is Visibility.PRIVATE
    assert visibility_of(ListingStatus.PUBLISHED, public=True) is Visibility.PUBLIC


@pytest.mark.parametrize(
    ("status", "restricted", "owner_state", "label"),
    [
        ("draft", False, "active", "private"),
        ("published", False, "active", "public"),
        ("published", True, "active", "private"),
        ("published", False, "suspended", "private"),
        ("published", False, "unverified", "private"),
    ],
)
def test_the_owner_facing_visibility_label_says_public_only_when_it_is(
    status, restricted, owner_state, label
):
    owner, view, _ = build(status, restricted, owner_state)
    assert selectors.get_owned_listing(user(owner), view.id).visibility == label
    page = selectors.list_owned(user(owner))
    assert [item.visibility for item in page.items if item.id == view.id] == [label]


def drop_status_check():
    with connection.cursor() as cursor:  # undone with the test's transaction
        cursor.execute("ALTER TABLE listings_propertylisting DROP CONSTRAINT listings_status_valid")


@pytest.mark.parametrize("viewer", VIEWERS)
def test_a_status_outside_draft_and_published_is_refused_to_everyone_the_owner_included(viewer):
    """The owner branch is explicit: a later status grants nothing until its story adds it."""
    owner, view, image = build("draft", False, "active")
    drop_status_check()
    PropertyListing.objects.filter(id=view.id).update(status="archived")
    actor = viewer_actor(viewer, owner)
    row = PropertyListing.objects.get(pk=view.id)
    assert policy.is_public(row) is False and policy.can_view(actor, row) is False
    assert not selectors.public_listings().filter(pk=view.id).exists()
    for call in (
        lambda: selectors.public_listing(actor, view.id),
        lambda: selectors.image_for_serving(actor, view.id, image.id),
        lambda: selectors.image_for_serving(actor, view.id, image.id, with_bytes=False),
    ):
        with pytest.raises(DomainError) as caught:
            call()
        assert caught.value.code == "not_found"
