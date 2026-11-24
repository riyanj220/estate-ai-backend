"""`listings.services.apply_updates` (Story 2.2): the one write path for Listing fields."""

import dataclasses
import logging
import threading
import uuid
from decimal import Decimal

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.catalog import registry
from apps.identity import services as identity_services
from apps.identity.models import Account
from apps.kernel import events
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.listings import lifecycle, selectors, services
from apps.listings.events import ListingModelInputsChanged
from apps.listings.models import ListingRevision, ListingTerms, PropertyListing
from tests.listings.support import HOUSE, SYSTEM, make_account, seed_cap, user

RENT = ("rent", "house", "house")
TITLE = "Family house near the park"
GOOD = {
    "title": TITLE,
    "description": "Three bedrooms.\n\nQuiet street.",
    "asking_price": "1500000",
}


@pytest.fixture(autouse=True)
def cap(db):
    seed_cap()


def draft(account=None, values=HOUSE):
    account = account or make_account()
    return account, services.create_draft(user(account), *values)


def update(account, view, updates, base=None, **kw):
    return services.apply_updates(
        user(account), view.id, view.revision if base is None else base, updates, **kw
    )


def refused(account, view, updates, base=None, exc=services.FieldsInvalid):
    with pytest.raises(exc) as caught:
        update(account, view, updates, base)
    return caught.value


def codes(account, view, updates):
    return refused(account, view, updates).fields


def counts():
    return (PropertyListing.objects.count(), ListingRevision.objects.count())


# --- saving -------------------------------------------------------------------------------------


def test_saving_title_description_and_price():
    account, view = draft()
    saved = update(account, view, GOOD)
    assert saved.revision == 2 and saved.registry_version == registry.registry_version()
    assert saved.fields["title"] == TITLE
    assert saved.fields["description"] == "Three bedrooms.\n\nQuiet street."
    assert saved.fields["asking_price"] == "1500000.00"
    assert saved.fields["price_basis"] is None and saved.fields["rental_period"] is None
    assert {k: dict(v) for k, v in saved.provenance.items()} == {
        name: {"origin": "manual", "confirmed": True} for name in GOOD
    }
    revision = ListingRevision.objects.get(revision=2)
    assert (revision.origin, revision.actor_account_id) == ("manual", account.id)
    assert revision.changes == {**GOOD, "asking_price": "1500000.00"} | {
        "description": GOOD["description"]
    }
    row = PropertyListing.objects.get()
    assert row.revision == 2 and row.title == TITLE
    terms = ListingTerms.objects.get(listing=row)
    assert terms.asking_price == Decimal("1500000.00")
    assert selectors.get_owned_listing(user(account), view.id) == saved


def test_money_is_numeric_in_the_database():
    account, view = draft()
    update(account, view, {"asking_price": "1234567.8"})
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT data_type, numeric_precision, numeric_scale FROM information_schema.columns "
            "WHERE table_name = 'listings_listingterms' AND column_name = 'asking_price'"
        )
        assert cursor.fetchone() == ("numeric", 14, 2)
    assert isinstance(ListingTerms.objects.get().asking_price, Decimal)


def test_partial_saves_never_need_complete_data():
    account, view = draft()
    first = update(account, view, {"title": TITLE})
    assert first.revision == 2 and first.fields["asking_price"] is None
    second = update(account, first, {"asking_price": "99"})
    assert second.revision == 3 and second.fields["title"] == TITLE
    assert set(second.provenance) == {"title", "asking_price"}
    assert [r.revision for r in ListingRevision.objects.order_by("revision")] == [1, 2, 3]
    assert ListingRevision.objects.get(revision=3).changes == {"asking_price": "99.00"}


def test_clearing_a_field_removes_its_provenance_and_value():
    account, view = draft()
    saved = update(account, view, GOOD)
    cleared = update(account, saved, {"description": None, "title": "   "})
    assert cleared.revision == 3
    assert cleared.fields["description"] is None and cleared.fields["title"] is None
    assert set(cleared.provenance) == {"asking_price"}
    assert ListingRevision.objects.get(revision=3).changes == {"description": None, "title": None}
    assert PropertyListing.objects.get().title is None  # empty is null, never ""
    again = update(account, cleared, {"asking_price": None})
    assert again.fields["asking_price"] is None and dict(again.provenance) == {}


