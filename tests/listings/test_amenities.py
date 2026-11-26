"""Amenities and seller-declared nearby facilities (Story 2.8): the `amenities` registry type, the
`ListingAmenity` rows, replacement and clearing, unanswered versus none, the catalogue check,
projection, revisions, events, concurrency and drift. Codes are catalogue codes or low-entropy
fake words."""

import json
import logging
import threading

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext

from apps.catalog import registry
from apps.catalog.models import Amenity
from apps.kernel import events
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.listings import fields, selectors, services, validation
from apps.listings.events import ListingModelInputsChanged
from apps.listings.models import (
    ListingAmenity,
    ListingRevision,
    PropertyListing,
)
from tests.listings.support import (
    AMENITY_IDS,
    HOUSE,
    PLOT_FILE,
    RENT_HOUSE,
    SHOP,
    make_account,
    seed_cap,
    set_listing_visibility,
    user,
)

ELEVATOR, LOBBY, POOL = "elevator", "lobby", "swimming_pool"
SCHOOL, PARK, MOSQUE = "nearby_school", "nearby_park", "nearby_mosque"


@pytest.fixture(autouse=True)
def cap(db):
    seed_cap()


def draft(account=None, values=HOUSE):
    account = account or make_account()
    return account, services.create_draft(user(account), *values)


def update(account, view, updates, base=None):
    return services.apply_updates(
        user(account), view.id, view.revision if base is None else base, updates
    )


def refused(account, view, updates, base=None):
    with pytest.raises(services.FieldsInvalid) as caught:
        update(account, view, updates, base)
    return caught.value.fields


def stored(view_or_id):
    key = getattr(view_or_id, "id", view_or_id)
    return dict(ListingAmenity.objects.filter(listing_id=key).values_list("amenity_code", "state"))


def counts():
    return (
        PropertyListing.objects.count(),
        ListingAmenity.objects.count(),
        ListingRevision.objects.count(),
    )


# --- the registry and the storage map ----------------------------------------------------------


def test_the_registry_defines_the_amenities_field_last_with_the_right_shape():
    assert registry.registry_version() == 10
    assert tuple(f.id for f in registry.fields())[-1:] == AMENITY_IDS
    definition = registry.field("amenities")
    assert (definition.type, definition.requirement, definition.field_class) == (
        "amenities",
        "optional",
        "public",
    )
    assert definition.section == "amenities"
    assert definition.applicability == registry.Applicability()
    assert (definition.searchable, definition.sortable) == (True, False)
    assert definition.valuation_input and not definition.derived
    assert not definition.seller_declared  # the nearby kind carries that, not the field
    assert definition.allowed_values is None and definition.reference is None


def test_the_field_is_stored_as_rows_and_is_editable_and_on_the_wire_before_the_derived_area():
    assert fields.STORAGE["amenities"] == fields.AMENITIES
    assert "amenities" in fields.EDITABLE_FIELDS
    assert fields.WIRE_FIELDS[-2:] == ("amenities", "area_sqft")
    assert "amenities" not in {f.name for f in ListingAmenity._meta.get_fields()}  # no column


def test_the_loader_accepts_the_type_and_refuses_a_private_one():
    text = registry.FIELDS_PATH.read_text(encoding="utf-8")
    assert registry.parse(text).version == 10
    tail = "    class: public\n    searchable: true\n    valuation_input: true\n"
    assert text.endswith(tail)
    private = text.removesuffix(tail) + "    class: private\n"
    with pytest.raises(ImproperlyConfigured, match="must be public"):
        registry.parse(private)
    wrong = text.replace("    section: amenities\n", "    section: elsewhere\n")
    with pytest.raises(ImproperlyConfigured):
        registry.parse(wrong)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("  - id: amenities\n    type: amenities", "  - id: extras\n    type: amenities"),
        ("  - id: amenities\n    type: amenities", "  - id: amenities\n    type: text"),
        ("    searchable: true\n    valuation_input: true\n", "    sortable: true\n"),
        ("    searchable: true\n    valuation_input: true\n", "    derived: true\n"),
        (
            "    searchable: true\n    valuation_input: true\n",
            "    allowed_values: [present, none]\n",
        ),
    ],
)
def test_the_loader_refuses_a_misused_amenities_field(old, new):
    text = registry.FIELDS_PATH.read_text(encoding="utf-8")
    assert text.count(old) == 1 or old.startswith("    searchable")
    head, _, tail = text.rpartition(old)
    with pytest.raises(ImproperlyConfigured):
        registry.parse(head + new + tail)


