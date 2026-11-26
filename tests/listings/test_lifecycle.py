"""The Listing lifecycle (Story 2.17): the status transitions, editing of Published and Archived
Listings, owner visibility of every status, counts, and the interactions with older stories.
Values are low-entropy fake words."""

import itertools
import threading
import uuid

import pytest
from django.db import connection
from django.utils import timezone

from apps.identity import services as identity_services
from apps.kernel import audit, events
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord, IdempotencyRecord
from apps.listings import policy, publication, quota, selectors, services, status
from apps.listings.events import ListingModelInputsChanged, ListingStatusChanged
from apps.listings.lifecycle import ListingStatus
from apps.listings.models import (
    ListingImage,
    ListingRevision,
    PropertyListing,
    PublicationEvent,
)
from tests.imaging import make_image
from tests.listings.support import (
    HOUSE,
    RENT_HOUSE,
    SYSTEM,
    make_account,
    places,
    random_email,
    ready_draft,
    seed_cap,
    seed_media_policies,
    set_account_state,
    set_listing_visibility,
    user,
)

PNG = make_image("PNG", (400, 300))
ALL = tuple(s.value for s in ListingStatus)
TARGETS = ("archived", "sold", "rented")
# Written out from the spec, not read from the code under test.
ALLOWED = {
    ("published", "archived"),
    ("published", "sold"),
    ("published", "rented"),
    ("sold", "archived"),
    ("rented", "archived"),
}
_keys = itertools.count(1)


@pytest.fixture(autouse=True)
def setup(db):
    seed_cap()
    seed_media_policies()


@pytest.fixture
def bus(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    seen = []
    events.subscribe(ListingStatusChanged, seen.append)
    events.subscribe(ListingModelInputsChanged, seen.append)
    return seen


def agency():
    return make_account(random_email(), "agency")


def published(account=None, values=HOUSE, **kw):
    """(account, view) of a Published Listing, built through the real write path."""
    account = account or agency()
    view = ready_draft(account, values, **kw)
    publication.publish(user(account), view.id, view.revision, f"key-{next(_keys):04d}")
    return account, selectors.get_owned_listing(user(account), view.id)


def published_for(target, account=None):
    """A sale for `sold`, a rental for `rented`, either for `archived` (Story 2.17)."""
    return published(account, RENT_HOUSE if target == "rented" else HOUSE)


def row_of(view):
    return PropertyListing.objects.get(id=view.id)


UNSET = object()


def move(account, view, target, base=UNSET):
    return status.change_status(
        user(account), view.id, view.revision if base is UNSET else base, target
    )


def refused(call, code="conflict"):
    with pytest.raises(DomainError) as caught:
        call()
    assert caught.value.code == code, caught.value.code
    return caught.value


def snapshot():
    return (
        list(PropertyListing.objects.values_list("id", "status", "revision", "updated_at")),
        ListingRevision.objects.count(),
        AuditRecord.objects.count(),
        PublicationEvent.objects.count(),
        IdempotencyRecord.objects.count(),
    )


def edit(account, view, fields, base=None):
    return services.apply_updates(
        user(account), view.id, view.revision if base is None else base, fields
    )


def codes_of(account, view, fields):
    with pytest.raises(services.FieldsInvalid) as caught:
        edit(account, view, fields)
    return caught.value.fields


# --- the transition table ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("purpose", "source", "target"), list(itertools.product(("sale", "rent"), ALL, TARGETS))
)
def test_every_transition_is_allowed_or_refused_as_the_table_says(purpose, source, target, bus):
    account, view = published(values=HOUSE if purpose == "sale" else RENT_HOUSE)
    set_listing_visibility(view.id, source)
    view = selectors.get_owned_listing(user(account), view.id)
    before = snapshot()
    # sold is for a sale and rented for a rental; archive has no purpose rule
    fits = {"archived": True, "sold": purpose == "sale", "rented": purpose == "rent"}[target]
    if (source, target) in ALLOWED and fits:
        done = move(account, view, target)
        assert (done.status, done.revision) == (target, view.revision + 1)
        assert row_of(view).status == target
    else:
        # a Removed Listing is moderation's, so the owner is refused outright; the rest conflict
        code = "forbidden" if source == "removed" else "conflict"
        refused(lambda: move(account, view, target), code)
        assert snapshot() == before
        assert bus == []