def test_values_are_normalised_before_they_are_stored():
    account, view = draft()
    saved = update(
        account, view, {"title": "  Family   house\tnear park ", "description": "a\r\n\r\n\r\nb"}
    )
    assert saved.fields["title"] == "Family house near park"
    assert saved.fields["description"] == "a\n\nb"


def test_an_unchanged_update_writes_nothing_and_costs_no_revision():
    account, view = draft()
    saved = update(account, view, GOOD)
    before = (counts(), PropertyListing.objects.get().updated_at)
    same = update(account, saved, {"title": f"  {TITLE} ", "asking_price": "1500000.0"})
    assert same == saved and same.revision == 2
    assert (counts(), PropertyListing.objects.get().updated_at) == before
    noop_clear = update(account, saved, {"rental_period": None})
    assert noop_clear.revision == 2 and counts() == before[0]


def test_a_no_op_does_not_create_the_terms_row():
    account, view = draft()
    ListingTerms.objects.all().delete()
    same = update(account, view, {"title": None})
    assert same.revision == 1 and ListingTerms.objects.count() == 0


def test_terms_are_created_lazily_for_a_draft_that_has_none():
    account, view = draft()
    ListingTerms.objects.all().delete()  # a Draft from before this story
    assert view.fields["asking_price"] is None
    priced = update(account, view, {"asking_price": "5"})
    assert priced.fields["asking_price"] == "5.00" and ListingTerms.objects.count() == 1
    assert ListingTerms.objects.get().asking_price == Decimal("5.00")


# --- the revision check -------------------------------------------------------------------------


def test_a_stale_revision_is_a_conflict_carrying_the_current_listing():
    account, view = draft()
    saved = update(account, view, {"title": TITLE})
    stale = refused(account, view, {"title": "Another title entirely"}, exc=services.StaleRevision)
    assert stale.code == "stale_revision" and stale.current == saved
    assert PropertyListing.objects.get().title == TITLE and counts() == (1, 2)


def test_the_stale_check_comes_before_validation():
    account, view = draft()
    update(account, view, {"title": TITLE})
    with pytest.raises(services.StaleRevision):
        update(account, view, {"title": "x", "asking_price": "-1", "rental_period": "week"}, 1)
    assert counts() == (1, 2)


def test_a_future_revision_is_also_stale():
    account, view = draft()
    with pytest.raises(services.StaleRevision) as caught:
        update(account, view, {"title": TITLE}, base=99)
    assert caught.value.current.revision == 1


def test_replaying_the_same_request_is_a_conflict_not_a_double_write():
    account, view = draft()
    update(account, view, {"title": TITLE, "asking_price": "10"})
    with pytest.raises(services.StaleRevision):
        update(account, view, {"title": TITLE, "asking_price": "10"})
    assert counts() == (1, 2)


@pytest.mark.django_db(transaction=True)
def test_two_sessions_with_the_same_base_revision_make_one_success_and_one_conflict():
    account, view = draft()
    barrier, outcomes = threading.Barrier(2), []

    def work(title):
        try:
            barrier.wait()
            saved = services.apply_updates(user(account), view.id, 1, {"title": title})
            outcomes.append(("saved", saved.fields["title"]))
        except services.StaleRevision as exc:
            outcomes.append(("stale", exc.current.revision))
        except Exception as exc:  # noqa: BLE001
            outcomes.append((type(exc).__name__, None))
        finally:
            connection.close()

    threads = [
        threading.Thread(target=work, args=(t,))
        for t in ("First session title", "Second session title")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)
    kinds = sorted(kind for kind, _ in outcomes)
    assert kinds == ["saved", "stale"]
    winner = next(value for kind, value in outcomes if kind == "saved")
    assert next(value for kind, value in outcomes if kind == "stale") == 2
    row = PropertyListing.objects.get()
    assert row.revision == 2 and row.title == winner
    assert ListingRevision.objects.filter(revision=2).count() == 1


# --- who may edit -------------------------------------------------------------------------------


def test_visitor_is_unauthenticated():
    _, view = draft()
    with pytest.raises(DomainError) as caught:
        services.apply_updates(Actor.visitor("r"), view.id, 1, {"title": TITLE})
    assert caught.value.code == "unauthenticated"


