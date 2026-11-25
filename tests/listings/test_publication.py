"""`listings.publication.publish` (Story 2.15): validation, quota, idempotency, atomicity,
concurrency and visibility. Values are low-entropy fake words."""

import hashlib
import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.core import mail
from django.db import connection
from django.utils import timezone

from apps.catalog import registry
from apps.catalog.models import City
from apps.identity.models import Account
from apps.kernel import ai_log, audit, events, idempotency, notifications, runtime
from apps.kernel import mail as kernel_mail
from apps.kernel import media as kernel_media
from apps.kernel import storage as kernel_storage
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError, field_message
from apps.kernel.models import AuditRecord, IdempotencyRecord
from apps.listings import publication, selectors, services
from apps.listings.events import ListingPublished
from apps.listings.models import (
    ListingLocation,
    ListingQuotaPolicy,
    ListingRevision,
    PropertyListing,
    PublicationEvent,
)
from tests.listings.support import (
    APARTMENT,
    COMMERCIAL_PLOT,
    HOUSE,
    PLOT_FILE,
    RENT_HOUSE,
    SHOP,
    STREET,
    SYSTEM,
    make_account,
    places,
    random_email,
    ready_draft,
    seed_cap,
    set_account_state,
    user,
)

KEY = "key-0001"
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
BASE_REQUIRED = {
    "title",
    "asking_price",
    "price_basis",
    "area_value",
    "area_unit",
    "city",
    "locality",
}  # written out from the spec, not read from the registry
UNIT = {"bedrooms", "bathrooms"}


@pytest.fixture(autouse=True)
def cap(db):
    seed_cap()


def publish(account, view, key=KEY, base=None, **kw):
    return publication.publish(
        user(account), view.id, view.revision if base is None else base, key, **kw
    )


def refused(account, view, exc=DomainError, key=KEY, base=None, **kw):
    with pytest.raises(exc) as caught:
        publish(account, view, key, base, **kw)
    return caught.value


def problems(account, view, **kw):
    return refused(account, view, publication.FieldsInvalid, **kw).fields


def nothing_written():
    return (
        PublicationEvent.objects.count(),
        AuditRecord.objects.filter(action="listing.published").count(),
        IdempotencyRecord.objects.count(),
        PropertyListing.objects.filter(status="published").count(),
    )


def fresh(account, view):
    return selectors.get_owned_listing(user(account), view.id)