@pytest.mark.parametrize("target", ["published", "removed", "draft", "pending", "", 5, [], None])
def test_a_target_outside_archived_sold_rented_is_a_validation_error(target):
    account, view = published()
    before = snapshot()
    caught = refused(lambda: move(account, view, target), "validation_error")
    assert set(caught.fields) == {"status"}
    assert snapshot() == before


@pytest.mark.parametrize("base", [None, 0, -1, True, "1", 1.5])
def test_a_bad_base_revision_is_a_validation_error(base):
    account, view = published()
    caught = refused(lambda: move(account, view, "archived", base=base), "validation_error")
    assert set(caught.fields) == {"base_revision"}
    assert row_of(view).status == "published"


def test_a_successful_move_writes_exactly_the_documented_things(
    bus, django_capture_on_commit_callbacks
):
    account, view = published()
    allowance = quota.owner_allowance(user(account), timezone.now())
    keys = IdempotencyRecord.objects.count()
    events_before = PublicationEvent.objects.count()
    revisions = ListingRevision.objects.count()
    audits = AuditRecord.objects.filter(action="listing.status_changed").count()
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        done = move(account, view, "sold")
        assert bus == []  # nothing before the commit
    assert len(callbacks) == 1
    assert bus == [ListingStatusChanged(listing_id=view.id, account_id=account.id, status="sold")]
    assert (done.status, done.visibility, done.revision) == ("sold", "private", view.revision + 1)
    row = row_of(view)
    assert (row.status, row.revision) == ("sold", view.revision + 1)
    revision = ListingRevision.objects.get(listing=row, revision=row.revision)
    assert (revision.changes, revision.origin, revision.actor_account_id) == (
        {"status": "sold"},
        "manual",
        account.id,
    )
    assert ListingRevision.objects.count() == revisions + 1
    record = AuditRecord.objects.get(action="listing.status_changed")
    assert AuditRecord.objects.filter(action="listing.status_changed").count() == audits + 1
    assert (record.target_type, record.target_id, record.metadata) == (
        "listing",
        view.id,
        {"from": "published", "to": "sold"},
    )
    # no quota, no publication event, no key
    assert PublicationEvent.objects.count() == events_before
    assert quota.owner_allowance(user(account), timezone.now()) == allowance
    assert IdempotencyRecord.objects.count() == keys  # no key is claimed


def test_a_repeat_is_a_clean_conflict_not_a_second_change(bus):
    account, view = published()
    done = move(account, view, "archived")
    before = snapshot()
    refused(lambda: move(account, done, "archived"))
    refused(lambda: move(account, done, "sold"))  # archived has no way out here
    assert snapshot() == before
    assert AuditRecord.objects.filter(action="listing.status_changed").count() == 1


def test_a_listing_never_returns_to_published_here_and_removed_is_not_reachable():
    account, view = published()
    done = move(account, view, "sold")
    for target in ("published", "removed"):
        refused(lambda t=target: move(account, done, t), "validation_error")
    assert row_of(view).status == "sold"


def test_a_stale_revision_carries_the_current_listing_and_writes_nothing(bus):
    account, view = published()
    edit(account, view, {"title": "A different family house"})
    before = snapshot()
    caught = refused(lambda: move(account, view, "archived"), "stale_revision")
    assert caught.current.revision == view.revision + 1
    assert caught.current.status == "published"
    assert snapshot() == before and bus == []


def test_the_stale_check_comes_before_the_transition_check():
    account, view = published()
    set_listing_visibility(view.id, "removed")
    caught = refused(lambda: move(account, view, "archived", base=99), "stale_revision")
    assert caught.current.status == "removed"


# --- who may change a status ---------------------------------------------------------------