def test_everyone_but_the_owner_gets_not_found():
    owner, view = draft()
    other = make_account("other@example.com")
    admin = identity_services.provision_admin(
        SYSTEM, "root.admin@example.com", "mango river 4291 x"
    )
    cases = [
        (user(other), view.id),
        (Actor.admin(admin.id, "r"), view.id),
        (Actor.user(admin.id, "r"), view.id),
        (SYSTEM, view.id),
        (user(owner), uuid.uuid4()),
        (user(owner), "not-a-uuid"),
        (user(owner), str(view.id).upper()),
        (user(owner), str(view.id).replace("-", "")),
        (user(owner), None),
        (user(owner), 5),
    ]
    for actor, lookup in cases:
        with pytest.raises(DomainError) as caught:
            services.apply_updates(actor, lookup, 1, {"title": TITLE})
        assert caught.value.code == "not_found", (actor.kind, lookup)
    assert PropertyListing.objects.get().title is None


def test_not_found_comes_before_the_capability_check_and_the_body_checks():
    owner, view = draft()
    suspended = make_account("sus@example.com", state="suspended")
    with pytest.raises(DomainError) as caught:
        services.apply_updates(user(suspended), view.id, "junk", {"nope": 1})
    assert caught.value.code == "not_found"
    Account.objects.filter(id=owner.id).update(suspended_at=timezone.now(), suspension_reason="r")
    with pytest.raises(DomainError) as caught:
        services.apply_updates(user(owner), view.id, "junk", {"nope": 1})
    assert caught.value.code == "forbidden"  # the capability comes before the body checks


@pytest.mark.parametrize("state", ["suspended", "deactivated"])
def test_suspended_and_deactivated_owners_are_forbidden(state):
    account, view = draft()
    Account.objects.filter(id=account.id).update(
        **{
            "suspended": {"suspended_at": timezone.now(), "suspension_reason": "r"},
            "deactivated": {"deactivated_at": timezone.now()},
        }[state]
    )
    with pytest.raises(DomainError) as caught:
        update(account, view, {"title": TITLE})
    assert caught.value.code == "forbidden"
    assert PropertyListing.objects.get().title is None and counts() == (1, 1)


def test_an_email_unverified_owner_may_edit():
    account, view = draft(make_account(state="unverified"))
    assert update(account, view, {"title": TITLE}).revision == 2


def test_an_agency_draft_is_edited_from_any_session_of_the_agency():
    account, view = draft(make_account(account_type="agency"))
    saved = services.apply_updates(Actor.user(account.id, "other-request"), view.id, 1, GOOD)
    assert saved.revision == 2


def test_only_editable_statuses_may_change(monkeypatch):
    assert lifecycle.EDITABLE_STATUSES == frozenset({"draft"})
    account, view = draft()
    PropertyListing.objects.update(registry_version=1)
    monkeypatch.setattr(lifecycle, "EDITABLE_STATUSES", frozenset())
    with pytest.raises(DomainError) as caught:
        update(account, view, {"title": TITLE})
    assert caught.value.code == "forbidden"
    row = PropertyListing.objects.get()
    assert (row.title, row.revision, row.registry_version) == (None, 1, 1)
    with pytest.raises(services.StaleRevision):  # the revision check still comes first
        update(account, view, {"title": TITLE}, base=7)


# --- the request shape --------------------------------------------------------------------------


@pytest.mark.parametrize("base", [0, -1, "1", 1.0, True, [1], {}, None])
def test_base_revision_must_be_a_positive_integer(base):
    account, view = draft()
    with pytest.raises(services.FieldsInvalid) as caught:
        services.apply_updates(user(account), view.id, base, {"title": TITLE})
    assert caught.value.fields == {"base_revision": "required" if base is None else "invalid"}
    assert counts() == (1, 1)


@pytest.mark.parametrize(
    ("updates", "code"),
    [({}, "empty"), ([("title", "x")], "not_a_dict"), ("title", "not_a_dict"), (None, "required")],
)
def test_updates_must_be_a_non_empty_mapping(updates, code):
    account, view = draft()
    assert refused(account, view, updates).fields == {"fields": code}


def test_unknown_and_non_editable_fields_are_refused_together_without_echo():
    account, view = draft()
    secret = "owner_account_id_canary"
    error = refused(
        account,
        view,
        {
            "purpose": "rent",
            "category": "plot",
            "subtype": "shop",
            secret: 1,
            "status": "published",
            "owner_account_id": str(account.id),
            "origin": "ai",
            "confirmed": True,
            "title": TITLE,
        },
    )
    assert error.fields == {
        "purpose": "not_editable",
        "category": "not_editable",
        "subtype": "not_editable",
        "non_field_errors": "unknown_field",
    }
    assert secret not in repr(error.fields) and str(account.id) not in repr(error.fields)
    assert PropertyListing.objects.get().purpose == "sale" and counts() == (1, 1)