@pytest.fixture
def bus(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    seen = []
    events.subscribe(ListingPublished, seen.append)
    return seen


# --- the happy path -------------------------------------------------------------------------


def test_publishing_a_ready_draft_writes_everything(bus, django_capture_on_commit_callbacks):
    account = make_account()
    view = ready_draft(account)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        done = publish(account, view, now=NOW)
        assert bus == []  # nothing before the commit
    assert len(callbacks) == 1
    assert bus == [ListingPublished(listing_id=view.id, account_id=account.id)]
    assert (done.status, done.visibility, done.revision) == (
        "published",
        "public",
        view.revision + 1,
    )
    assert done.fields == fresh(account, view).fields
    row = PropertyListing.objects.get()
    assert (row.status, row.revision, row.updated_at) == ("published", view.revision + 1, NOW)
    event = PublicationEvent.objects.get()
    assert (event.account_id, event.listing_id, event.kind, event.counted) == (
        account.id,
        view.id,
        "initial",
        True,
    )
    assert (event.idempotency_key, event.created_at, event.correction_used) == (KEY, NOW, False)
    assert event.identity_fingerprint == publication.identity_fingerprint(view)
    revision = ListingRevision.objects.get(revision=row.revision)
    assert (revision.changes, revision.origin) == ({"status": "published"}, "manual")
    record = AuditRecord.objects.get(action="listing.published")
    assert (record.target_type, record.target_id, record.actor_id) == (
        "listing",
        view.id,
        account.id,
    )
    assert record.metadata == {} and record.reason == ""
    assert IdempotencyRecord.objects.get().result_ref == f"listing:{view.id}"


def test_a_published_listing_is_public_for_an_active_owner_only():
    account = make_account()
    view = ready_draft(account)
    with pytest.raises(DomainError):
        selectors.public_listing(SYSTEM, view.id)  # a Draft is not public
    publish(account, view)
    assert selectors.public_listing(Actor.visitor(), view.id).id == view.id
    assert fresh(account, view).visibility == "public"
    set_account_state(account.id, "unverified")
    with pytest.raises(DomainError) as hidden:
        selectors.public_listing(Actor.visitor(), view.id)
    assert hidden.value.code == "not_found"
    assert fresh(account, view).visibility == "private"


def test_the_draft_expiry_markers_are_cleared():
    account = make_account()
    view = ready_draft(account)
    PropertyListing.objects.update(delete_after=NOW, expiry_notice_sent_at=NOW)
    publish(account, view)
    row = PropertyListing.objects.get()
    assert (row.delete_after, row.expiry_notice_sent_at) == (None, None)


def test_emitting_with_no_subscribers_schedules_nothing(
    monkeypatch, django_capture_on_commit_callbacks
):
    monkeypatch.setattr(events, "_registry", {})
    account = make_account()
    view = ready_draft(account)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        publish(account, view)
    assert callbacks == []


def test_nothing_external_happens_inside_the_transaction(monkeypatch):
    account = make_account()
    view = ready_draft(account)

    def boom(*args, **kwargs):
        raise AssertionError("an external call inside the publish transaction")

    for module, name in (
        (runtime, "submit_heavy"),
        (runtime, "submit_interactive"),
        (kernel_mail, "send"),
        (kernel_mail, "deliver"),
        (ai_log, "record_ai_call"),
        (kernel_media, "store_image"),
        (kernel_media, "process_upload"),
        (kernel_media, "store_processed"),
        (kernel_media, "delete_asset"),
        (kernel_media, "discard_file"),
        (kernel_storage, "discard"),
        (kernel_storage, "get_storage"),
        (notifications, "notify"),
    ):
        monkeypatch.setattr(module, name, boom)
    assert publish(account, view).status == "published"
    assert mail.outbox == []


def test_the_publish_module_imports_nothing_external():
    """No mail, AI, storage, upload or notification module is even imported by `publication`."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(publication.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    banned = (
        "mail",
        "ai_log",
        "storage",
        "media",
        "runtime",
        "notifications",
        "images",
        "assistant",
    )
    hits = sorted(name for name in imported if any(part in banned for part in name.split(".")))
    assert hits == [], hits


# --- what is required -----------------------------------------------------------------------


REQUIRED_BY_KIND = [
    (HOUSE, BASE_REQUIRED | UNIT),
    (RENT_HOUSE, BASE_REQUIRED | UNIT | {"rental_period"}),
    (APARTMENT, BASE_REQUIRED | UNIT),
    (COMMERCIAL_PLOT, BASE_REQUIRED),
    (PLOT_FILE, BASE_REQUIRED),
    (SHOP, BASE_REQUIRED | {"rental_period"}),
]


@pytest.mark.parametrize(("values", "required"), REQUIRED_BY_KIND)
def test_a_bare_draft_lists_every_required_item_at_once(values, required):
    account = make_account()
    bare = services.create_draft(user(account), *values)
    assert problems(account, bare) == {
        **dict.fromkeys(required, "required"),
        "images": "images_required",
    }
    assert nothing_written() == (0, 0, 0, 0)


@pytest.mark.parametrize(("values", "required"), REQUIRED_BY_KIND)
def test_every_required_item_alone_blocks_and_a_complete_draft_publishes(values, required):
    account = make_account()
    view = ready_draft(account, values)
    original = {name: view.fields[name] for name in required}
    for name in sorted(required):
        cleared = services.apply_updates(
            user(account), view.id, fresh(account, view).revision, {name: None}
        )
        expected = {"city", "locality"} if name == "city" else {name}  # a city carries its locality
        assert problems(account, cleared, key=f"key-{name}") == dict.fromkeys(expected, "required")
        services.apply_updates(user(account), view.id, cleared.revision, original)
    assert publish(account, fresh(account, view)).status == "published"


def test_the_list_is_the_registry_and_the_registry_change_is_followed(monkeypatch):
    import dataclasses

    account = make_account()
    view = ready_draft(account, extra={"description": None})
    real = registry.fields()

    def stricter():
        return tuple(
            dataclasses.replace(f, requirement="required_for_publication")
            if f.id == "description"
            else f
            for f in real
        )

    monkeypatch.setattr(registry, "fields", stricter)
    assert problems(account, view) == {"description": "required"}


def test_no_image_is_its_own_error_with_a_static_sentence():
    account = make_account()
    view = ready_draft(account, images=0)
    assert problems(account, view) == {"images": "images_required"}
    assert field_message("images_required") == "Add at least one image."
    assert field_message("confirm_required") == "Confirm this value before publishing."


def test_a_ghost_link_is_not_an_image():
    account = make_account()
    view = ready_draft(account, images=0)
    from tests.listings.support import add_images

    add_images(view.id, count=0, ghosts=2)
    assert problems(account, view) == {"images": "images_required"}


def test_a_live_image_among_ghosts_is_enough():
    account = make_account()
    view = ready_draft(account, images=1)
    from tests.listings.support import add_images

    add_images(view.id, count=0, ghosts=1)  # position clash is fine: a new link, no asset row
    assert selectors.live_image_count(view.id) == 1
    assert publish(account, view).status == "published"


def test_optional_data_never_blocks():
    account = make_account()
    view = ready_draft(account)
    assert all(
        view.fields[name] is None for name in ("description", "street", "society", "amenities")
    )
    assert publish(account, view).status == "published"


def test_a_locality_of_another_city_is_refused():
    account = make_account()
    view = ready_draft(account)
    ListingLocation.objects.update(locality=places()["other"].id)
    assert problems(account, view) == {"locality": "locality_not_in_city"}


def test_a_stored_value_is_judged_again_by_the_current_rules(monkeypatch):
    import dataclasses

    account = make_account()
    view = ready_draft(account)
    real = registry.field

    def tighter(name):
        found = real(name)
        return dataclasses.replace(found, min_length=60) if name == "title" else found

    monkeypatch.setattr(registry, "field", tighter)
    monkeypatch.setattr(
        registry, "fields", lambda: tuple(tighter(f.id) for f in registry.load().fields)
    )
    assert problems(account, view) == {"title": "too_short"}


def test_an_inactive_city_blocks():
    account = make_account()
    view = ready_draft(account)
    City.objects.filter(id=places()["karachi"].id).update(is_active=False)
    assert set(problems(account, view)) == {"city", "locality"}


def test_a_rental_price_basis_on_a_sale_is_refused():
    account = make_account()
    view = ready_draft(account)
    from apps.listings.models import ListingTerms

    ListingTerms.objects.update(price_basis="rental_period")
    assert problems(account, view) == {"price_basis": "invalid_choice"}


# --- unconfirmed Assistant values -----------------------------------------------------------


def test_an_unconfirmed_assistant_value_of_a_sensitive_field_blocks_until_confirmed():
    account = make_account()
    view = ready_draft(account, extra={"street": STREET})
    services.apply_updates(
        user(account), view.id, view.revision, {"street": "Plum Road"}, origin="ai"
    )
    view = fresh(account, view)
    assert dict(view.provenance["street"]) == {"origin": "ai", "confirmed": False}
    assert problems(account, view) == {"street": "confirm_required"}
    services.apply_updates(user(account), view.id, view.revision, {"street": "Plum Road"})
    assert publish(account, fresh(account, view)).status == "published"


def test_an_unconfirmed_value_of_an_identity_field_blocks():
    account = make_account()
    view = ready_draft(account)
    row = PropertyListing.objects.get()
    row.provenance = {**row.provenance, "category": {"origin": "ai", "confirmed": False}}
    row.save(update_fields=["provenance"])
    assert problems(account, fresh(account, view)) == {"category": "confirm_required"}


def test_an_unconfirmed_value_of_an_ordinary_field_or_a_confirmed_ai_one_does_not_block():
    account = make_account()
    view = ready_draft(account)
    row = PropertyListing.objects.get()
    row.provenance = {
        **row.provenance,
        "title": {"origin": "ai", "confirmed": False},
        "street": {"origin": "ai", "confirmed": True},
    }
    row.save(update_fields=["provenance"])
    assert publish(account, fresh(account, view)).status == "published"


# --- the other refusals ---------------------------------------------------------------------


def test_a_stale_revision_carries_the_current_listing_and_writes_nothing():
    account = make_account()
    view = ready_draft(account)
    error = refused(account, view, services.StaleRevision, base=view.revision - 1)
    assert error.code == "stale_revision" and error.current.revision == view.revision
    assert nothing_written() == (0, 0, 0, 0)


def test_a_listing_that_is_not_a_draft_is_a_conflict():
    account = make_account()
    view = ready_draft(account)
    done = publish(account, view)
    assert refused(account, done, key="key-0002").code == "conflict"
    assert nothing_written() == (1, 1, 1, 1)


@pytest.mark.parametrize(
    ("state", "code"),
    [
        ("unverified", "email_not_verified"),
        ("suspended", "publishing_unavailable"),
        ("deactivated", "forbidden"),
    ],
)
def test_only_an_active_account_publishes(state, code):
    account = make_account()
    view = ready_draft(account)
    set_account_state(account.id, state)
    assert refused(account, view).code == code
    assert nothing_written() == (0, 0, 0, 0)


def test_the_refusal_reasons_are_static_and_distinct():
    from apps.kernel.errors import get_error

    codes = ("email_not_verified", "publishing_unavailable", "quota_exhausted")
    assert len({get_error(c).message for c in codes}) == 3
    assert [get_error(c).status for c in codes] == [403, 403, 409]


def test_another_account_an_admin_a_visitor_and_bad_ids_are_refused_alike():
    owner = make_account()
    view = ready_draft(owner)
    other = make_account(random_email())
    for actor in (user(other), Actor.system()):
        with pytest.raises(DomainError) as caught:
            publication.publish(actor, view.id, view.revision, KEY)
        assert caught.value.code == "not_found"
    with pytest.raises(DomainError) as visitor:
        publication.publish(Actor.visitor(), view.id, view.revision, KEY)
    assert visitor.value.code == "unauthenticated"
    for bad in ("nope", str(uuid.uuid4()), view.id.hex, None, 5):
        with pytest.raises(DomainError) as caught:
            publication.publish(user(owner), bad, view.revision, KEY)
        assert caught.value.code == "not_found"
    assert nothing_written() == (0, 0, 0, 0)


@pytest.mark.parametrize(
    ("base", "key", "expected"),
    [
        (None, KEY, {"base_revision": "required"}),
        ("2", KEY, {"base_revision": "invalid"}),
        (0, KEY, {"base_revision": "invalid"}),
        (True, KEY, {"base_revision": "invalid"}),
        (2, None, {"idempotency_key": "required"}),
        (2, "", {"idempotency_key": "invalid"}),
        (2, "a b", {"idempotency_key": "invalid"}),
        (2, "x" * 129, {"idempotency_key": "invalid"}),
        (2, "é", {"idempotency_key": "invalid"}),
        (2, 5, {"idempotency_key": "invalid"}),
        (None, "", {"base_revision": "required", "idempotency_key": "invalid"}),
    ],
)
def test_the_request_shape_is_judged_before_anything_is_written(base, key, expected):
    account = make_account()
    view = ready_draft(account)
    with pytest.raises(publication.FieldsInvalid) as caught:
        publication.publish(user(account), view.id, base, key)
    assert caught.value.fields == expected and nothing_written() == (0, 0, 0, 0)


def test_the_longest_and_the_odd_key_characters_are_accepted():
    account = make_account()
    view = ready_draft(account)
    assert publish(account, view, key="A" * 64 + "._:-" + "z" * 60).status == "published"


# --- the allowance --------------------------------------------------------------------------


def test_the_third_publication_of_an_individual_is_refused_with_their_own_numbers():
    account = make_account()
    for index in range(2):
        publish(account, ready_draft(account), key=f"key-{index}", now=NOW)
    view = ready_draft(account)
    error = refused(account, view, publication.QuotaExhausted, key="key-9", now=NOW)
    assert (error.code, error.limit, error.used) == ("quota_exhausted", 2, 2)
    assert error.resets_at == datetime(2026, 12, 1, tzinfo=publication.quota.KARACHI)
    assert error.resets_at.utcoffset() == timedelta(hours=5)
    assert fresh(account, view).status == "draft"
    assert PublicationEvent.objects.count() == 2
    assert IdempotencyRecord.objects.filter(key="key-9").count() == 0


def test_an_agency_has_ten():
    account = make_account(account_type="agency")
    for index in range(10):
        publish(account, ready_draft(account), key=f"key-{index}", now=NOW)
    error = refused(account, ready_draft(account), publication.QuotaExhausted, key="key-x", now=NOW)
    assert (error.limit, error.used) == (10, 10)


def test_an_exhausted_account_still_gets_the_validation_errors_first():
    account = make_account()
    for index in range(2):
        publish(account, ready_draft(account), key=f"key-{index}", now=NOW)
    bare = services.create_draft(user(account), *HOUSE)
    assert "title" in problems(account, bare, now=NOW)


def test_a_later_higher_limit_allows_it_from_its_effective_time():
    account = make_account()
    for index in range(2):
        publish(account, ready_draft(account), key=f"key-{index}", now=NOW)
    view = ready_draft(account)
    start = NOW + timedelta(days=1)
    ListingQuotaPolicy.objects.filter(account_type="individual").update(effective_to=start)
    ListingQuotaPolicy.objects.create(
        account_type="individual", monthly_limit=3, effective_from=start
    )
    assert refused(account, view, publication.QuotaExhausted, key="key-a", now=NOW).limit == 2
    assert publish(account, view, key="key-b", now=start).status == "published"


def test_last_months_events_do_not_count_and_the_karachi_boundary_is_exact():
    account = make_account()
    for at in (
        datetime(2026, 10, 31, 18, 59, 59, tzinfo=UTC),  # still October in Karachi
        datetime(2026, 10, 1, tzinfo=UTC),
    ):
        PublicationEvent.objects.create(
            account_id=account.id,
            listing_id=uuid.uuid4(),
            kind="initial",
            counted=True,
            created_at=at,
        )
    view = ready_draft(account)
    assert publish(account, view, now=NOW).status == "published"
    other = ready_draft(account)
    PublicationEvent.objects.create(
        account_id=account.id,
        listing_id=uuid.uuid4(),
        kind="initial",
        counted=True,
        created_at=datetime(2026, 10, 31, 19, 0, tzinfo=UTC),  # 1 November 00:00 in Karachi
    )
    assert refused(account, other, publication.QuotaExhausted, key="key-2", now=NOW).used == 2


def test_uncounted_events_and_other_accounts_do_not_consume():
    account, other = make_account(), make_account(random_email())
    for owner, kind, counted in (
        (account, "republish_free", False),
        (account, "seed", False),
        (other, "initial", True),
        (other, "initial", True),
    ):
        PublicationEvent.objects.create(
            account_id=owner.id, listing_id=uuid.uuid4(), kind=kind, counted=counted, created_at=NOW
        )
    assert publish(account, ready_draft(account), now=NOW).status == "published"
    assert publish(account, ready_draft(account), key="key-2", now=NOW).status == "published"


def test_a_missing_policy_is_a_server_fault_not_a_free_publication():
    account = make_account()
    view = ready_draft(account)
    ListingQuotaPolicy.objects.all().delete()
    with pytest.raises(publication.quota.QuotaPolicyMissing):
        publish(account, view)
    assert nothing_written() == (0, 0, 0, 0)


# --- idempotency ----------------------------------------------------------------------------


def test_a_replay_returns_the_listing_and_charges_once(bus, django_capture_on_commit_callbacks):
    account = make_account()
    view = ready_draft(account)
    with django_capture_on_commit_callbacks(execute=True):
        first = publish(account, view)
    with django_capture_on_commit_callbacks(execute=True):
        again = publish(account, view)
    assert again == fresh(account, view) and again.fields == first.fields
    assert nothing_written() == (1, 1, 1, 1) and len(bus) == 1
    assert ListingRevision.objects.filter(changes={"status": "published"}).count() == 1


def test_a_replay_works_even_when_the_allowance_is_now_used_up():
    account = make_account()
    first = ready_draft(account)
    publish(account, first, key="key-first")
    publish(account, ready_draft(account), key="key-second")
    assert publish(account, first, key="key-first").status == "published"
    assert PublicationEvent.objects.count() == 2


def test_the_same_key_for_other_content_is_refused():
    account = make_account()
    view, second = ready_draft(account), ready_draft(account)
    publish(account, view)
    assert refused(account, second, key=KEY).code == "idempotency_key_reused"
    assert refused(account, view, key=KEY, base=view.revision + 5).code == "idempotency_key_reused"
    assert nothing_written() == (1, 1, 1, 1)
    assert fresh(account, second).status == "draft"


def test_a_key_is_scoped_to_the_account():
    one, two = make_account(), make_account(random_email())
    assert publish(one, ready_draft(one)).status == "published"
    assert publish(two, ready_draft(two)).status == "published"
    assert PublicationEvent.objects.count() == 2


def test_a_refused_attempt_keeps_no_claim_and_the_key_can_be_retried():
    account = make_account()
    view = ready_draft(account, images=0)
    problems(account, view)
    assert IdempotencyRecord.objects.count() == 0
    from tests.listings.support import add_images

    add_images(view.id)
    assert publish(account, view).status == "published"


def test_a_key_older_than_a_day_replays_from_its_event_and_completes_the_claim():
    account = make_account()
    first, second = ready_draft(account), ready_draft(account)
    publish(account, first, now=NOW)
    later = NOW + timedelta(hours=25)
    again = publish(account, first, now=later)  # the claim expired; the event still answers
    assert again.status == "published" and PublicationEvent.objects.count() == 1
    assert IdempotencyRecord.objects.get().result_ref == f"listing:{first.id}"  # completed
    assert publish(account, first, now=later).status == "published"  # now an ordinary replay
    assert refused(account, second, now=later).code == "idempotency_key_reused"
    assert fresh(account, second).status == "draft"
    assert AuditRecord.objects.filter(action="listing.published").count() == 1


def test_a_purged_claim_replays_from_the_event():
    account = make_account()
    view = ready_draft(account)
    publish(account, view)
    IdempotencyRecord.objects.all().delete()  # purged after seven days
    assert publish(account, view).status == "published"
    assert PublicationEvent.objects.count() == 1 and IdempotencyRecord.objects.count() == 1


def test_a_claim_without_a_result_is_a_conflict_and_never_redoes_the_work():
    account = make_account()
    view = ready_draft(account)
    IdempotencyRecord.objects.create(
        scope=publication.SCOPE,
        account_id=account.id,
        key=KEY,
        request_hash=idempotency.request_fingerprint(
            {"listing_id": view.id, "base_revision": view.revision}
        ),
        created_at=timezone.now(),
    )
    assert refused(account, view).code == "conflict"
    assert nothing_written()[0] == 0


@pytest.mark.parametrize("ref", ["listing:not-a-uuid", "other:" + str(uuid.UUID(int=5)), "junk"])
def test_a_malformed_stored_reference_is_not_found(ref):
    account = make_account()
    view = ready_draft(account)
    IdempotencyRecord.objects.create(
        scope=publication.SCOPE,
        account_id=account.id,
        key=KEY,
        request_hash=idempotency.request_fingerprint(
            {"listing_id": view.id, "base_revision": view.revision}
        ),
        result_ref=ref,
        created_at=timezone.now(),
    )
    assert refused(account, view).code == "not_found"


def test_a_replay_of_a_listing_the_caller_no_longer_has_is_not_found():
    account = make_account()
    view = ready_draft(account)
    publish(account, view)
    IdempotencyRecord.objects.update(result_ref=f"listing:{uuid.uuid4()}")
    assert refused(account, view).code == "not_found"


# --- all or nothing -------------------------------------------------------------------------


def fail_at(monkeypatch, target):
    def boom(*args, **kwargs):
        raise RuntimeError("injected")

    if target == "event":
        monkeypatch.setattr(PublicationEvent.objects, "create", boom)
    elif target == "status":
        monkeypatch.setattr(PropertyListing, "save", boom)
    elif target == "revision":
        monkeypatch.setattr(ListingRevision.objects, "create", boom)
    elif target == "audit":
        monkeypatch.setattr(audit, "record", boom)
    elif target == "claim":
        monkeypatch.setattr(idempotency_claim_type(), "complete", boom)


def idempotency_claim_type():
    from apps.kernel.idempotency import Claim

    return Claim


@pytest.mark.parametrize("target", ["event", "status", "revision", "audit", "claim"])
def test_a_failure_in_any_step_leaves_nothing_half_applied(
    target, monkeypatch, bus, django_capture_on_commit_callbacks
):
    account = make_account()
    view = ready_draft(account)
    before = (PropertyListing.objects.get().revision, ListingRevision.objects.count())
    with monkeypatch.context() as patched:
        fail_at(patched, target)
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            with pytest.raises(RuntimeError):
                publish(account, view)
    row = PropertyListing.objects.get()
    assert (row.status, row.revision) == ("draft", before[0])
    assert ListingRevision.objects.count() == before[1]
    assert nothing_written() == (0, 0, 0, 0)
    assert callbacks == [] and bus == []
    assert publish(account, view).status == "published"  # and the same key works afterwards


def test_a_failing_subscriber_does_not_undo_the_publication(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})

    def failing(event):
        raise RuntimeError("subscriber")

    events.subscribe(ListingPublished, failing)
    account = make_account()
    view = ready_draft(account)
    publish(account, view)
    assert PropertyListing.objects.get().status == "published"


# --- the fingerprint ------------------------------------------------------------------------


GOLDEN = "81c4240f92b2e7b507e5570d92eb2cc8dd59f455f6c0f029c26009bab1bc38dd"


def fp(**fields):
    return publication.identity_fingerprint(SimpleNamespace(fields=fields))


def test_the_fingerprint_matches_a_golden_vector():
    expected = hashlib.sha256(
        b'{"area_unit":null,"area_value":null,"category":"house","city":null,"locality":null,'
        b'"marla_basis":null,"purpose":"sale","subtype":"house"}'
    ).hexdigest()
    assert fp(purpose="sale", category="house", subtype="house") == expected == GOLDEN


def test_a_single_identity_difference_changes_the_fingerprint():
    base = fp(purpose="sale", category="plot", subtype="residential_plot")
    assert fp(purpose="rent", category="plot", subtype="residential_plot") != base
    assert fp(purpose="sale", category="plot", subtype="commercial_plot") != base
    assert fp(purpose="sale", category="commercial", subtype="residential_plot") != base
    assert fp(category="plot", subtype="residential_plot", purpose="sale") == base  # key order


IDENTITY_BASE = {
    "purpose": "sale",
    "category": "plot",
    "subtype": "residential_plot",
    "city": "11111111-1111-4111-8111-111111111111",
    "locality": "22222222-2222-4222-8222-222222222222",
    "area_value": "10.0000",
    "area_unit": "marla",
    "marla_basis": "225",
}


@pytest.mark.parametrize(
    ("name", "other"),
    [
        ("city", "33333333-3333-4333-8333-333333333333"),
        ("locality", "44444444-4444-4444-8444-444444444444"),
        ("area_value", "11.0000"),
        ("area_unit", "kanal"),
        ("marla_basis", "250"),
    ],
)
def test_changing_each_new_identity_field_changes_the_fingerprint(name, other):
    assert fp(**IDENTITY_BASE) != fp(**{**IDENTITY_BASE, name: other})
    assert fp(**IDENTITY_BASE) == fp(**IDENTITY_BASE)  # and the base is stable


def test_the_fingerprint_is_stable_and_covers_the_identity_fields_only():
    account = make_account()
    view = ready_draft(account)
    one = publication.identity_fingerprint(view)
    assert len(one) == 64 and int(one, 16) >= 0
    services.apply_updates(user(account), view.id, view.revision, {"title": "Another family house"})
    assert publication.identity_fingerprint(fresh(account, view)) == one  # not an identity field
    # Story 2.17: the same flag is the identity locked while Published (and 2.18's quota identity)
    assert {f.id for f in registry.fields() if f.quota_identity} == {
        "purpose",
        "category",
        "subtype",
        "city",
        "locality",
        "area_value",
        "area_unit",
        "marla_basis",
    }


@pytest.fixture
def wide(monkeypatch):
    """Every field counts as identity, so any value shape can be fingerprinted."""
    import dataclasses

    flagged = tuple(dataclasses.replace(f, quota_identity=True) for f in registry.fields())
    monkeypatch.setattr(registry, "fields", lambda: flagged)


def test_text_is_nfc_and_trimmed_and_numbers_are_canonical(wide):
    assert fp(title="Caf\u00e9") == fp(title="Cafe\u0301") == fp(title="  Caf\u00e9 \n")
    assert fp(title="Cafe") != fp(title="Café")
    assert (
        fp(area_value="10.0000") == fp(area_value="10") == fp(area_value=10) == fp(area_value=10.0)
    )
    assert fp(area_value="-0.00") == fp(area_value="0") == fp(area_value=0)
    assert fp(area_value="10.5") != fp(area_value="10")
    assert fp(asking_price=Decimal("1E+3")) == fp(asking_price="1000.00")


def test_lists_are_ordered_and_maps_ignore_order(wide):
    assert fp(amenities=["a", "b"]) != fp(amenities=["b", "a"])
    assert fp(amenities={"x": "present", "y": "none"}) == fp(
        amenities={"y": "none", "x": "present"}
    )


def test_booleans_are_not_numbers_and_none_is_not_zero(wide):
    assert len({fp(lift=True), fp(lift=1), fp(lift=None), fp(lift=0), fp(lift=False)}) == 5


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), Decimal("NaN"), Decimal("-Infinity")])
def test_non_finite_numbers_are_refused(wide, bad):
    with pytest.raises(ValueError):
        fp(area_value=bad)


def test_the_fingerprint_never_appears_in_a_response_or_log():
    account = make_account()
    view = ready_draft(account)
    done = publish(account, view)
    assert publication.identity_fingerprint(view) not in repr(done)
    assert "fingerprint" not in repr(AuditRecord.objects.get(action="listing.published").metadata)


# --- concurrency ----------------------------------------------------------------------------


def run_threads(count, work):
    barrier, outcomes = threading.Barrier(count), []
    lock = threading.Lock()

    def target(index):
        try:
            barrier.wait(timeout=30)
            result = ("ok", work(index))
        except DomainError as exc:
            result = (exc.code, None)
        except Exception as exc:  # noqa: BLE001
            result = (type(exc).__name__, None)
        finally:
            connection.close()
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=target, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads)
    return sorted(kind for kind, _ in outcomes), outcomes


def one_slot_left(account):
    PublicationEvent.objects.create(
        account_id=account.id, listing_id=uuid.uuid4(), kind="initial", counted=True
    )


@pytest.mark.django_db(transaction=True)
def test_twenty_requests_for_one_slot_publish_exactly_once():
    account = make_account()
    one_slot_left(account)
    drafts = [ready_draft(account), ready_draft(account)]

    def work(index):
        view = drafts[index % 2]
        return publication.publish(user(account), view.id, view.revision, f"key-{index:02d}")

    kinds, _ = run_threads(20, work)
    assert kinds.count("ok") == 1, kinds
    assert set(kinds) <= {"ok", "quota_exhausted", "conflict", "stale_revision"}
    assert PublicationEvent.objects.filter(kind="initial").count() == 2  # the old one and one new
    assert PropertyListing.objects.filter(status="published").count() == 1
    assert AuditRecord.objects.filter(action="listing.published").count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_drafts_of_one_account_race_for_one_slot():
    account = make_account()
    one_slot_left(account)
    drafts = [ready_draft(account), ready_draft(account)]

    def work(index):
        view = drafts[index]
        return publication.publish(user(account), view.id, view.revision, f"key-{index}")

    kinds, _ = run_threads(2, work)
    assert kinds == ["ok", "quota_exhausted"]
    assert PropertyListing.objects.filter(status="published").count() == 1


@pytest.mark.django_db(transaction=True)
def test_the_same_key_replayed_concurrently_charges_once():
    account = make_account()
    view = ready_draft(account)

    def work(_):
        return publication.publish(user(account), view.id, view.revision, KEY)

    kinds, outcomes = run_threads(20, work)
    assert kinds == ["ok"] * 20, kinds
    assert PublicationEvent.objects.count() == 1
    assert {out.revision for _, out in outcomes} == {view.revision + 1}
    assert AuditRecord.objects.filter(action="listing.published").count() == 1
    assert IdempotencyRecord.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_refused_attempts_are_all_refused_and_consume_nothing():
    account = make_account()
    view = ready_draft(account, images=0)  # invalid: every attempt is refused, none charged

    def work(_):
        return publication.publish(user(account), view.id, view.revision, KEY)

    kinds, _ = run_threads(6, work)
    assert kinds == ["validation_error"] * 6
    assert nothing_written() == (0, 0, 0, 0)


def test_the_clock_default_is_now():
    account = make_account()
    view = ready_draft(account)
    before = timezone.now()
    publish(account, view)
    assert before <= PublicationEvent.objects.get().created_at <= timezone.now()


# --- review additions: AI values, stored data, clock, precedence ----------------------------


def set_provenance(**entries):
    row = PropertyListing.objects.order_by("-created_at").first()  # the newest Listing
    row.provenance = {**row.provenance, **entries}
    row.save(update_fields=["provenance"])


@pytest.mark.parametrize(
    "entry",
    [
        {"origin": "ai", "confirmed": False},
        {"origin": "ai", "confirmed": None},
        {"origin": "ai"},  # `confirmed` missing: unconfirmed, and no KeyError
        {"origin": "ai", "confirmed": "yes"},
    ],
)
def test_an_ai_value_that_is_not_confirmed_true_blocks(entry):
    account = make_account()
    view = ready_draft(account, extra={"street": STREET})
    set_provenance(street=entry)
    assert problems(account, fresh(account, view)) == {"street": "confirm_required"}


def test_several_unconfirmed_fields_are_all_named():
    account = make_account()
    view = ready_draft(account, extra={"street": STREET, "house_number": "Plum 12"})
    set_provenance(
        street={"origin": "ai", "confirmed": False},
        house_number={"origin": "ai"},
        category={"origin": "ai", "confirmed": None},
    )
    assert problems(account, fresh(account, view)) == dict.fromkeys(
        ("street", "house_number", "category"), "confirm_required"
    )


def test_an_unconfirmed_entry_for_an_empty_field_is_ignored_and_required_wins(monkeypatch):
    import dataclasses

    account = make_account()
    view = ready_draft(account, extra={"street": STREET})
    services.apply_updates(user(account), view.id, view.revision, {"street": None})
    set_provenance(street={"origin": "ai", "confirmed": False})
    assert publish(account, fresh(account, view)).status == "published"  # nothing to confirm
    other = ready_draft(account, extra={"title": TITLE_OK})
    services.apply_updates(user(account), other.id, other.revision, {"title": None})
    set_provenance(title={"origin": "ai", "confirmed": False})
    real = registry.fields()
    flagged = tuple(
        dataclasses.replace(f, quota_identity=True) if f.id == "title" else f for f in real
    )
    monkeypatch.setattr(registry, "fields", lambda: flagged)
    monkeypatch.setattr(
        registry,
        "field",
        lambda name: next((f for f in flagged if f.id == name), None),
    )
    assert problems(account, fresh(account, other), key="key-2") == {"title": "required"}


def test_malformed_provenance_never_breaks_publication():
    account = make_account()
    view = ready_draft(account)
    for bad in ("junk", [], {"street": "ai"}, {"nope": {"origin": "ai"}}, {3: {"origin": "ai"}}):
        PropertyListing.objects.update(provenance=bad)
        assert problems_or_none(account, view) is None
    PropertyListing.objects.update(provenance={})


def problems_or_none(account, view):
    try:
        publication.problems_of(PropertyListing.objects.get(), fresh(account, view), True)
    except Exception:  # noqa: BLE001
        raise
    return None


TITLE_OK = "Family house near the park"


def rich_values(values):
    """A valid value for every field that applies to the Subtype, from the registry's own rules."""
    from apps.catalog import selectors as catalog

    purpose, category, subtype = values
    out = {}
    for f in registry.fields():
        if f.derived or f.id in {"purpose", "category", "subtype", "city", "locality"}:
            continue
        if not f.applies_to(purpose, category, subtype):
            continue
        if f.type == "enum":
            out[f.id] = f.values_for(purpose, subtype)[0]
        elif f.type == "integer":
            out[f.id] = int(f.minimum)
        elif f.type == "boolean":
            out[f.id] = True
        elif f.type == "money":
            out[f.id] = format(max(f.minimum, Decimal("1")), ".2f")
        elif f.type == "decimal":
            out[f.id] = format(max(f.minimum, Decimal("1")), f".{f.scale}f")
        elif f.type == "date":
            out[f.id] = "2030-01-01"
        elif f.type == "text":
            out[f.id] = "Quiet green words"
        elif f.type == "amenities":
            out[f.id] = {code: "present" for code in sorted(catalog.amenity_codes())[:3]}
    out["area_unit"], out["marla_basis"] = "marla", "225"
    out["area_value"] = "10.0000"
    out["latitude"], out["longitude"] = "24.860000", "67.010000"
    if "floor_number" in out:
        out["floor_number"], out["building_floors"] = 1, 5
    if purpose == "rent":
        out["price_basis"] = "rental_period"
    return out


RICH = [
    HOUSE,
    RENT_HOUSE,
    APARTMENT,
    ("sale", "plot", "residential_plot"),
    COMMERCIAL_PLOT,
    PLOT_FILE,
    SHOP,
    ("rent", "commercial", "office"),
    ("sale", "commercial", "shop"),
]


@pytest.mark.parametrize("values", RICH)
def test_a_rich_valid_draft_with_every_section_filled_publishes(values):
    account = make_account()
    extra = rich_values(values)
    view = ready_draft(account, values, extra=extra)
    assert all(view.fields[name] is not None for name in extra), "every section is filled"
    assert judged(account, view) == {}
    assert publish(account, view).status == "published"


def judged(account, view, **stored):
    """`problems_of` for the stored Listing with some stored values replaced (the database refuses
    most inconsistent rows, so cross-field rules are exercised on the view)."""
    fake = SimpleNamespace(
        fields={**view.fields, **stored}, provenance=view.provenance, media=view.media
    )
    return publication.problems_of(PropertyListing.objects.get(), fake, True)


def test_invalid_stored_optional_data_blocks_with_field_level_errors():
    account = make_account()
    view = ready_draft(account, APARTMENT, extra={"floor_number": 1, "building_floors": 5})
    assert judged(account, view) == {}
    assert judged(account, view, floor_number=9) == {
        "floor_number": "invalid_floor",
        "building_floors": "invalid_floor",
    }
    assert judged(account, view, latitude="24.500000", longitude=None) == {"longitude": "invalid"}
    assert judged(account, view, longitude="67.010000") == {"latitude": "invalid"}
    assert judged(account, view, latitude="95.000000", longitude="67.010000") == {
        "latitude": "out_of_range"
    }


def test_a_marla_basis_on_another_unit_blocks():
    account = make_account()
    view = ready_draft(account)
    assert view.fields["marla_basis"] == "225"
    assert judged(account, view, area_unit="sqft") == {"marla_basis": "not_applicable"}


def test_a_stale_value_that_does_not_apply_blocks():
    from apps.listings.models import ListingPlot

    account = make_account()
    view = ready_draft(account)
    ListingPlot.objects.create(listing_id=view.id, corner_plot=True)  # a plot detail on a house
    assert problems(account, view) == {"corner_plot": "not_applicable"}


def test_the_month_is_read_after_the_lock_wait(monkeypatch):
    """A request that starts before Karachi midnight and gets the lock after it counts in the
    new month: the allowance and the event use the later instant."""
    account = make_account()
    for index in range(2):  # November is used up
        publish(account, ready_draft(account), key=f"key-{index}", now=NOW)
    view = ready_draft(account)
    before = datetime(2026, 11, 30, 18, 59, 59, tzinfo=UTC)  # 23:59:59 in Karachi
    after = datetime(2026, 11, 30, 19, 0, 1, tzinfo=UTC)  # 00:00:01 on 1 December
    clock = iter([before, after])
    monkeypatch.setattr(publication, "timezone", SimpleNamespace(now=lambda: next(clock)))
    assert publish(account, view, key="key-9").status == "published"
    assert PublicationEvent.objects.get(idempotency_key="key-9").created_at == after


def test_a_passed_clock_is_used_as_given(monkeypatch):
    account = make_account()
    view = ready_draft(account)
    monkeypatch.setattr(
        publication, "timezone", SimpleNamespace(now=lambda: pytest.fail("the clock was read"))
    )
    assert publish(account, view, now=NOW).status == "published"
    assert PublicationEvent.objects.get().created_at == NOW


def test_the_account_is_locked_before_the_allowance_is_read(monkeypatch):
    account = make_account()
    view = ready_draft(account)
    calls = []
    real_lock, real_allowance = publication.lock_account, publication.quota.allowance_for

    def lock(account_id):
        calls.append("lock")
        return real_lock(account_id)

    def allowance(*args, **kwargs):
        calls.append("allowance")
        assert calls[:-1] == ["lock"], "the allowance was read before the account lock"
        held = Account.objects.select_for_update(nowait=True).filter(id=account.id).exists()
        assert held  # and this same transaction still owns the row
        return real_allowance(*args, **kwargs)

    monkeypatch.setattr(publication, "lock_account", lock)
    monkeypatch.setattr(publication.quota, "allowance_for", allowance)
    publish(account, view)
    assert calls == ["lock", "allowance"]


def test_an_unverified_owner_with_a_bare_draft_is_told_to_verify_first():
    account = make_account()
    bare = services.create_draft(user(account), *HOUSE)
    set_account_state(account.id, "unverified")
    assert refused(account, bare).code == "email_not_verified"


def test_a_stale_revision_on_a_published_listing_is_stale_not_conflict():
    account = make_account()
    view = ready_draft(account)
    publish(account, view)
    assert refused(account, view, services.StaleRevision, key="key-2", base=view.revision).code == (
        "stale_revision"
    )


def test_stale_revision_beats_the_account_state():
    account = make_account()
    view = ready_draft(account)
    set_account_state(account.id, "unverified")
    refused(account, view, services.StaleRevision, base=view.revision - 1)


def test_an_account_that_became_an_administrator_is_not_found():
    account = make_account()
    view = ready_draft(account)
    Account.objects.filter(id=account.id).update(account_type="admin")
    assert refused(account, view).code == "not_found"
    assert nothing_written() == (0, 0, 0, 0)