# --- saving, replacing, clearing ---------------------------------------------------------------


def test_a_fresh_draft_has_no_amenities_unanswered_is_null_and_no_rows():
    account, view = draft()
    assert view.fields["amenities"] is None
    assert selectors.get_owned_listing(user(account), view.id).fields["amenities"] is None
    assert stored(view) == {}


def test_saving_a_map_stores_rows_bumps_the_revision_and_serves_the_map():
    account, view = draft()
    saved = update(account, view, {"amenities": {ELEVATOR: "present", LOBBY: "none"}})
    assert saved.revision == 2
    assert dict(saved.fields["amenities"]) == {ELEVATOR: "present", LOBBY: "none"}
    assert stored(view) == {ELEVATOR: "present", LOBBY: "none"}
    again = selectors.get_owned_listing(user(account), view.id)
    assert dict(again.fields["amenities"]) == {ELEVATOR: "present", LOBBY: "none"}
    assert dict(saved.provenance["amenities"]) == {"origin": "manual", "confirmed": True}


def test_a_new_map_replaces_the_whole_stored_map():
    account, view = draft()
    saved = update(account, view, {"amenities": {ELEVATOR: "present", LOBBY: "present"}})
    saved = update(account, saved, {"amenities": {POOL: "present", LOBBY: "none"}})
    assert saved.revision == 3
    assert dict(saved.fields["amenities"]) == {POOL: "present", LOBBY: "none"}
    assert stored(view) == {POOL: "present", LOBBY: "none"}  # the elevator is gone, not kept
    assert ListingAmenity.objects.count() == 2


@pytest.mark.parametrize("cleared", [None, {}])
def test_null_and_an_empty_object_clear_everything(cleared):
    account, view = draft()
    saved = update(account, view, {"amenities": {ELEVATOR: "present", SCHOOL: "none"}})
    saved = update(account, saved, {"amenities": cleared})
    assert saved.revision == 3 and saved.fields["amenities"] is None
    assert stored(view) == {} and "amenities" not in saved.provenance
    assert ListingRevision.objects.get(revision=3).changes == {"amenities": None}


def test_clearing_when_nothing_is_stored_is_a_no_op():
    account, view = draft()
    for cleared in (None, {}):
        saved = update(account, view, {"amenities": cleared})
        assert saved.revision == 1 and saved.fields["amenities"] is None
    assert counts() == (1, 0, 1)


def test_a_code_left_out_is_unanswered_never_none_and_never_a_row():
    account, view = draft()
    saved = update(account, view, {"amenities": {ELEVATOR: "present"}})
    shown = dict(saved.fields["amenities"])
    assert shown == {ELEVATOR: "present"}
    assert LOBBY not in shown and POOL not in shown and SCHOOL not in shown
    assert set(stored(view)) == {ELEVATOR}
    assert "none" not in set(stored(view).values())
    assert ListingAmenity.objects.filter(listing_id=view.id).count() == 1
    # leaving a code out of the next map removes the answer; it does not turn it into none
    saved = update(account, saved, {"amenities": {LOBBY: "present"}})
    assert dict(saved.fields["amenities"]) == {LOBBY: "present"}
    assert not ListingAmenity.objects.filter(amenity_code=ELEVATOR).exists()