def test_every_failing_field_is_reported_at_once():
    account, view = draft()
    assert codes(account, view, {"title": "short", "asking_price": "-3", "price_basis": "x"}) == {
        "title": "too_short",
        "asking_price": "invalid_price",
        "price_basis": "invalid_choice",
    }
    assert counts() == (1, 1)


# --- terms and the purpose rules ----------------------------------------------------------------


def test_rent_terms_are_accepted():
    account, view = draft(values=RENT)
    saved = update(
        account,
        view,
        {"asking_price": "85000", "price_basis": "rental_period", "rental_period": "month"},
    )
    assert (saved.fields["price_basis"], saved.fields["rental_period"]) == (
        "rental_period",
        "month",
    )
    assert saved.fields["asking_price"] == "85000.00"


@pytest.mark.parametrize("basis", ["total", "per_unit"])
def test_sale_prices_use_total_or_per_unit(basis):
    account, view = draft()
    assert update(account, view, {"price_basis": basis}).fields["price_basis"] == basis


def test_a_rental_period_does_not_apply_to_a_sale():
    account, view = draft()
    assert codes(account, view, {"rental_period": "month"}) == {"rental_period": "not_applicable"}
    assert codes(account, view, {"rental_period": "month", "price_basis": "total"}) == {
        "rental_period": "not_applicable"
    }
    assert update(account, view, {"rental_period": None}).revision == 1  # clearing is harmless


def test_the_price_basis_must_suit_the_purpose():
    sale, sale_view = draft()
    assert codes(sale, sale_view, {"price_basis": "rental_period"}) == {
        "price_basis": "invalid_choice"
    }
    rent, rent_view = draft(make_account("r@example.com"), RENT)
    assert codes(rent, rent_view, {"price_basis": "total"}) == {"price_basis": "invalid_choice"}
    assert codes(rent, rent_view, {"price_basis": "per_unit"}) == {"price_basis": "invalid_choice"}
    assert codes(rent, rent_view, {"price_basis": 5}) == {"price_basis": "invalid"}


def test_saving_never_requires_a_complete_rental():
    account, view = draft(values=RENT)
    basis = update(account, view, {"price_basis": "rental_period"})  # completeness: Story 2.15
    assert basis.fields["price_basis"] == "rental_period" and basis.fields["rental_period"] is None
    both = update(account, basis, {"rental_period": "week"})
    cleared = update(account, both, {"rental_period": None})
    assert cleared.fields["rental_period"] is None
    assert codes(account, cleared, {"rental_period": "decade"}) == {
        "rental_period": "invalid_choice"
    }
    other, other_view = draft(make_account("p@example.com"), RENT)
    assert update(other, other_view, {"rental_period": "day"}).fields["rental_period"] == "day"


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("0", "invalid_price"),
        ("-1", "invalid_price"),
        ("1.234", "invalid_price"),
        (1500000.5, "invalid"),
        ("12abc", "invalid"),
        ("1000000000000", "invalid_price"),
    ],
)
def test_money_rules_through_the_service(value, code):
    account, view = draft()
    assert codes(account, view, {"asking_price": value}) == {"asking_price": code}
    assert ListingTerms.objects.get().asking_price is None


# --- text through the service -------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "Call 0300 1234567 now please",
        "Write to jane@example.com please",
        "House 12, Street 5, Gulberg",
    ],
)
def test_contact_details_are_refused_and_not_echoed(value, caplog):
    account, view = draft()
    with caplog.at_level(logging.DEBUG):
        for field in ("title", "description"):
            error = refused(account, view, {field: value})
            assert error.fields == {field: "contains_contact_details"}
            assert value not in repr(error.fields) and value not in str(error)
    assert value not in caplog.text and counts() == (1, 1)
    assert all(value not in repr(r.changes) for r in ListingRevision.objects.all())


def test_text_shape_codes():
    account, view = draft()
    assert codes(account, view, {"title": "short"}) == {"title": "too_short"}
    assert codes(account, view, {"title": "x" * 101}) == {"title": "too_long"}
    assert codes(account, view, {"title": "Nice\x00 family house"}) == {"title": "invalid"}
    assert codes(account, view, {"description": "x" * 20_001}) == {"description": "too_long"}
    assert codes(account, view, {"description": "x" * 5001}) == {"description": "too_long"}
    assert codes(account, view, {"title": 12345678901}) == {"title": "invalid"}