def test_everyone_but_the_owner_is_not_found_and_a_visitor_unauthenticated():
    account, view = published()
    other = make_account(random_email())
    admin = identity_services.provision_admin(
        SYSTEM, "root.admin@example.com", "mango river 4291 x"
    )
    before = snapshot()
    for actor in (
        user(other),
        Actor.admin(admin.id, "r"),
        Actor.user(admin.id, "r"),
        SYSTEM,
    ):
        refused(lambda a=actor: status.change_status(a, view.id, 1, "archived"), "not_found")
    for lookup in (uuid.uuid4(), "nope", None, 5, str(view.id).upper()):
        refused(lambda v=lookup: status.change_status(user(account), v, 1, "archived"), "not_found")
    refused(
        lambda: status.change_status(Actor.visitor("r"), view.id, 1, "archived"), "unauthenticated"
    )
    assert snapshot() == before


@pytest.mark.parametrize("state", ["active", "unverified"])
@pytest.mark.parametrize("target", TARGETS)
def test_active_and_email_unverified_owners_may_use_all_three(state, target):
    account, view = published_for(target)
    set_account_state(account.id, state)
    assert move(account, view, target).status == target


@pytest.mark.parametrize("target", ["sold", "rented"])
def test_a_suspended_owner_may_not_mark_sold_or_rented(target, bus):
    account, view = published_for(target)
    set_account_state(account.id, "suspended")
    before = snapshot()
    refused(lambda: move(account, view, target), "forbidden")
    assert snapshot() == before and bus == []


def test_a_suspended_owner_may_archive_but_not_edit():
    account, view = published()
    set_account_state(account.id, "suspended")
    assert move(account, view, "archived").status == "archived"
    refused(lambda: edit(account, view, {"title": "A different family house"}), "forbidden")


def test_a_suspended_owner_may_still_read_every_status_and_the_counts():
    account, view = published()
    set_account_state(account.id, "suspended")
    assert selectors.get_owned_listing(user(account), view.id).status == "published"
    assert selectors.owner_counts(user(account))["published"] == 1
    assert [i.id for i in selectors.list_owned(user(account), "published").items] == [view.id]


def test_a_deactivated_owner_has_no_access():
    account, view = published()
    set_account_state(account.id, "deactivated")
    refused(lambda: move(account, view, "archived"), "forbidden")
    refused(lambda: selectors.owner_counts(user(account)), "forbidden")


# --- restricted and removed ----------------------------------------------------------------


def test_a_restricted_listing_may_only_be_archived_and_stays_restricted():
    account, view = published()
    PropertyListing.objects.filter(id=view.id).update(restricted=True, restriction_reason="reason")
    view = selectors.get_owned_listing(user(account), view.id)
    for target in ("sold", "rented"):
        refused(lambda t=target: move(account, view, t), "forbidden")
    refused(lambda: edit(account, view, {"title": "A different family house"}), "forbidden")
    assert move(account, view, "archived").status == "archived"
    row = row_of(view)
    assert (row.restricted, row.restriction_reason) == (True, "reason")


def test_a_removed_listing_cannot_be_changed_by_its_owner():
    account, view = published()
    set_listing_visibility(view.id, "removed")
    view = selectors.get_owned_listing(user(account), view.id)
    before = snapshot()
    for target in TARGETS:
        refused(lambda t=target: move(account, view, t), "forbidden")
    refused(lambda: edit(account, view, {"title": "A different family house"}), "forbidden")
    refused(lambda: services.upload_image(user(account), view.id, PNG), "forbidden")
    assert snapshot() == before


@pytest.mark.parametrize("flag", [False, True])
def test_the_owner_reads_restriction_state_and_the_public_never_does(flag):
    account, view = published()
    PropertyListing.objects.filter(id=view.id).update(
        restricted=flag, restriction_reason="short reason" if flag else None
    )
    owner_view = selectors.get_owned_listing(user(account), view.id)
    assert owner_view.restricted is flag
    assert owner_view.restriction_reason == ("short reason" if flag else None)
    page = selectors.list_owned(user(account))
    assert [(i.restricted, i.restriction_reason) for i in page.items] == [
        (flag, "short reason" if flag else None)
    ]
    preview = selectors.preview_of(user(account), view.id)
    assert "short reason" not in repr(preview.__dict__ if hasattr(preview, "__dict__") else preview)
    assert not {"restricted", "restriction_reason", "status"} & set(
        selectors.PublicListingView.__dataclass_fields__
    )
    if not flag:
        public = selectors.public_listing(Actor.visitor("r"), view.id)
        assert "restriction_reason" not in repr(public)


