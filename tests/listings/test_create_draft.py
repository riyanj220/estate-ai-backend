"""`listings.services.create_draft` and the owner read path (Story 2.1)."""

import base64
import json
import threading
import time
import uuid

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection

from apps.identity import services as identity_services
from apps.kernel import audit
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord, RateLimitPolicy
from apps.listings import selectors, services
from apps.listings.models import ListingRevision, ListingTerms, PropertyListing
from tests.listings.support import (
    HOUSE,
    PASSWORD,
    SYSTEM,
    make_account,
    random_email,
    seed_cap,
    user,
)


@pytest.fixture(autouse=True)
def cap(db):
    seed_cap()


def code(exc_info):
    return exc_info.value.code


# --- create: who may ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["individual", "agency"])
def test_active_owner_creates_a_draft(kind):
    account = make_account(account_type=kind)
    view = services.create_draft(user(account), *HOUSE)
    assert view.status == "draft" and view.visibility == "private"
    assert (view.revision, view.registry_version) == (1, 2)
    assert dict(view.fields) == {
        "purpose": "sale",
        "category": "house",
        "subtype": "house",
        "title": None,
        "description": None,
        "asking_price": None,
        "price_basis": None,
        "rental_period": None,
    }
    assert dict(view.provenance) == {}
    assert ListingTerms.objects.get(listing_id=view.id).asking_price is None
    assert view.media == () and view.amenities == ()
    row = PropertyListing.objects.get()
    assert row.id == view.id and row.owner_account_id == account.id
    revision = ListingRevision.objects.get()
    assert (revision.listing_id, revision.revision, revision.origin) == (row.id, 1, "manual")
    assert revision.actor_account_id == account.id
    assert revision.changes == {"purpose": "sale", "category": "house", "subtype": "house"}


def test_email_unverified_owner_may_create():
    account = make_account(state="unverified")
    assert services.create_draft(user(account), *HOUSE).revision == 1


@pytest.mark.parametrize("state", ["suspended", "deactivated"])
def test_suspended_and_deactivated_accounts_are_refused(state):
    account = make_account(state=state)
    with pytest.raises(DomainError) as exc:
        services.create_draft(user(account), *HOUSE)
    assert code(exc) == "forbidden"
    assert PropertyListing.objects.count() == ListingRevision.objects.count() == 0
    assert AuditRecord.objects.filter(action="listing.draft_created").count() == 0


def test_administrators_are_never_owners():
    admin = identity_services.provision_admin(
        SYSTEM, "root.admin@example.com", "mango river 4291 x"
    )
    for actor in (Actor.admin(admin.id, "r"), Actor.user(admin.id, "r")):
        with pytest.raises(DomainError) as exc:
            services.create_draft(actor, *HOUSE)
        assert code(exc) == "forbidden"
    assert PropertyListing.objects.count() == 0


def test_visitor_and_system_actors_cannot_create():
    with pytest.raises(DomainError) as exc:
        services.create_draft(Actor.visitor("r"), *HOUSE)
    assert code(exc) == "unauthenticated"
    with pytest.raises(DomainError) as exc:
        services.create_draft(SYSTEM, *HOUSE)
    assert code(exc) == "forbidden"


def test_a_missing_account_is_refused():
    with pytest.raises(DomainError) as exc:
        services.create_draft(Actor.user(uuid.uuid4(), "r"), *HOUSE)
    assert code(exc) == "forbidden"


# --- create: validation ------------------------------------------------------------------------


def invalid(account, *values):
    before = PropertyListing.objects.count()
    with pytest.raises(services.FieldsInvalid) as exc:
        services.create_draft(user(account), *values)
    assert exc.value.code == "validation_error"
    assert PropertyListing.objects.count() == before
    return exc.value.fields


def test_subtype_must_belong_to_the_category():
    assert invalid(make_account(), "sale", "apartment", "shop") == {
        "subtype": "subtype_not_in_category"
    }


def test_plot_file_is_a_distinct_subtype_of_plot():
    account = make_account()
    first = services.create_draft(user(account), "rent", "plot", "plot_file")
    second = services.create_draft(user(account), "rent", "plot", "residential_plot")
    assert first.fields["subtype"] == "plot_file" and second.fields["subtype"] == "residential_plot"
    assert invalid(account, "rent", "house", "plot_file") == {"subtype": "subtype_not_in_category"}