# --- provenance ---------------------------------------------------------------------------------


def test_the_service_decides_the_origin_and_confirmation():
    account, view = draft()
    by_ai = update(account, view, {"title": TITLE, "asking_price": "10"}, origin="ai")
    assert {k: dict(v) for k, v in by_ai.provenance.items()} == {
        "title": {"origin": "ai", "confirmed": False},
        "asking_price": {"origin": "ai", "confirmed": False},
    }
    assert ListingRevision.objects.get(revision=2).origin == "ai"
    by_owner = update(account, by_ai, {"title": "Corner house on the main road"})
    assert dict(by_owner.provenance["title"]) == {"origin": "manual", "confirmed": True}
    assert dict(by_owner.provenance["asking_price"]) == {"origin": "ai", "confirmed": False}
    assert ListingRevision.objects.get(revision=3).origin == "manual"


def test_the_origin_argument_is_validated_and_never_taken_from_the_payload():
    account, view = draft()
    for bad in ("robot", None, 5):
        with pytest.raises(DomainError) as caught:
            update(account, view, {"title": TITLE}, origin=bad)
        assert caught.value.code == "validation_error" and not isinstance(caught.value, ValueError)
    for payload in ({"origin": "ai"}, {"confirmed": True}, {"title": TITLE, "origin": "ai"}):
        assert codes(account, view, payload)["non_field_errors"] == "unknown_field"
    assert counts() == (1, 1)


# --- older registry versions --------------------------------------------------------------------


def test_a_draft_from_an_older_registry_is_revalidated_and_advanced():
    account, view = draft()
    PropertyListing.objects.update(registry_version=1)
    assert codes(account, view, {"title": "short"}) == {"title": "too_short"}
    assert PropertyListing.objects.get().registry_version == 1  # a failed edit changes nothing
    saved = update(account, view, {"title": TITLE})
    assert saved.registry_version == registry.registry_version() == 5
    assert PropertyListing.objects.get().registry_version == 5


def test_a_registry_change_does_not_alter_a_listing_nobody_edits():
    account, view = draft()
    PropertyListing.objects.update(registry_version=1)
    assert selectors.get_owned_listing(user(account), view.id).registry_version == 1


def test_validation_uses_the_current_registry(monkeypatch):
    account, view = draft()
    real = registry.field

    def tighter(field_id):
        found = real(field_id)
        if field_id == "title":
            return dataclasses.replace(found, max_length=12)
        return found

    monkeypatch.setattr(registry, "field", tighter)
    assert codes(account, view, {"title": TITLE}) == {"title": "too_long"}


# --- the model-inputs event ---------------------------------------------------------------------