def test_a_removed_listing_is_visible_to_the_owner_only():
    account, view = published()
    PropertyListing.objects.filter(id=view.id).update(
        status="removed", restriction_reason="short reason"
    )
    other = make_account(random_email())
    assert selectors.get_owned_listing(user(account), view.id).restriction_reason == "short reason"
    assert [i.id for i in selectors.list_owned(user(account), "removed").items] == [view.id]
    refused(lambda: selectors.get_owned_listing(user(other), view.id), "not_found")
    assert selectors.list_owned(user(other)).items == ()
    assert selectors.preview_of(user(account), view.id).id == view.id  # preview: every status
    refused(lambda: selectors.preview_of(user(other), view.id), "not_found")


# --- public visibility of each status ------------------------------------------------------


@pytest.mark.parametrize("state", [s for s in ALL if s != "published"])
def test_no_other_status_is_ever_public_but_the_owner_sees_it(state):
    account, view = published(images=1)
    image = ListingImage.objects.get(listing_id=view.id)
    set_listing_visibility(view.id, state)
    visitor = Actor.visitor("r")
    other = user(make_account(random_email()))
    assert policy.is_public(row_of(view)) is False
    assert not selectors.public_listings().filter(id=view.id).exists()
    for viewer in (visitor, other, user(account)):
        refused(lambda v=viewer: selectors.public_listing(v, view.id), "not_found")
    for viewer in (visitor, other):
        refused(lambda v=viewer: selectors.image_for_serving(v, view.id, image.id), "not_found")
        refused(
            lambda v=viewer: selectors.image_for_serving(v, view.id, image.id, with_bytes=False),
            "not_found",
        )
    assert policy.can_view(user(account), row_of(view)) is True
    assert selectors.get_owned_listing(user(account), view.id).visibility == "private"


def test_archiving_takes_the_listing_off_the_public_surface_at_once():
    account, view = published()
    visitor = Actor.visitor("r")
    assert selectors.public_listing(visitor, view.id).id == view.id
    move(account, view, "archived")
    refused(lambda: selectors.public_listing(visitor, view.id), "not_found")


# --- editing a Published Listing -----------------------------------------------------------


def published_with_address():
    account, view = published()
    view = edit(account, view, {"street": "Mango Lane", "society": "Plum Gardens"})
    return account, view


EDITS = [
    ("text", {"title": "A different family house"}),
    ("text", {"description": "A bright home with a garden."}),
    ("money", {"asking_price": "1750000"}),
    ("enum", {"price_basis": "per_unit"}),
    ("integer", {"bedrooms": 4}),
    ("integer", {"bathrooms": 3}),
    ("enum", {"condition": "good"}),
    ("location text", {"society": "Plum Gardens", "block": "A"}),
    ("private address", {"street": "Mango Lane", "house_number": "12"}),
    ("amenities", None),
    ("several", {"title": "A different family house", "asking_price": "1750000", "bedrooms": 5}),
]


@pytest.mark.parametrize(
    ("kind", "fields"), EDITS, ids=[f"{k}-{i}" for i, (k, _) in enumerate(EDITS)]
)
def test_editing_a_non_identity_field_of_a_published_listing_is_saved_without_quota(
    kind, fields, bus
):
    account, view = published()
    if fields is None:
        from apps.catalog import selectors as catalog

        fields = {"amenities": dict.fromkeys(sorted(catalog.amenity_codes())[:2], "present")}
    allowance = quota.owner_allowance(user(account), timezone.now())
    events_before = PublicationEvent.objects.count()
    done = edit(account, view, fields)
    assert (done.status, done.visibility, done.revision) == (
        "published",
        "public",
        view.revision + 1,
    )
    stored = selectors.get_owned_listing(user(account), view.id)  # read back, not the return value
    for name, value in fields.items():
        wanted = "1750000.00" if name == "asking_price" else value  # money keeps two decimals
        assert stored.fields[name] == wanted, name
    assert stored.revision == done.revision
    row = row_of(view)
    assert (row.status, row.revision) == ("published", view.revision + 1)
    assert ListingRevision.objects.filter(listing=row, revision=row.revision).count() == 1
    assert PublicationEvent.objects.count() == events_before
    assert quota.owner_allowance(user(account), timezone.now()) == allowance
    assert selectors.public_listing(Actor.visitor("r"), view.id).id == view.id  # live at once