def test_none_is_an_answer_and_is_kept_for_every_kind_including_nearby():
    account, view = draft()
    saved = update(account, view, {"amenities": {ELEVATOR: "none", POOL: "none", SCHOOL: "none"}})
    assert dict(saved.fields["amenities"]) == {ELEVATOR: "none", POOL: "none", SCHOOL: "none"}
    assert set(stored(view).values()) == {"none"}


def test_saving_the_same_map_again_changes_nothing_whatever_the_key_order():
    account, view = draft()
    saved = update(account, view, {"amenities": {ELEVATOR: "present", LOBBY: "none"}})
    same = update(account, saved, {"amenities": {LOBBY: "none", ELEVATOR: "present"}})
    assert same.revision == 2
    assert counts() == (1, 2, 2)


def test_every_purpose_and_subtype_accepts_amenities():
    for values in (HOUSE, RENT_HOUSE, SHOP, PLOT_FILE):
        account, view = draft(make_account(f"{values[2]}.{values[0]}@example.com"), values)
        saved = update(account, view, {"amenities": {ELEVATOR: "present"}})
        assert dict(saved.fields["amenities"]) == {ELEVATOR: "present"}


def test_amenities_save_together_with_other_fields_in_one_revision():
    account, view = draft()
    saved = update(
        account, view, {"title": "Family house near the park", "amenities": {POOL: "present"}}
    )
    assert saved.revision == 2 and saved.fields["title"] == "Family house near the park"
    assert dict(saved.fields["amenities"]) == {POOL: "present"}
    assert set(ListingRevision.objects.get(revision=2).changes) == {"title", "amenities"}


# --- nearby: code and state only ---------------------------------------------------------------


def test_a_nearby_entry_is_stored_and_shown_with_code_and_state_only():
    account, view = draft()
    saved = update(account, view, {"amenities": {SCHOOL: "present", PARK: "none"}})
    shown = saved.fields["amenities"]
    assert dict(shown) == {SCHOOL: "present", PARK: "none"}
    for state in shown.values():
        assert isinstance(state, str)  # a plain state, never an object with a distance
    columns = {f.name for f in ListingAmenity._meta.get_fields()}
    assert columns == {"id", "listing", "amenity_code", "state"}
    assert not any(
        word in name for name in columns for word in ("distance", "verif", "score", "metre")
    )


@pytest.mark.parametrize(
    "value",
    [
        {SCHOOL: {"state": "present", "distance_m": 300}},
        {SCHOOL: {"state": "present"}},
        {SCHOOL: ["present"]},
        {SCHOOL: 300},
        {SCHOOL: "present", "nearby_school_distance": "300"},
    ],
)
def test_a_distance_or_any_extra_shape_is_refused_nothing_written(value):
    account, view = draft()
    assert refused(account, view, {"amenities": value}) == {"amenities": "invalid"}
    assert counts() == (1, 0, 1)


# --- refusals -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        {"no_such_code": "present"},
        {"ELEVATOR": "present"},
        {" elevator": "present"},
        {"elevator ": "present"},
        {"": "present"},
        {"elevator": "present", "no_such_code": "none"},
        {"1": "present"},
    ],
)
def test_an_unknown_code_is_invalid_and_nothing_is_written(value):
    account, view = draft()
    assert refused(account, view, {"amenities": value}) == {"amenities": "invalid"}
    assert counts() == (1, 0, 1)
    assert PropertyListing.objects.get().revision == 1


def test_an_inactive_code_is_invalid():
    Amenity.objects.filter(code=ELEVATOR).update(is_active=False)
    account, view = draft()
    assert refused(account, view, {"amenities": {ELEVATOR: "present"}}) == {"amenities": "invalid"}
    assert refused(account, view, {"amenities": {ELEVATOR: "present", LOBBY: "present"}}) == {
        "amenities": "invalid"
    }
    assert update(account, view, {"amenities": {LOBBY: "present"}}).revision == 2