@pytest.fixture
def bus(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    seen = []
    events.subscribe(ListingModelInputsChanged, seen.append)
    return seen


@pytest.fixture
def valuation_input(monkeypatch):
    real = registry.field

    def flagged(field_id):
        found = real(field_id)
        if field_id == "asking_price":
            return dataclasses.replace(found, valuation_input=True)
        return found

    monkeypatch.setattr(registry, "field", flagged)


def test_a_valuation_input_change_emits_after_commit(
    bus, valuation_input, django_capture_on_commit_callbacks
):
    account, view = draft()
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        update(account, view, {"asking_price": "100"})
        assert bus == []  # nothing before the commit
    assert len(callbacks) == 1
    assert bus == [ListingModelInputsChanged(listing_id=view.id)]


def test_other_fields_and_no_ops_emit_nothing(
    bus, valuation_input, django_capture_on_commit_callbacks
):
    account, view = draft()
    with django_capture_on_commit_callbacks(execute=True):
        saved = update(account, view, {"title": TITLE})
        update(account, saved, {"title": TITLE})
        priced = update(account, saved, {"asking_price": "100"})
        update(account, priced, {"asking_price": "100.00"})
    assert bus == [ListingModelInputsChanged(listing_id=view.id)]


def test_emitting_with_no_subscribers_is_a_no_op(
    monkeypatch, valuation_input, django_capture_on_commit_callbacks
):
    monkeypatch.setattr(events, "_registry", {})
    account, view = draft()
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        assert update(account, view, {"asking_price": "100"}).revision == 2
    assert callbacks == []


def test_only_the_area_residential_plot_and_commercial_inputs_are_valuation_inputs():
    flagged = {f.id for f in registry.fields() if f.valuation_input}
    assert flagged == {
        "area_value",
        "area_unit",
        "marla_basis",
        "bedrooms",
        "bathrooms",
        "construction_year",
        "condition",
        "covered_area_sqft",
        "plot_width",
        "plot_depth",
        "road_width_ft",
        "corner_plot",
        "development_status",
        "frontage_ft",
    }  # never the derived area_sqft


# --- rollback -----------------------------------------------------------------------------------


def test_a_failure_after_the_write_rolls_everything_back(
    bus, valuation_input, monkeypatch, django_capture_on_commit_callbacks
):
    account, view = draft()
    ListingTerms.objects.all().delete()  # the lazily created terms row must roll back too

    def boom(*args, **kwargs):
        raise RuntimeError("revision write failed")

    monkeypatch.setattr(ListingRevision.objects, "create", boom)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(RuntimeError):
            update(account, view, {"title": TITLE, "asking_price": "100"})
    row = PropertyListing.objects.get()
    assert (row.title, row.revision, row.provenance) == (None, 1, {})
    assert ListingTerms.objects.count() == 0 and ListingRevision.objects.count() == 1
    assert callbacks == [] and bus == []


def test_a_validation_failure_writes_nothing_not_even_terms():
    account, view = draft()
    ListingTerms.objects.all().delete()
    assert codes(account, view, {"asking_price": "100", "title": "short"}) == {"title": "too_short"}
    assert ListingTerms.objects.count() == 0 and counts() == (1, 1)


# --- privacy ------------------------------------------------------------------------------------


def test_views_never_carry_the_owner_and_are_immutable():
    account, view = draft()
    saved = update(account, view, GOOD)
    assert str(account.id) not in repr(saved)
    with pytest.raises(TypeError):
        saved.provenance["title"]["origin"] = "ai"
    with pytest.raises(TypeError):
        saved.provenance["extra"] = {}
    stale = refused(account, view, {"title": "Another title entirely"}, exc=services.StaleRevision)
    assert str(account.id) not in repr(stale.current)


# --- review additions --------------------------------------------------------------------------


def test_only_the_listing_row_is_locked():
    account, view = draft()
    with CaptureQueriesContext(connection) as queries:
        update(account, view, {"title": TITLE, "asking_price": "5"})
    locking = [q["sql"] for q in queries if "FOR UPDATE" in q["sql"]]
    assert len(locking) == 1
    assert 'FROM "listings_propertylisting"' in locking[0]
    assert "listings_listingterms" not in locking[0] and "JOIN" not in locking[0]


def test_saving_a_stored_ai_value_manually_confirms_it():
    account, view = draft()
    by_ai = update(account, view, {"title": TITLE}, origin="ai")
    assert dict(by_ai.provenance["title"]) == {"origin": "ai", "confirmed": False}
    confirmed = update(account, by_ai, {"title": TITLE})  # the same value
    assert confirmed.revision == 3
    assert dict(confirmed.provenance["title"]) == {"origin": "manual", "confirmed": True}
    row = ListingRevision.objects.get(revision=3)
    assert (row.origin, row.changes) == ("manual", {"title": TITLE})
    again = update(account, confirmed, {"title": TITLE})  # already manual and confirmed
    assert again.revision == 3 and counts()[1] == 3


def test_the_assistant_resaving_a_value_does_not_unconfirm_it_and_writes_nothing():
    account, view = draft()
    saved = update(account, view, {"title": TITLE})
    same = update(account, saved, {"title": TITLE}, origin="ai")
    assert same.revision == 2 and dict(same.provenance["title"])["confirmed"] is True
    changed = update(account, same, {"title": "Another family house here"}, origin="ai")
    assert dict(changed.provenance["title"]) == {"origin": "ai", "confirmed": False}


def test_confirming_a_valuation_input_without_a_value_change_emits_nothing(
    bus, valuation_input, django_capture_on_commit_callbacks
):
    account, view = draft()
    with django_capture_on_commit_callbacks(execute=True):
        priced = update(account, view, {"asking_price": "100"}, origin="ai")
    assert len(bus) == 1
    with django_capture_on_commit_callbacks(execute=True):
        update(account, priced, {"asking_price": "100"})  # confirms only
    assert len(bus) == 1 and ListingRevision.objects.count() == 3


def test_a_malformed_stored_provenance_never_breaks_a_read_or_an_edit():
    account, view = draft()
    saved = update(account, view, {"title": TITLE})
    for bad in (
        {"title": "manual", "nope": {"origin": "manual", "confirmed": True}},
        {"title": {"origin": "manual"}},
        {"title": {"origin": "robot", "confirmed": True}},
        {"title": {"origin": "manual", "confirmed": "yes"}},
        {"asking_price": {"origin": "manual", "confirmed": True}},  # an unset field
        [],
        "junk",
    ):
        PropertyListing.objects.update(provenance=bad)
        read = selectors.get_owned_listing(user(account), view.id)
        assert dict(read.provenance) == {} and read.fields["title"] == TITLE
    PropertyListing.objects.update(provenance="junk")
    healed = update(account, saved, {"title": TITLE})  # confirms: provenance is rewritten
    assert dict(healed.provenance["title"]) == {"origin": "manual", "confirmed": True}


def test_shape_errors_come_before_the_stale_check():
    account, view = draft()
    update(account, view, {"title": TITLE})  # the revision moves to 2
    with pytest.raises(services.FieldsInvalid) as caught:
        update(account, view, {"purpose": "rent", "nope": 1}, base=1)
    assert caught.value.fields == {"purpose": "not_editable", "non_field_errors": "unknown_field"}
    with pytest.raises(services.FieldsInvalid) as caught:
        services.apply_updates(user(account), view.id, "1", {"title": TITLE})
    assert caught.value.fields == {"base_revision": "invalid"}
    with pytest.raises(services.StaleRevision):  # a well-formed stale request is a conflict
        update(account, view, {"title": "x"}, base=1)


def test_an_outer_rollback_after_a_successful_edit_leaves_no_event(
    bus, valuation_input, django_capture_on_commit_callbacks
):
    account, view = draft()
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(RuntimeError), transaction.atomic():
            update(account, view, {"asking_price": "100", "title": TITLE})
            raise RuntimeError("later step failed")
    assert callbacks == [] and bus == []
    row = PropertyListing.objects.get()
    assert (row.title, row.revision) == (None, 1) and counts() == (1, 1)
    assert ListingTerms.objects.get().asking_price is None


@pytest.fixture
def published(monkeypatch):
    """A Listing row whose status is published (the lifecycle enum only has draft for now)."""
    import enum

    class Status(enum.StrEnum):
        DRAFT = "draft"
        PUBLISHED = "published"

    monkeypatch.setattr(selectors, "ListingStatus", Status)
    account, view = draft()
    with connection.cursor() as cursor:  # rolled back with the test's transaction
        cursor.execute("ALTER TABLE listings_propertylisting DROP CONSTRAINT listings_status_valid")
    PropertyListing.objects.update(status="published", registry_version=1)
    return account, view


def test_a_refused_edit_leaves_a_published_listing_unaltered(published):
    account, view = published
    with pytest.raises(DomainError) as caught:
        update(account, view, {"title": TITLE})
    assert caught.value.code == "forbidden"
    row = PropertyListing.objects.get()
    assert (row.status, row.registry_version, row.revision, row.title) == ("published", 1, 1, None)
    assert counts() == (1, 1)


def test_a_stale_base_on_a_published_listing_is_a_conflict_with_current(published):
    account, view = published
    with pytest.raises(services.StaleRevision) as caught:
        update(account, view, {"title": TITLE}, base=4)
    assert caught.value.current.status == "published" and caught.value.current.revision == 1
    assert PropertyListing.objects.get().registry_version == 1


def test_urdu_text_pasted_with_direction_marks_is_accepted():
    account, view = draft()
    pasted = (
        "\u200f\u06a9\u0631\u0627\u0686\u06cc \u06af\u06be\u0631 "
        "\u200e\u0628\u0631\u0627\u0626\u06d2 \u0641\u0631\u0648\u062e\u062a\u200f"
    )
    saved = update(account, view, {"title": pasted, "description": pasted})
    assert saved.fields["title"] == pasted
    for bad in ("\u202e", "\u202a", "\u2066", "\u2069", "\u200b"):
        assert codes(account, saved, {"title": f"Family{bad} house here"}) == {"title": "invalid"}
    assert codes(account, saved, {"title": "\u200f" * 9 + "ab"}) == {"title": "too_short"}