def test_a_published_edit_is_visible_in_the_public_projection_immediately():
    account, view = published()
    edit(account, view, {"title": "A different family house"})
    assert selectors.public_listing(Actor.visitor("r"), view.id).title == "A different family house"


def test_the_private_address_stays_private_after_a_published_edit():
    account, view = published_with_address()
    public = selectors.public_listing(Actor.visitor("r"), view.id)
    assert "Mango Lane" not in repr(public.__dict__) and "street" not in public.fields


def test_a_valuation_input_change_emits_the_event_and_another_change_does_not(
    bus, django_capture_on_commit_callbacks
):
    account, view = published()
    with django_capture_on_commit_callbacks(execute=True):
        done = edit(account, view, {"bedrooms": 5})
    assert bus == [ListingModelInputsChanged(listing_id=view.id)]
    with django_capture_on_commit_callbacks(execute=True):
        edit(account, done, {"title": "A different family house"})
    assert len(bus) == 1


# --- identity fields ------------------------------------------------------------------------


def identity_changes():
    place = places()
    return {
        "city": {"city": str(place["lahore"].id), "locality": str(place["other"].id)},
        "locality": {"locality": str(place["area"].id)},  # set in a second area below
        "area_value": {"area_value": "11"},
        "area_unit": {"area_unit": "kanal"},
        "marla_basis": {"marla_basis": "250"},
    }


@pytest.mark.parametrize("name", ["city", "area_value", "area_unit"])
def test_identity_fields_are_not_editable_while_published(name, bus):
    account, view = published()
    fields = identity_changes()[name]
    before = snapshot()
    problems = codes_of(account, view, fields)
    assert problems[name] == "not_editable"
    assert snapshot() == before and bus == []


def test_a_changed_locality_is_not_editable_while_published():
    from apps.catalog.models import Locality

    account, view = published()
    place = places()
    second, _ = Locality.objects.get_or_create(
        city=place["karachi"], name="Testville South", kind="area"
    )
    before = snapshot()
    assert codes_of(account, view, {"locality": str(second.id)}) == {"locality": "not_editable"}
    assert snapshot() == before


def test_every_failing_identity_field_is_reported_at_once_with_the_others():
    account, view = published()
    problems = codes_of(
        account, view, {"area_value": "11", "area_unit": "kanal", "bedrooms": 99999}
    )
    assert problems["area_value"] == problems["area_unit"] == "not_editable"
    assert problems["bedrooms"] != "not_editable"


def test_resending_the_stored_identity_value_is_accepted_while_published():
    account, view = published()
    done = edit(
        account,
        view,
        {
            "city": view.fields["city"],
            "area_value": view.fields["area_value"],
            "title": "A different family house",
        },
    )
    assert done.revision == view.revision + 1
    again = edit(account, done, {"area_unit": done.fields["area_unit"]})
    assert again.revision == done.revision  # nothing changed, nothing written


def test_clearing_an_identity_field_while_published_is_refused():
    account, view = published()
    assert codes_of(account, view, {"area_value": None})["area_value"] == "not_editable"


@pytest.mark.parametrize("name", ["city", "area_value", "area_unit", "marla_basis"])
def test_identity_fields_are_editable_on_a_draft_and_an_archived_listing(name):
    account, view = published()
    archived = move(account, view, "archived")
    fields = identity_changes()[name]
    done = edit(account, archived, fields)
    assert done.status == "archived" and done.revision == archived.revision + 1
    owner = make_account(random_email())
    draft = ready_draft(owner)
    assert edit(owner, draft, fields).revision == draft.revision + 1


def test_the_identity_set_comes_from_the_registry_flag_and_covers_the_spec_list():
    from apps.catalog import registry

    flagged = {f.id for f in registry.fields() if f.quota_identity}
    assert flagged == {
        "purpose",
        "category",
        "subtype",
        "city",
        "locality",
        "area_value",
        "area_unit",
    }


# --- an edit may not leave a Published Listing invalid -------------------------------------