def test_every_failing_field_is_reported_at_once_by_code_only():
    secret = "owner@example.com"
    assert invalid(make_account(), None, secret, 5) == {
        "purpose": "required",
        "category": "invalid_choice",
        "subtype": "invalid",
    }
    assert invalid(make_account("b@example.com"), "", "  ", None) == {
        "purpose": "required",
        "category": "required",
        "subtype": "required",
    }
    assert invalid(make_account("c@example.com"), "lease", "castle", "tent") == {
        "purpose": "invalid_choice",
        "category": "invalid_choice",
        "subtype": "invalid_choice",
    }
    # a valid Category with an unknown Subtype is an invalid choice, not a mismatch
    assert invalid(make_account("d@example.com"), "sale", "house", "tent") == {
        "subtype": "invalid_choice"
    }
    assert secret not in repr(services.FieldsInvalid({"a": "required"}).__dict__)


def test_wrong_types_are_invalid_not_a_crash():
    account = make_account()
    assert invalid(account, ["sale"], {"a": 1}, True) == {
        "purpose": "invalid",
        "category": "invalid",
        "subtype": "invalid",
    }


def test_capability_is_checked_before_validation():
    account = make_account(state="suspended")
    with pytest.raises(DomainError) as exc:
        services.create_draft(user(account), "nope", "nope", "nope")
    assert code(exc) == "forbidden"


# --- create: the open-Draft cap ----------------------------------------------------------------


def fill(account, n):
    for _ in range(n):
        services.create_draft(user(account), *HOUSE)


def test_the_twenty_first_draft_is_throttled_without_retry_after():
    account = make_account()
    fill(account, 20)
    with pytest.raises(DomainError) as exc:
        services.create_draft(user(account), *HOUSE)
    assert code(exc) == "throttled" and not hasattr(exc.value, "retry_after")
    assert PropertyListing.objects.count() == 20
    assert AuditRecord.objects.filter(action="listing.draft_created").count() == 20


def test_other_owners_are_unaffected_by_a_full_account():
    full, other = make_account(), make_account("other@example.com")
    fill(full, 20)
    assert services.create_draft(user(other), *HOUSE).revision == 1


def test_the_cap_counts_only_status_draft():
    account = make_account()
    fill(account, 20)
    # Status has one value now, so lift its CHECK for this test. Only safe because the `db`
    # fixture's transaction rolls the DDL back: never copy this into a transaction=True test.
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_constraint WHERE conname = 'listings_status_valid'")
        assert cursor.fetchone() is not None
        cursor.execute("ALTER TABLE listings_propertylisting DROP CONSTRAINT listings_status_valid")
    PropertyListing.objects.filter(owner_account_id=account.id).update(status="published")
    assert services.create_draft(user(account), *HOUSE).status == "draft"


def test_the_cap_follows_the_seeded_policy_not_a_constant():
    account = make_account()
    seed_cap(2)
    fill(account, 2)
    with pytest.raises(DomainError) as exc:
        services.create_draft(user(account), *HOUSE)
    assert code(exc) == "throttled"
    seed_cap(3)
    assert services.create_draft(user(account), *HOUSE).revision == 1
    assert RateLimitPolicy.objects.get(scope="open_drafts").limit == 3


def test_a_windowed_policy_is_a_configuration_error():
    RateLimitPolicy.objects.filter(scope="open_drafts").update(window_seconds=60)
    with pytest.raises(ImproperlyConfigured):
        services.create_draft(user(make_account()), *HOUSE)
    assert PropertyListing.objects.count() == 0


def test_a_missing_policy_is_a_configuration_error():
    RateLimitPolicy.objects.filter(scope="open_drafts").delete()
    with pytest.raises(ImproperlyConfigured):
        services.create_draft(user(make_account()), *HOUSE)


# --- audit and rollback ------------------------------------------------------------------------


def test_one_audit_record_with_enumerated_metadata_only():
    account = make_account()
    view = services.create_draft(user(account), "rent", "plot", "plot_file")
    record = AuditRecord.objects.get(action="listing.draft_created")
    assert (record.target_type, record.target_id) == ("listing", view.id)
    assert (record.actor_kind, record.actor_id) == ("user", account.id)
    assert record.metadata == {"purpose": "rent", "category": "plot", "subtype": "plot_file"}
    assert record.request_id == "req"