@pytest.mark.parametrize(
    "state",
    ["yes", "no", "PRESENT", "Present", " present", "present ", "", "absent", "unanswered",
     "unknown", None, True, False, 1, 0, 1.5, [], {}, ["present"]],
)  # fmt: skip
def test_a_state_other_than_the_two_exact_strings_is_invalid(state):
    account, view = draft()
    assert refused(account, view, {"amenities": {ELEVATOR: state}}) == {"amenities": "invalid"}
    assert counts() == (1, 0, 1)


@pytest.mark.parametrize(
    "value", ["present", "", 5, 1.5, True, False, ["elevator"], [[ELEVATOR, "present"]], [{}]]
)
def test_a_value_that_is_not_an_object_is_invalid(value):
    account, view = draft()
    assert refused(account, view, {"amenities": value}) == {"amenities": "invalid"}
    assert counts() == (1, 0, 1)


def test_a_non_string_key_cannot_reach_validation_as_a_catalogue_code():
    codes = frozenset({ELEVATOR})
    assert validation.check_amenities({1: "present"}, None, codes) == (None, "invalid")
    assert validation.check_amenities({None: "present"}, None, codes) == (None, "invalid")
    assert validation.check_amenities({("a",): "present"}, None, codes) == (None, "invalid")


def test_forty_entries_are_accepted_and_forty_one_are_refused():
    for i in range(20):  # the catalogue has 26 entries; add enough to reach 46 active codes
        Amenity.objects.create(
            code=f"extra_{i:02d}", label=f"Extra {i:02d}", kind="community", group="extras"
        )
    codes = sorted(selectors_codes())
    assert len(codes) == 46
    account, view = draft()
    assert refused(account, view, {"amenities": dict.fromkeys(codes[:41], "present")}) == {
        "amenities": "invalid"
    }
    assert counts() == (1, 0, 1)
    saved = update(account, view, {"amenities": dict.fromkeys(codes[:40], "present")})
    assert len(saved.fields["amenities"]) == 40
    assert ListingAmenity.objects.count() == 40


def selectors_codes():
    from apps.catalog import selectors as catalog

    return catalog.amenity_codes()


def test_without_the_catalogue_the_validator_fails_safe_accepting_nothing_new():
    assert validation.check_amenities({ELEVATOR: "present"}, None, None) == (None, "invalid")
    # an unchanged stored entry still passes, and a clear needs no catalogue
    stored_map = {ELEVATOR: "present"}
    assert validation.check_amenities({ELEVATOR: "present"}, stored_map, None) == (
        stored_map,
        None,
    )
    assert validation.check_amenities({}, stored_map, None) == (None, None)


def test_the_catalogue_is_only_read_when_the_request_touches_amenities(monkeypatch):
    from apps.catalog import selectors as catalog

    calls = []
    real = catalog.amenity_codes
    monkeypatch.setattr(catalog, "amenity_codes", lambda: calls.append(1) or real())
    account, view = draft()
    saved = update(account, view, {"title": "Family house near the park"})
    assert calls == []
    update(account, saved, {"amenities": {ELEVATOR: "present"}})
    assert calls == [1]


def test_the_validator_judges_pure_values_and_never_copies_a_submitted_value():
    codes = frozenset({ELEVATOR, LOBBY})
    assert validation.check_amenities({ELEVATOR: "present"}, None, codes) == (
        {ELEVATOR: "present"},
        None,
    )
    assert validation.check_amenities({}, {ELEVATOR: "present"}, codes) == (None, None)
    value, code = validation.check_amenities({"secret_word": "present"}, None, codes)
    assert (value, code) == (None, "invalid")


def test_the_refusal_names_only_the_field_and_never_echoes_what_was_sent():
    account, view = draft()
    with pytest.raises(services.FieldsInvalid) as caught:
        update(account, view, {"amenities": {"plum_secret_code": "present"}})
    blob = json.dumps(caught.value.fields) + str(caught.value) + repr(caught.value.args)
    assert "plum_secret_code" not in blob
    assert caught.value.fields == {"amenities": "invalid"}