def test_an_edit_that_would_make_the_listing_invalid_is_refused_with_every_problem(bus):
    account, view = published()
    before = snapshot()
    problems = codes_of(account, view, {"title": None, "asking_price": None, "bedrooms": None})
    assert problems == {
        "title": "required",
        "asking_price": "required",
        "bedrooms": "required",
    }
    assert snapshot() == before and bus == []
    row = row_of(view)
    assert (row.title, row.revision) == (view.fields["title"], view.revision)


def test_a_valid_edit_alongside_an_invalid_one_writes_nothing():
    account, view = published()
    before = snapshot()
    assert codes_of(account, view, {"title": "A different family house", "asking_price": None})
    assert snapshot() == before
    assert row_of(view).title == view.fields["title"]


def test_an_invalid_value_is_refused_by_the_field_rules_before_completeness():
    account, view = published()
    assert codes_of(account, view, {"title": "ab"}) == {"title": "too_short"}


def test_a_locality_outside_the_city_is_refused_while_published_and_nothing_is_written():
    account, view = published()
    place = places()
    before = snapshot()
    # the more specific code wins over the identity lock
    assert codes_of(account, view, {"locality": str(place["other"].id)}) == {
        "locality": "locality_not_in_city"
    }
    assert snapshot() == before


def test_removing_the_last_image_of_a_published_listing_is_refused_and_changes_nothing():
    account, view = published(images=1)
    image = ListingImage.objects.get(listing_id=view.id)
    before = (snapshot(), ListingImage.objects.count())
    with pytest.raises(services.FieldsInvalid) as caught:
        services.remove_image(user(account), view.id, image.id)
    assert caught.value.fields == {"images": "images_required"}
    assert (snapshot(), ListingImage.objects.count()) == before


def test_an_image_may_be_removed_while_another_remains_and_a_draft_may_lose_its_last():
    account, view = published(images=2)
    first, second = ListingImage.objects.filter(listing_id=view.id).order_by("position")
    services.remove_image(user(account), view.id, second.id)
    assert ListingImage.objects.filter(listing_id=view.id).count() == 1
    owner = make_account(random_email())
    draft = ready_draft(owner, images=1)
    services.remove_image(user(owner), draft.id, ListingImage.objects.get(listing_id=draft.id).id)
    assert ListingImage.objects.filter(listing_id=draft.id).count() == 0


def test_images_of_a_published_listing_may_be_uploaded_and_reordered_and_the_cover_changed():
    account, view = published(images=2)
    uploaded = services.upload_image(user(account), view.id, PNG)
    current = selectors.get_owned_listing(user(account), view.id)
    assert current.revision == view.revision + 1 and len(current.media) == 3
    order = [m.id for m in reversed(current.media)]
    done = services.set_image_order(user(account), view.id, current.revision, order, uploaded.id)
    assert [m.id for m in done.media] == order
    assert next(m for m in done.media if m.is_cover).id == uploaded.id
    assert done.status == "published" and done.revision == current.revision + 1


# --- editing an Archived Listing ------------------------------------------------------------


def test_an_archived_listing_is_edited_like_a_draft_without_completeness():
    account, view = published()
    archived = move(account, view, "archived")
    done = edit(account, archived, {"title": None, "asking_price": None})
    assert done.status == "archived" and done.fields["title"] is None
    assert codes_of(account, done, {"title": "ab"}) == {"title": "too_short"}
    removed = services.remove_image(
        user(account), view.id, ListingImage.objects.get(listing_id=view.id).id
    )
    assert removed is None  # images may all go from an Archived Listing


@pytest.mark.parametrize("state", ["sold", "rented"])
def test_sold_and_rented_listings_are_view_only(state):
    account, view = published_for(state)
    done = move(account, view, state)
    before = snapshot()
    refused(lambda: edit(account, done, {"title": "A different family house"}), "forbidden")
    refused(lambda: services.upload_image(user(account), view.id, PNG), "forbidden")
    image = ListingImage.objects.get(listing_id=view.id)
    refused(lambda: services.remove_image(user(account), view.id, image.id), "forbidden")
    refused(
        lambda: services.set_image_order(user(account), view.id, done.revision, [image.id]),
        "forbidden",
    )
    assert snapshot() == before