def test_the_audit_schema_is_enumerations_only():
    schema = audit.registered_actions()["listing.draft_created"]
    assert set(schema) == {"purpose", "category", "subtype"}
    assert all(isinstance(v, frozenset) for v in schema.values())
    assert "plot_file" in schema["subtype"]


def test_a_failure_after_the_insert_leaves_nothing(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(audit, "record", boom)
    with pytest.raises(RuntimeError):
        services.create_draft(user(make_account()), *HOUSE)
    monkeypatch.undo()
    assert PropertyListing.objects.count() == ListingRevision.objects.count() == 0
    assert AuditRecord.objects.filter(action="listing.draft_created").count() == 0


def test_a_failed_revision_insert_rolls_the_listing_back(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("revision down")

    monkeypatch.setattr(ListingRevision.objects, "create", boom)
    with pytest.raises(RuntimeError):
        services.create_draft(user(make_account()), *HOUSE)
    monkeypatch.undo()
    assert PropertyListing.objects.count() == 0


# --- the cap race ------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_parallel_creations_at_nineteen_drafts_make_one_success_and_one_429():
    seed_cap()
    account = make_account()
    fill(account, 19)
    barrier, outcomes = threading.Barrier(2), []

    def work():
        try:
            barrier.wait()
            services.create_draft(user(account), *HOUSE)
            outcomes.append("created")
        except DomainError as exc:
            outcomes.append(exc.code)
        except Exception as exc:  # noqa: BLE001
            outcomes.append(type(exc).__name__)
        finally:
            connection.close()

    threads = [threading.Thread(target=work) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["created", "throttled"]
    assert PropertyListing.objects.filter(owner_account_id=account.id).count() == 20
    assert ListingRevision.objects.count() == 20


# --- reads -------------------------------------------------------------------------------------


def test_owner_reads_their_listing_and_others_get_not_found():
    owner, other = make_account(), make_account("other@example.com")
    view = services.create_draft(user(owner), *HOUSE)
    assert selectors.get_owned_listing(user(owner), view.id) == view
    assert selectors.get_owned_listing(user(owner), str(view.id)) == view
    for actor, lookup in [
        (user(other), view.id),
        (user(owner), uuid.uuid4()),
        (user(owner), "not-a-uuid"),
        (user(owner), None),
        (user(owner), 12345),
        (Actor.visitor("r"), view.id),
        (SYSTEM, view.id),
    ]:
        with pytest.raises(DomainError) as exc:
            selectors.get_owned_listing(actor, lookup)
        assert code(exc) == "not_found"


def test_administrators_get_not_found():
    owner = make_account()
    view = services.create_draft(user(owner), *HOUSE)
    admin = identity_services.provision_admin(
        SYSTEM, "root.admin@example.com", "mango river 4291 x"
    )
    for actor in (Actor.admin(admin.id, "r"), Actor.user(admin.id, "r")):
        with pytest.raises(DomainError) as exc:
            selectors.get_owned_listing(actor, view.id)
        assert code(exc) == "not_found"


def test_a_suspended_owner_may_read_but_a_deactivated_one_may_not():
    owner = make_account()
    view = services.create_draft(user(owner), *HOUSE)
    from django.utils import timezone

    from apps.identity.models import Account

    Account.objects.filter(id=owner.id).update(suspended_at=timezone.now(), suspension_reason="r")
    assert selectors.get_owned_listing(user(owner), view.id).id == view.id
    Account.objects.filter(id=owner.id).update(deactivated_at=timezone.now())
    with pytest.raises(DomainError) as exc:
        selectors.get_owned_listing(user(owner), view.id)
    assert code(exc) == "not_found"


def test_agency_listing_is_shared_by_every_session_of_the_account():
    agency = make_account(account_type="agency")
    view = services.create_draft(user(agency), *HOUSE)
    second_session = Actor.user(agency.id, "another-request")
    assert selectors.get_owned_listing(second_session, view.id).id == view.id


def test_view_is_frozen_and_has_no_owner():
    view = services.create_draft(user(make_account()), *HOUSE)
    assert set(view.__dataclass_fields__) == {
        "id",
        "status",
        "visibility",
        "revision",
        "registry_version",
        "fields",
        "provenance",
        "media",
        "amenities",
    }
    with pytest.raises(Exception):  # noqa: B017 (FrozenInstanceError)
        view.status = "published"
    with pytest.raises(TypeError):
        view.fields["purpose"] = "rent"


# --- list --------------------------------------------------------------------------------------


def test_list_is_newest_first_and_owner_only():
    owner, other = make_account(), make_account("other@example.com")
    created = [services.create_draft(user(owner), *HOUSE) for _ in range(3)]
    services.create_draft(user(other), *HOUSE)
    page = selectors.list_owned(user(owner))
    assert [v.id for v in page.items] == [v.id for v in reversed(created)]
    assert page.next_cursor is None


def test_list_paginates_with_an_opaque_cursor():
    owner = make_account()
    created = [services.create_draft(user(owner), *HOUSE).id for _ in range(5)]
    seen, cursor, pages = [], None, 0
    while True:
        page = selectors.list_owned(user(owner), cursor=cursor, limit=2)
        seen += [v.id for v in page.items]
        pages += 1
        cursor = page.next_cursor
        if cursor is None:
            break
    assert pages == 3 and seen == list(reversed(created))


def test_list_status_filter_and_limits():
    owner = make_account()
    fill(owner, 3)
    assert len(selectors.list_owned(user(owner), status="draft").items) == 3
    with pytest.raises(ValueError):
        selectors.list_owned(user(owner), status="nonsense")
    assert len(selectors.list_owned(user(owner), limit=0).items) == 1
    assert len(selectors.list_owned(user(owner), limit=10_000).items) == 3


@pytest.mark.parametrize(
    "cursor",
    ["", "garbage", "!!!!", "e30", "eyJjIjoiMSIsImkiOiIyIn0", "a" * 500, "bnVsbA", "W10"],
)
def test_bad_cursors_are_refused(cursor):
    owner = make_account()
    fill(owner, 1)
    with pytest.raises(selectors.InvalidCursor):
        selectors.list_owned(user(owner), cursor=cursor)


def enc(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def test_a_malformed_cursor_is_refused_never_a_crash():
    owner = make_account()
    fill(owner, 1)
    uid = str(uuid.uuid4())
    when = "2026-01-01T00:00:00+00:00"
    for obj in (
        {"c": "2026-01-01T00:00:00", "i": uid, "s": ""},
        {"c": when, "i": uid.upper(), "s": ""},
        {"c": when, "i": uid, "s": "", "x": 1},
        {"c": when, "i": uid},
        {"c": 5, "i": uid, "s": ""},
        {"c": when, "i": 5, "s": ""},
        {"c": when, "i": uid, "s": 5},
        {"c": ["a"], "i": [uid], "s": ""},
        {"c": "0001-01-01T00:00:00+14:00", "i": uid, "s": ""},
        {"c": "9999-12-31T23:59:59-14:00", "i": uid, "s": ""},
        {"c": "0001-01-01T00:00:00+00:00", "i": uid, "s": ""},
        {"c": "garbage", "i": uid, "s": ""},
        [1, 2],
        "text",
        None,
    ):
        with pytest.raises(selectors.InvalidCursor):
            selectors.list_owned(user(owner), cursor=enc(obj))
    good = enc({"c": "2999-01-01T00:00:00+00:00", "i": uid, "s": ""})
    assert len(selectors.list_owned(user(owner), cursor=good).items) == 1


def test_a_cursor_is_bound_to_its_status_filter():
    owner = make_account()
    fill(owner, 3)
    page = selectors.list_owned(user(owner), limit=1)
    with pytest.raises(selectors.InvalidCursor):
        selectors.list_owned(user(owner), status="draft", cursor=page.next_cursor)
    filtered = selectors.list_owned(user(owner), status="draft", limit=1)
    assert len(selectors.list_owned(user(owner), "draft", filtered.next_cursor, 1).items) == 1
    with pytest.raises(selectors.InvalidCursor):
        selectors.list_owned(user(owner), cursor=filtered.next_cursor)


def test_ids_must_be_canonical_lowercase_uuids():
    owner = make_account()
    view = services.create_draft(user(owner), *HOUSE)
    for variant in (str(view.id).upper(), view.id.hex, "{" + str(view.id) + "}"):
        with pytest.raises(DomainError) as exc:
            selectors.get_owned_listing(user(owner), variant)
        assert code(exc) == "not_found"


def test_a_state_change_between_the_check_and_the_lock_is_refused(monkeypatch):
    from django.utils import timezone

    from apps.identity.models import Account

    for change in (
        {"suspended_at": timezone.now(), "suspension_reason": "r"},
        {"deactivated_at": timezone.now()},
    ):
        account = make_account(random_email())
        real = services.lock_account

        def late_change(account_id, real=real, change=change, account=account):
            Account.objects.filter(id=account.id).update(**change)  # after the first check
            return real(account_id)

        monkeypatch.setattr(services, "lock_account", late_change)
        with pytest.raises(DomainError) as exc:
            services.create_draft(user(account), *HOUSE)
        assert code(exc) == "forbidden"
        assert not PropertyListing.objects.filter(owner_account_id=account.id).exists()
    monkeypatch.undo()


def test_an_account_that_became_an_administrator_is_refused_at_the_lock(monkeypatch):
    from apps.identity.models import Account

    account = make_account()
    real = services.lock_account

    def retyped(account_id):
        Account.objects.filter(id=account.id).update(account_type="admin")
        return real(account_id)

    monkeypatch.setattr(services, "lock_account", retyped)
    with pytest.raises(DomainError) as exc:
        services.create_draft(user(account), *HOUSE)
    assert code(exc) == "forbidden"
    assert PropertyListing.objects.count() == 0


def test_list_access_rules():
    with pytest.raises(DomainError) as exc:
        selectors.list_owned(Actor.visitor("r"))
    assert code(exc) == "unauthenticated"
    admin = identity_services.provision_admin(
        SYSTEM, "root.admin@example.com", "mango river 4291 x"
    )
    for actor in (Actor.admin(admin.id, "r"), SYSTEM):
        with pytest.raises(DomainError) as exc:
            selectors.list_owned(actor)
        assert code(exc) == "forbidden"
    suspended = make_account("s@example.com", state="suspended")
    assert selectors.list_owned(user(suspended)).items == ()
    gone = make_account("g@example.com", state="deactivated")
    with pytest.raises(DomainError) as exc:
        selectors.list_owned(user(gone))
    assert code(exc) == "forbidden"


def test_identity_helpers_unused_warning_guard():
    assert PASSWORD and random_email()


@pytest.mark.django_db(transaction=True)
def test_a_second_creation_waits_for_the_first_to_commit():
    """The 20th Draft is inserted only after the other creator counted: the lock orders them."""
    seed_cap()
    account = make_account()
    fill(account, 19)
    first_counted, second_started = threading.Event(), threading.Event()
    real_limit, outcomes = services._open_draft_limit, {}

    def slow_limit():  # called after the count and before the insert
        value = real_limit()
        if not first_counted.is_set():
            first_counted.set()
            second_started.wait(5)
            time.sleep(0.5)  # the second creator is now at (or blocked on) the account lock
        return value

    def first():
        try:
            services.create_draft(user(account), *HOUSE)
            outcomes["first"] = "created"
        except DomainError as exc:
            outcomes["first"] = exc.code
        finally:
            connection.close()

    def second():
        try:
            first_counted.wait(5)
            second_started.set()
            services.create_draft(user(account), *HOUSE)
            outcomes["second"] = "created"
        except DomainError as exc:
            outcomes["second"] = exc.code
        finally:
            connection.close()

    services._open_draft_limit = slow_limit
    try:
        threads = [threading.Thread(target=first), threading.Thread(target=second)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        services._open_draft_limit = real_limit
    assert outcomes == {"first": "created", "second": "throttled"}
    assert PropertyListing.objects.filter(owner_account_id=account.id).count() == 20


@pytest.mark.django_db(transaction=True)
def test_the_race_holds_over_repeated_rounds():
    seed_cap()
    for round_ in range(5):
        account = make_account(f"round{round_}@example.com")
        fill(account, 19)
        barrier, outcomes = threading.Barrier(3), []

        def work(account=account, barrier=barrier, outcomes=outcomes):
            try:
                barrier.wait()
                services.create_draft(user(account), *HOUSE)
                outcomes.append("created")
            except DomainError as exc:
                outcomes.append(exc.code)
            finally:
                connection.close()

        threads = [threading.Thread(target=work) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(outcomes) == ["created", "throttled", "throttled"]