def test_a_failing_amenities_value_does_not_write_the_other_fields_of_the_request():
    account, view = draft()
    assert refused(account, view, {"title": "Family house near the park", "amenities": [1]}) == {
        "amenities": "invalid"
    }
    assert PropertyListing.objects.get().title is None
    assert counts() == (1, 0, 1)


# --- a code the catalogue dropped later ---------------------------------------------------------


def test_a_stored_entry_whose_code_was_deactivated_can_be_resent_with_the_same_state():
    account, view = draft()
    saved = update(account, view, {"amenities": {ELEVATOR: "present", LOBBY: "none"}})
    Amenity.objects.filter(code=ELEVATOR).update(is_active=False)
    # the read still serves what is stored
    assert dict(selectors.get_owned_listing(user(account), view.id).fields["amenities"]) == {
        ELEVATOR: "present",
        LOBBY: "none",
    }
    same = update(account, saved, {"amenities": {ELEVATOR: "present", LOBBY: "none"}})
    assert same.revision == 2  # nothing changed
    changed = update(account, same, {"amenities": {ELEVATOR: "present", LOBBY: "present"}})
    assert dict(changed.fields["amenities"]) == {ELEVATOR: "present", LOBBY: "present"}
    # the old code can be re-sent only as stored: a different state is a new answer and needs an
    # active code
    assert refused(account, changed, {"amenities": {ELEVATOR: "none"}}) == {"amenities": "invalid"}
    dropped = update(account, changed, {"amenities": {LOBBY: "present"}})  # or simply dropped
    assert dict(dropped.fields["amenities"]) == {LOBBY: "present"}


# --- reads --------------------------------------------------------------------------------------


def test_the_public_projection_carries_the_map_and_nothing_else_per_entry():
    account, view = draft()
    update(account, view, {"amenities": {ELEVATOR: "present", SCHOOL: "none"}})
    set_listing_visibility(view.id, "published")
    public = selectors.public_listing(Actor.visitor("r"), view.id)
    shown = public.fields["amenities"]
    assert dict(shown) == {ELEVATOR: "present", SCHOOL: "none"}
    assert all(isinstance(code, str) and isinstance(state, str) for code, state in shown.items())
    assert "amenities" in dict(public.fields)
    assert not any(
        word in key for key in public.fields for word in ("distance", "verified", "verification")
    )


def test_a_public_listing_with_no_answers_has_null_never_an_empty_none_filled_map():
    account, view = draft()
    set_listing_visibility(view.id, "published")
    public = selectors.public_listing(Actor.visitor("r"), view.id)
    assert public.fields["amenities"] is None


def test_a_draft_is_not_public_and_a_stranger_cannot_read_or_write_the_map():
    account, view = draft()
    update(account, view, {"amenities": {ELEVATOR: "present"}})
    other = make_account("other@example.com")
    for actor in (user(other), Actor.visitor("r"), Actor.system("r")):
        with pytest.raises(DomainError) as caught:
            selectors.get_owned_listing(actor, view.id)
        assert caught.value.code in {"not_found", "unauthenticated"}
        with pytest.raises(DomainError):
            services.apply_updates(actor, view.id, 2, {"amenities": {LOBBY: "present"}})
    with pytest.raises(DomainError) as caught:
        selectors.public_listing(Actor.visitor("r"), view.id)
    assert caught.value.code == "not_found"
    assert stored(view) == {ELEVATOR: "present"}


def test_the_own_list_reads_every_map_in_one_query_not_one_per_listing():
    account = make_account()
    views = [draft(account)[1] for _ in range(3)]
    for view in views[1:]:  # the first stays unanswered
        update(account, view, {"amenities": {ELEVATOR: "present", POOL: "none"}})
    actor = user(account)
    with CaptureQueriesContext(connection) as queries:
        page = selectors.list_owned(actor)
    amenity_queries = [q for q in queries.captured_queries if "listings_listingamenity" in q["sql"]]
    assert len(amenity_queries) == 1
    by_id = {item.id: item.fields["amenities"] for item in page.items}
    assert by_id[views[0].id] is None
    assert (
        dict(by_id[views[1].id]) == dict(by_id[views[2].id]) == {ELEVATOR: "present", POOL: "none"}
    )