def test_the_images_of_a_restricted_listing_are_view_only():
    account, view = published()
    PropertyListing.objects.filter(id=view.id).update(restricted=True)
    image = ListingImage.objects.get(listing_id=view.id)
    refused(lambda: services.upload_image(user(account), view.id, PNG), "forbidden")
    refused(lambda: services.remove_image(user(account), view.id, image.id), "forbidden")


# --- the owner list and counts --------------------------------------------------------------


def test_the_status_filter_returns_each_status_and_counts_match():
    account = agency()
    drafts = [ready_draft(account) for _ in range(2)]
    pubs = [published(account)[1] for _ in range(3)]
    move(account, pubs[1], "archived")
    sold = move(account, pubs[2], "sold")
    move(account, sold, "archived")
    move(account, published(account, RENT_HOUSE)[1], "rented")
    set_listing_visibility(drafts[1].id, "removed")
    expected = {"draft": 1, "published": 1, "archived": 2, "sold": 0, "rented": 1, "removed": 1}
    assert selectors.owner_counts(user(account)) == expected
    assert list(selectors.owner_counts(user(account))) == list(ALL)
    for name, total in expected.items():
        page = selectors.list_owned(user(account), name)
        assert len(page.items) == total and {i.status for i in page.items} <= {name}
    assert len(selectors.list_owned(user(account)).items) == sum(expected.values())
    with pytest.raises(ValueError):
        selectors.list_owned(user(account), "pending")


def test_counts_are_per_account_and_zero_for_an_account_without_listings():
    one, _ = published()
    two = make_account(random_email())
    assert selectors.owner_counts(user(two)) == dict.fromkeys(ALL, 0)
    assert selectors.owner_counts(user(one))["published"] == 1
    refused(lambda: selectors.owner_counts(Actor.visitor("r")), "unauthenticated")
    admin = identity_services.provision_admin(
        SYSTEM, "root.admin@example.com", "mango river 4291 x"
    )
    refused(lambda: selectors.owner_counts(Actor.admin(admin.id, "r")), "forbidden")


def test_the_filtered_pages_paginate_with_a_cursor_per_status():
    account = agency()
    for _ in range(3):
        _, view = published(account)
        move(account, view, "archived")
    first = selectors.list_owned(user(account), "archived", limit=2)
    assert len(first.items) == 2 and first.next_cursor
    rest = selectors.list_owned(user(account), "archived", first.next_cursor, 2)
    assert len(rest.items) == 1 and rest.next_cursor is None
    with pytest.raises(selectors.InvalidCursor):
        selectors.list_owned(user(account), "sold", first.next_cursor)


# --- failures and races ---------------------------------------------------------------------