# --- revisions, provenance, events --------------------------------------------------------------


def test_the_revision_row_records_the_map_of_codes_and_states():
    account, view = draft()
    saved = update(account, view, {"amenities": {ELEVATOR: "present", SCHOOL: "none"}})
    row = ListingRevision.objects.get(revision=2)
    assert row.changes == {"amenities": {ELEVATOR: "present", SCHOOL: "none"}}
    assert (row.origin, row.actor_account_id) == ("manual", account.id)
    update(account, saved, {"amenities": {LOBBY: "present"}})
    assert ListingRevision.objects.get(revision=3).changes == {"amenities": {LOBBY: "present"}}


def test_an_ai_origin_write_is_stored_unconfirmed_like_any_field():
    account, view = draft()
    saved = services.apply_updates(
        user(account), view.id, 1, {"amenities": {ELEVATOR: "present"}}, origin="ai"
    )
    assert dict(saved.provenance["amenities"]) == {"origin": "ai", "confirmed": False}
    again = services.apply_updates(user(account), view.id, 2, {"amenities": {ELEVATOR: "present"}})
    assert dict(again.provenance["amenities"]) == {"origin": "manual", "confirmed": True}
    assert again.revision == 3  # confirming the stored answer is a change, like any field


@pytest.fixture
def bus(monkeypatch):
    monkeypatch.setattr(events, "_registry", {})
    seen = []
    events.subscribe(ListingModelInputsChanged, seen.append)
    return seen


def test_a_change_to_the_map_emits_model_inputs_changed_after_commit_and_a_no_op_does_not(
    bus, django_capture_on_commit_callbacks
):
    account, view = draft()
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        saved = update(account, view, {"amenities": {ELEVATOR: "present"}})
    assert len(callbacks) == 1 and [e.listing_id for e in bus] == [view.id]
    assert set(vars(bus[0])) == {"listing_id"}  # the event carries no values, no codes
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        saved = update(account, saved, {"amenities": {ELEVATOR: "present"}})  # unchanged
    assert callbacks == [] and len(bus) == 1
    with django_capture_on_commit_callbacks(execute=True):
        update(account, saved, {"amenities": None})  # a clear is a change
    assert len(bus) == 2


# --- concurrency and rollback -------------------------------------------------------------------


def test_a_stale_revision_is_a_conflict_with_the_current_map_and_writes_nothing():
    account, view = draft()
    update(account, view, {"amenities": {ELEVATOR: "present"}})
    with pytest.raises(services.StaleRevision) as caught:
        update(account, view, {"amenities": {LOBBY: "none"}}, base=1)
    assert caught.value.current.revision == 2
    assert dict(caught.value.current.fields["amenities"]) == {ELEVATOR: "present"}
    assert stored(view) == {ELEVATOR: "present"}
    assert counts() == (1, 1, 2)


@pytest.mark.django_db(transaction=True)
def test_two_sessions_writing_the_map_make_one_success_and_one_conflict():
    seed_cap()
    account, view = draft()
    barrier, outcomes = threading.Barrier(2), []

    def work(code):
        try:
            barrier.wait()
            saved = services.apply_updates(
                user(account), view.id, 1, {"amenities": {code: "present"}}
            )
            outcomes.append(("saved", next(iter(saved.fields["amenities"]))))
        except services.StaleRevision as exc:
            outcomes.append(("stale", exc.current.revision))
        except Exception as exc:  # noqa: BLE001
            outcomes.append((type(exc).__name__, None))
        finally:
            connection.close()

    threads = [threading.Thread(target=work, args=(code,)) for code in (ELEVATOR, LOBBY)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)
    assert sorted(kind for kind, _ in outcomes) == ["saved", "stale"]
    winner = next(value for kind, value in outcomes if kind == "saved")
    assert PropertyListing.objects.get().revision == 2
    assert stored(view) == {winner: "present"}  # exactly the winner's map, never a merge
    assert ListingRevision.objects.filter(revision=2).count() == 1