def test_a_failing_audit_write_leaves_status_and_revision_unchanged(monkeypatch, bus):
    account, view = published()
    before = snapshot()

    def boom(*args, **kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(audit, "record", boom)
    with pytest.raises(RuntimeError):
        move(account, view, "archived")
    monkeypatch.undo()
    assert snapshot() == before and bus == []
    row = row_of(view)
    assert (row.status, row.revision) == ("published", view.revision)


def test_a_failing_subscriber_never_undoes_the_move(monkeypatch):
    def failing(event):
        raise RuntimeError("subscriber down")

    monkeypatch.setattr(events, "_registry", {})
    events.subscribe(ListingStatusChanged, failing)
    account, view = published()
    assert move(account, view, "archived").status == "archived"


@pytest.mark.django_db(transaction=True)
def test_two_status_changes_racing_make_one_success_and_one_clean_refusal():
    seed_cap()
    account, view = published()
    barrier, outcomes = threading.Barrier(2), []

    def work(target):
        try:
            barrier.wait()
            outcomes.append(("ok", move(account, view, target).status))
        except DomainError as exc:
            outcomes.append((exc.code, None))
        except Exception as exc:  # noqa: BLE001
            outcomes.append((type(exc).__name__, None))
        finally:
            connection.close()

    threads = [threading.Thread(target=work, args=(t,)) for t in ("sold", "archived")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)
    assert sorted(kind for kind, _ in outcomes) == ["ok", "stale_revision"]
    row = row_of(view)
    assert row.revision == view.revision + 1
    assert row.status == next(value for kind, value in outcomes if kind == "ok")
    assert ListingRevision.objects.filter(listing=row, revision=row.revision).count() == 1
    assert AuditRecord.objects.filter(action="listing.status_changed").count() == 1


@pytest.mark.django_db(transaction=True)
def test_an_edit_racing_an_archive_is_one_clean_winner_and_one_stale():
    seed_cap()
    account, view = published()
    barrier, outcomes = threading.Barrier(2), []

    def archive():
        try:
            barrier.wait()
            move(account, view, "archived")
            outcomes.append("archived")
        except DomainError as exc:
            outcomes.append(exc.code)
        finally:
            connection.close()

    def change():
        try:
            barrier.wait()
            edit(account, view, {"title": "A different family house"})
            outcomes.append("edited")
        except DomainError as exc:
            outcomes.append(exc.code)
        finally:
            connection.close()

    threads = [threading.Thread(target=archive), threading.Thread(target=change)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)
    assert sorted(outcomes) in (["archived", "stale_revision"], ["edited", "stale_revision"])
    row = row_of(view)
    assert row.revision == view.revision + 1
    assert (row.status == "archived") == ("archived" in outcomes)


# --- interactions with the earlier stories --------------------------------------------------


@pytest.mark.parametrize("state", ["published", "archived", "sold", "rented", "removed"])
def test_only_a_draft_can_be_deleted(state):
    account, view = published()
    set_listing_visibility(view.id, state)
    refused(lambda: services.delete_draft(user(account), view.id), "conflict")
    assert PropertyListing.objects.filter(id=view.id).exists()


@pytest.mark.parametrize("state", ["archived", "sold", "rented", "removed"])
def test_publication_of_a_non_draft_is_still_a_conflict(state):
    account, view = published()
    set_listing_visibility(view.id, state)
    view = selectors.get_owned_listing(user(account), view.id)
    with pytest.raises(DomainError) as caught:
        publication.publish(user(account), view.id, view.revision, "key-9999")
    assert caught.value.code == "conflict"


def test_only_drafts_count_toward_the_open_draft_cap():
    seed_cap(2)
    account = agency()
    for _ in range(2):
        _, view = published(account)
        move(account, view, "archived")
    for _ in range(2):
        services.create_draft(user(account), *HOUSE)  # archived ones do not count
    with pytest.raises(DomainError) as caught:
        services.create_draft(user(account), *HOUSE)
    assert caught.value.code == "throttled"


def test_retention_never_touches_a_listing_that_is_not_a_draft():
    from datetime import timedelta

    from django.utils import timezone

    account = agency()
    ids = []
    for state in ("archived", "sold", "rented", "removed"):
        _, view = published(account)
        set_listing_visibility(view.id, state)
        ids.append(view.id)
    old = timezone.now() - timedelta(days=400)
    PropertyListing.objects.filter(id__in=ids).update(updated_at=old, created_at=old)
    now = timezone.now()
    assert not set(selectors.idle_draft_ids(now)) & set(ids)
    assert not set(selectors.due_draft_ids(now)) & set(ids)
    for listing_id in ids:
        assert services.notice_idle_draft(SYSTEM, listing_id, now) is False
        assert services.expire_idle_draft(SYSTEM, listing_id, now) == "skipped"
    assert PropertyListing.objects.filter(id__in=ids).count() == 4


@pytest.mark.parametrize("state", ["draft", "published", "archived", "sold", "rented", "removed"])
def test_sample_data_removal_works_for_a_listing_in_every_status(state):
    from apps.identity import selectors as identity
    from tests.listings.support import PASSWORD

    owner = identity_services.create_sample_account(
        SYSTEM,
        f"sample.individual.9@{identity.SAMPLE_EMAIL_DOMAIN}",
        PASSWORD,
        "individual",
        "Sample Person",
    )
    view = ready_draft(owner)
    publication.publish(user(owner), view.id, view.revision, "key-sample")
    set_listing_visibility(view.id, state)
    assert services.remove_sample_listing(SYSTEM, view.id) is True
    assert not PropertyListing.objects.filter(id=view.id).exists()
    assert not ListingImage.objects.filter(listing_id=view.id).exists()


def test_the_status_service_takes_the_actor_first():
    import inspect

    assert list(inspect.signature(status.change_status).parameters)[:1] == ["actor"]