@pytest.mark.django_db(transaction=True)
def test_a_failure_while_writing_the_rows_rolls_everything_back_including_the_old_rows(
    monkeypatch,
):
    seed_cap()
    account, view = draft()
    saved = update(account, view, {"amenities": {ELEVATOR: "present", LOBBY: "none"}})
    before = counts()
    original = ListingAmenity.objects.bulk_create

    def boom(*args, **kwargs):
        raise RuntimeError

    monkeypatch.setattr(ListingAmenity.objects, "bulk_create", boom)
    with pytest.raises(RuntimeError):
        update(
            account, saved, {"title": "Family house near the park", "amenities": {POOL: "present"}}
        )
    monkeypatch.setattr(ListingAmenity.objects, "bulk_create", original)
    assert counts() == before
    assert stored(view) == {ELEVATOR: "present", LOBBY: "none"}  # the delete rolled back
    row = PropertyListing.objects.get()
    assert row.revision == 2 and row.title is None


@pytest.mark.django_db(transaction=True)
def test_a_failure_after_the_rows_emits_no_event_and_keeps_the_old_map(
    monkeypatch, bus, django_capture_on_commit_callbacks
):
    seed_cap()
    account, view = draft()
    saved = update(account, view, {"amenities": {ELEVATOR: "present"}})
    bus.clear()

    def boom(*args, **kwargs):
        raise RuntimeError

    monkeypatch.setattr(ListingRevision.objects, "create", boom)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(RuntimeError):
            update(account, saved, {"amenities": {LOBBY: "present"}})
    assert callbacks == [] and bus == []
    assert stored(view) == {ELEVATOR: "present"}


# --- the database -------------------------------------------------------------------------------


def test_the_database_refuses_a_bad_state_and_a_duplicate_code_and_cascades():
    account, view = draft()
    row = PropertyListing.objects.get()
    ListingAmenity.objects.create(listing=row, amenity_code=ELEVATOR, state="present")
    for kwargs in (
        {"amenity_code": ELEVATOR, "state": "none"},  # one entry per code
        {"amenity_code": LOBBY, "state": "maybe"},
        {"amenity_code": LOBBY, "state": ""},
        {"amenity_code": LOBBY, "state": "PRESENT"},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            ListingAmenity.objects.create(listing=row, **kwargs)
    row.delete()
    assert not ListingAmenity.objects.exists()


def test_the_search_index_is_on_code_and_state():
    indexes = {tuple(index.fields): index.name for index in ListingAmenity._meta.indexes}
    assert ("amenity_code", "state") in indexes
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(
            cursor, ListingAmenity._meta.db_table
        )
    assert any(
        c["index"] and c["columns"] == ["amenity_code", "state"] for c in constraints.values()
    )


# --- privacy and logs ---------------------------------------------------------------------------


def test_no_distance_verification_or_computed_value_appears_in_any_shape(caplog):
    caplog.set_level(logging.DEBUG)
    account, view = draft()
    saved = update(
        account, view, {"amenities": {SCHOOL: "present", MOSQUE: "none", ELEVATOR: "present"}}
    )
    set_listing_visibility(view.id, "published")
    public = selectors.public_listing(Actor.visitor("r"), view.id)
    owner = selectors.get_owned_listing(user(account), view.id)
    for shape in (saved, owner, public):
        blob = json.dumps(dict(shape.fields["amenities"]))
        for word in ("distance", "verified", "verification", "score", "metre", "km"):
            assert word not in blob
    for entry in ListingRevision.objects.all():
        assert not any(w in json.dumps(entry.changes) for w in ("distance", "verif"))
    assert not any("distance" in record.getMessage() for record in caplog.records)
