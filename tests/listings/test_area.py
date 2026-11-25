"""Area and measurement units (Story 2.3): conversions, validation and the edit path."""

import random
import threading
from decimal import ROUND_HALF_UP, Decimal

import pytest
from django.db import connection, transaction

from apps.catalog import registry
from apps.kernel import events
from apps.kernel.errors import DomainError
from apps.listings import area, selectors, services, validation
from apps.listings.events import ListingModelInputsChanged
from apps.listings.models import ListingArea, ListingRevision, PropertyListing
from tests.listings.support import HOUSE, make_account, seed_cap, user

D = Decimal


@pytest.fixture(autouse=True)
def cap(db):
    seed_cap()


def draft(account=None):
    account = account or make_account()
    return account, services.create_draft(user(account), *HOUSE)


def update(account, view, updates, base=None):
    return services.apply_updates(
        user(account), view.id, view.revision if base is None else base, updates
    )


def codes(account, view, updates):
    with pytest.raises(services.FieldsInvalid) as caught:
        update(account, view, updates)
    return caught.value.fields


def save(updates, account=None):
    account, view = draft(account)
    return account, update(account, view, updates)


# --- conversions (pure) -------------------------------------------------------------------------

CASES = [
    ("1200", "sqft", None, "1200.0000"),
    ("0.0001", "sqft", None, "0.0001"),
    ("1200.5", "sqft", None, "1200.5000"),
    ("10", "sqyd", None, "90.0000"),
    ("0.5", "sqyd", None, "4.5000"),
    ("2", "acre", None, "87120.0000"),
    ("0.25", "acre", None, "10890.0000"),
    ("0.0001", "acre", None, "4.3560"),
    ("5", "marla", "225", "1125.0000"),
    ("5", "marla", "250", "1250.0000"),
    ("5", "marla", "272.25", "1361.2500"),
    ("0.0001", "marla", "272.25", "0.0272"),  # 0.027225 rounds down
    ("0.0003", "marla", "272.25", "0.0817"),  # 0.081675 rounds up
    ("0.0002", "marla", "272.25", "0.0545"),  # 0.05445 is a tie: half up gives 0.0545, not 0.0544
    ("0.0002", "marla", "225", "0.0450"),
    ("1", "kanal", "225", "4500.0000"),
    ("1", "kanal", "250", "5000.0000"),
    ("1", "kanal", "272.25", "5445.0000"),
    ("2.5", "kanal", "272.25", "13612.5000"),
    ("0.0001", "kanal", "272.25", "0.5445"),
    ("9999999999.9999", "sqft", None, "9999999999.9999"),
    ("9999999999.9999", "acre", None, "435599999999995.6440"),
    ("9999999999.9999", "kanal", "272.25", "54449999999999.4555"),
    ("9999999999.9999", "marla", "272.25", "2722499999999.9728"),
    ("12345.6789", "sqyd", None, "111111.1101"),
]


@pytest.mark.parametrize(("value", "unit", "basis", "expected"), CASES)
def test_conversion_cases(value, unit, basis, expected):
    result = area.normalised_sqft(D(value), unit, D(basis) if basis else None)
    assert result == D(expected)
    assert result.as_tuple().exponent == -4  # always four decimals


def test_there_are_at_least_twenty_cases_covering_every_unit_and_basis():
    assert len(CASES) >= 20
    assert {c[1] for c in CASES} == {"sqft", "sqyd", "acre", "marla", "kanal"}
    assert {c[2] for c in CASES if c[2]} == {"225", "250", "272.25"}


def test_rounding_is_half_up_not_bankers():
    # Reachable from valid input: 0.0002 marla x 272.25 = 0.05445, a tie that banker's rounding
    # would send to 0.0544; 0.0006 marla x 272.25 = 0.16335 is a tie on an odd digit.
    assert area.normalised_sqft(D("0.0002"), "marla", D("272.25")) == D("0.0545")
    assert area.normalised_sqft(D("0.0006"), "marla", D("272.25")) == D("0.1634")
    assert area.normalised_sqft(D("0.0001"), "marla", D("250.00")) == D("0.0250")
    # The function itself rounds any tie half up (these inputs have five decimals, which the
    # validator refuses, so they only exercise the rounding rule).
    assert area.normalised_sqft(D("0.00005"), "sqft", None) == D("0.0001")
    assert area.normalised_sqft(D("0.00015"), "sqft", None) == D("0.0002")
    assert area.normalised_sqft(D("0.00025"), "sqft", None) == D("0.0003")


def test_every_registry_unit_has_a_conversion_and_no_unit_is_extra():
    units = set(registry.field("area_unit").allowed_values)
    assert units == set(area.SQUARE_FEET_PER) | set(area.MARLA_PER), (
        "a registry area_unit value needs a conversion in apps/listings/area.py"
    )
    assert set(area.MARLA_UNITS) <= units
    for unit in units:
        basis = D("225") if unit in area.MARLA_UNITS else None
        assert area.normalised_sqft(D("1"), unit, basis) is not None, unit


@pytest.mark.parametrize(
    ("value", "unit", "basis"),
    [
        (None, "sqft", None),
        (D("5"), None, None),
        (None, None, None),
        (D("5"), "marla", None),
        (D("5"), "kanal", None),
        (None, "marla", D("225")),
        (D("5"), "hectare", None),
    ],
)
def test_not_computable_is_none(value, unit, basis):
    assert area.normalised_sqft(value, unit, basis) is None


def test_a_basis_is_ignored_by_units_that_do_not_use_it():
    assert area.normalised_sqft(D("3"), "sqft", D("225")) == D("3.0000")


def test_the_result_always_equals_the_formula():
    """Property test over seeded random inputs, written independently of the implementation."""
    rng = random.Random(20261121)  # noqa: S311 (a reproducible test sequence)
    factors = {"sqft": D(1), "sqyd": D(9), "acre": D(43560)}
    for _ in range(2000):
        value = D(rng.randint(1, 99_999_999_999_999)).scaleb(-4)  # 0.0001 .. 9999999999.9999
        unit = rng.choice(["sqft", "sqyd", "acre", "marla", "kanal"])
        basis = rng.choice([D("225"), D("250"), D("272.25"), None])
        got = area.normalised_sqft(value, unit, basis)
        if unit in factors:
            exact = value * factors[unit]
        elif basis is None:
            assert got is None
            continue
        else:
            exact = value * basis * (20 if unit == "kanal" else 1)
        assert got == exact.quantize(D("0.0001"), rounding=ROUND_HALF_UP)
        assert abs(got - exact) <= D("0.00005")


def test_the_basis_text_is_the_enum_string():
    for text in registry.field("marla_basis").allowed_values:
        assert area.basis_text(D(text).quantize(D("0.01"))) == text
    assert area.basis_text(None) is None


def test_no_float_in_the_area_module():
    source = open(area.__file__).read()
    assert "float(" not in source and "import math" not in source


# --- validation (pure) --------------------------------------------------------------------------


def judged(updates, **current):
    state = {
        "area_value": None,
        "area_unit": None,
        "marla_basis": None,
        "area_sqft": None,
        **current,
    }
    return validation.evaluate("sale", "house", "house", state, updates)


@pytest.mark.parametrize(
    "value", ["1", "0.0001", "10.5", "9999999999", "9999999999.9999", "0.5", "100", "7.0"]
)
def test_good_area_values(value):
    values, errors = judged({"area_value": value})
    assert errors == {} and values["area_value"] == D(value).quantize(D("0.0001"))


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("0", "invalid_area"),
        ("0.0000", "invalid_area"),
        ("-5", "invalid_area"),
        ("-0.5", "invalid_area"),
        ("0.00001", "invalid_area"),
        ("1.00001", "invalid_area"),
        ("10000000000", "invalid_area"),
        ("9999999999.99999", "invalid_area"),
        ("0010", "invalid"),  # zero-padded
        ("00.5", "invalid"),
        (" 12", "invalid"),
        ("12 ", "invalid"),
        ("12\n", "invalid"),
        ("+5", "invalid"),
        ("99999999999.5", "invalid_area"),  # eleven whole digits
        ("12,5", "invalid"),
        ("1,200", "invalid"),
        ("1e3", "invalid"),
        ("abc", "invalid"),
        ("", "invalid"),
        (".5", "invalid"),
        ("5.", "invalid"),
        ("١٢", "invalid"),  # non-ASCII digits
        ("1" * 41, "invalid"),
        (5, "invalid"),
        (5.5, "invalid"),
        (True, "invalid"),
        (D("5"), "invalid"),
        (["5"], "invalid"),
    ],
)
def test_bad_area_values(value, code):
    values, errors = judged({"area_value": value})
    assert errors == {"area_value": code} and "area_value" not in values


@pytest.mark.parametrize("unit", ["hectare", "SQFT", "", "sq ft", "acres"])
def test_bad_units_are_invalid_choice(unit):
    assert judged({"area_unit": unit})[1] == {"area_unit": "invalid_choice"}


@pytest.mark.parametrize("basis", ["300", "225.0", "272.250", " 225", "", 225, 250.0, 272.25, True])
def test_bad_basis_is_invalid_choice(basis):
    assert judged({"marla_basis": basis}, area_unit="marla")[1] == {"marla_basis": "invalid_choice"}


@pytest.mark.parametrize("basis", ["225", "250", "272.25"])
def test_good_basis_with_marla_or_kanal(basis):
    for unit in ("marla", "kanal"):
        values, errors = judged({"area_unit": unit, "marla_basis": basis})
        assert errors == {} and values["marla_basis"] == basis


def test_a_basis_with_another_unit_is_not_applicable_on_the_resulting_state():
    assert judged({"area_unit": "sqft", "marla_basis": "225"})[1] == {
        "marla_basis": "not_applicable"
    }
    assert judged({"marla_basis": "225"})[1] == {"marla_basis": "not_applicable"}  # no unit at all
    assert judged({"marla_basis": "225"}, area_unit="acre")[1] == {"marla_basis": "not_applicable"}
    assert judged({"area_unit": None, "marla_basis": "225"}, area_unit="marla")[1] == {
        "marla_basis": "not_applicable"
    }
    assert judged({"marla_basis": "225"}, area_unit="marla")[1] == {}  # resulting unit is marla


def test_changing_the_unit_away_clears_a_stored_basis():
    values, errors = judged({"area_unit": "sqft"}, area_unit="marla", marla_basis="225")
    assert errors == {} and values == {"area_unit": "sqft", "marla_basis": None}
    values, _ = judged({"area_unit": "kanal"}, area_unit="marla", marla_basis="225")
    assert values == {"area_unit": "kanal"}  # kanal keeps it
    values, _ = judged({"area_unit": None}, area_unit="marla", marla_basis="225")
    assert values == {"area_unit": None, "marla_basis": None}


def test_a_clear_is_always_allowed_and_derived_input_is_not_editable():
    values, errors = judged({"area_value": None, "area_unit": None, "marla_basis": None})
    assert errors == {} and values == {"area_value": None, "area_unit": None, "marla_basis": None}
    assert judged({"area_sqft": "100"})[1] == {"area_sqft": "not_editable"}
    assert validation.check_shape(1, {"area_sqft": "100"}) == {"area_sqft": "not_editable"}


def test_errors_never_echo_values():
    _, errors = judged({"area_value": "secretvalue", "area_unit": "secretunit"})
    assert "secret" not in repr(errors)


# --- the edit path ------------------------------------------------------------------------------


def revision_changes(number):
    return ListingRevision.objects.get(revision=number).changes


def test_save_sqft():
    account, saved = save({"area_value": "1200", "area_unit": "sqft"})
    assert saved.revision == 2
    assert {
        k: saved.fields[k] for k in ("area_value", "area_unit", "marla_basis", "area_sqft")
    } == {
        "area_value": "1200.0000",
        "area_unit": "sqft",
        "marla_basis": None,
        "area_sqft": "1200.0000",
    }
    assert {k: dict(v) for k, v in saved.provenance.items()} == {
        "area_value": {"origin": "manual", "confirmed": True},
        "area_unit": {"origin": "manual", "confirmed": True},
    }
    assert "area_sqft" not in saved.provenance
    assert revision_changes(2) == {
        "area_value": "1200.0000",
        "area_unit": "sqft",
        "area_sqft": "1200.0000",
    }
    assert selectors.get_owned_listing(user(account), saved.id) == saved


@pytest.mark.parametrize(
    ("updates", "sqft"),
    [
        ({"area_value": "10", "area_unit": "sqyd"}, "90.0000"),
        ({"area_value": "2", "area_unit": "acre"}, "87120.0000"),
        ({"area_value": "5", "area_unit": "marla", "marla_basis": "225"}, "1125.0000"),
        ({"area_value": "1", "area_unit": "kanal", "marla_basis": "272.25"}, "5445.0000"),
    ],
)
def test_conversions_through_the_service(updates, sqft):
    _, saved = save(updates)
    assert saved.fields["area_sqft"] == sqft
    assert ListingArea.objects.get().area_sqft == D(sqft)


def test_marla_with_an_unknown_basis_saves_with_a_null_sqft_and_a_revision():
    account, view = draft()
    saved = update(account, view, {"area_value": "5", "area_unit": "marla"})
    assert saved.revision == 2 and saved.fields["area_sqft"] is None
    assert saved.fields["marla_basis"] is None
    assert ListingArea.objects.get().area_sqft is None
    assert "area_sqft" not in revision_changes(2)  # it did not change: still unknown


def test_a_basis_added_later_recomputes_in_the_same_save():
    account, view = draft()
    first = update(account, view, {"area_value": "5", "area_unit": "marla"})
    second = update(account, first, {"marla_basis": "250"})
    assert second.revision == 3
    assert (second.fields["marla_basis"], second.fields["area_sqft"]) == ("250", "1250.0000")
    assert revision_changes(3) == {"marla_basis": "250", "area_sqft": "1250.0000"}
    assert ListingArea.objects.get().marla_basis == D("250.00")
    third = update(account, second, {"marla_basis": "272.25"})
    assert (third.fields["marla_basis"], third.fields["area_sqft"]) == ("272.25", "1361.2500")


def test_the_basis_changes_the_value_of_a_kanal():
    account, view = draft()
    first = update(account, view, {"area_value": "2", "area_unit": "kanal", "marla_basis": "225"})
    assert first.fields["area_sqft"] == "9000.0000"
    second = update(account, first, {"area_unit": "marla"})
    assert second.fields["area_sqft"] == "450.0000" and second.fields["marla_basis"] == "225"


@pytest.mark.parametrize("bad", ["0", "-5"])
def test_zero_and_negative_are_refused_and_nothing_is_written(bad):
    account, view = draft()
    assert codes(account, view, {"area_value": bad}) == {"area_value": "invalid_area"}
    assert ListingArea.objects.count() == 0
    assert PropertyListing.objects.get().revision == 1
    assert ListingRevision.objects.count() == 1


def test_all_failing_area_fields_are_reported_at_once_and_nothing_is_written():
    account, view = draft()
    assert codes(
        account,
        view,
        {"area_value": "12,5", "area_unit": "hectare", "marla_basis": "300", "title": "short"},
    ) == {
        "area_value": "invalid",
        "area_unit": "invalid_choice",
        "marla_basis": "invalid_choice",
        "title": "too_short",
    }
    assert ListingArea.objects.count() == 0 and PropertyListing.objects.get().title is None


def test_a_basis_on_a_non_marla_unit_is_not_applicable():
    account, view = draft()
    assert codes(account, view, {"area_value": "5", "area_unit": "sqft", "marla_basis": "225"}) == {
        "marla_basis": "not_applicable"
    }
    assert ListingArea.objects.count() == 0


def test_changing_the_unit_away_clears_the_basis_and_recomputes():
    account, view = draft()
    first = update(account, view, {"area_value": "5", "area_unit": "marla", "marla_basis": "225"})
    assert first.fields["area_sqft"] == "1125.0000" and "marla_basis" in first.provenance
    second = update(account, first, {"area_unit": "sqft"})
    assert second.fields["marla_basis"] is None and second.fields["area_sqft"] == "5.0000"
    assert "marla_basis" not in second.provenance
    assert ListingArea.objects.get().marla_basis is None
    assert revision_changes(3) == {"area_unit": "sqft", "marla_basis": None, "area_sqft": "5.0000"}


def test_a_value_without_a_unit_and_a_unit_without_a_value_are_saved():
    account, view = draft()
    first = update(account, view, {"area_value": "5"})
    assert first.fields["area_sqft"] is None and first.fields["area_unit"] is None
    other_account, other = draft(make_account("john.roe@example.com"))
    second = update(other_account, other, {"area_unit": "acre"})
    assert second.fields["area_sqft"] is None and second.fields["area_value"] is None
    third = update(account, first, {"area_unit": "acre"})
    assert third.fields["area_sqft"] == "217800.0000"


def test_clearing_a_field_clears_the_provenance_and_the_derived_area():
    account, view = draft()
    saved = update(account, view, {"area_value": "5", "area_unit": "sqyd"})
    cleared = update(account, saved, {"area_value": None})
    assert cleared.revision == 3 and cleared.fields["area_value"] is None
    assert cleared.fields["area_sqft"] is None and "area_value" not in cleared.provenance
    assert revision_changes(3) == {"area_value": None, "area_sqft": None}
    assert ListingArea.objects.get().area_sqft is None


def test_the_derived_field_is_never_accepted():
    account, view = draft()
    assert codes(account, view, {"area_sqft": "100"}) == {"area_sqft": "not_editable"}
    assert codes(account, view, {"area_sqft": None}) == {"area_sqft": "not_editable"}
    assert codes(account, view, {"area_value": "5", "area_sqft": "1"}) == {
        "area_sqft": "not_editable"
    }
    assert ListingArea.objects.count() == 0


def test_no_change_keeps_the_revision_and_writes_nothing():
    account, view = draft()
    saved = update(account, view, {"area_value": "10", "area_unit": "sqyd"})
    before = (ListingRevision.objects.count(), ListingArea.objects.get().updated_at)
    same = update(account, saved, {"area_value": "10.0000", "area_unit": "sqyd"})
    assert same == saved and same.revision == 2
    assert (ListingRevision.objects.count(), ListingArea.objects.get().updated_at) == before


def test_a_no_op_never_creates_the_area_row():
    account, view = draft()
    saved = update(account, view, {"area_value": None})
    assert saved.revision == 1 and ListingArea.objects.count() == 0


def test_the_area_row_is_created_lazily_and_not_by_other_edits():
    account, view = draft()
    assert ListingArea.objects.count() == 0
    saved = update(account, view, {"title": "Family house near the park"})
    assert ListingArea.objects.count() == 0 and saved.fields["area_sqft"] is None
    update(account, saved, {"area_value": "3"})
    assert ListingArea.objects.count() == 1


def test_a_stale_revision_is_a_conflict_with_the_current_area():
    account, view = draft()
    winner = update(account, view, {"area_value": "7", "area_unit": "acre"})
    with pytest.raises(services.StaleRevision) as caught:
        update(account, view, {"area_value": "8"}, base=view.revision)
    assert caught.value.current == winner
    assert caught.value.current.fields["area_sqft"] == "304920.0000"
    assert ListingArea.objects.get().area_value == D("7")


@pytest.mark.django_db(transaction=True)
def test_concurrent_area_edits_have_exactly_one_winner():
    account, view = draft()
    outcomes = []
    barrier = threading.Barrier(2)

    def attempt(value):
        try:
            barrier.wait(timeout=10)
            services.apply_updates(
                user(account), view.id, 1, {"area_value": value, "area_unit": "sqft"}
            )
            outcomes.append(("ok", value))
        except services.StaleRevision:
            outcomes.append(("stale", value))
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt, args=(v,)) for v in ("11", "22")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(kind for kind, _ in outcomes) == ["ok", "stale"]
    winner = next(value for kind, value in outcomes if kind == "ok")
    stored = ListingArea.objects.get()
    assert stored.area_value == D(winner) and stored.area_sqft == D(winner)


def test_an_older_registry_draft_is_revalidated_for_area():
    account, view = draft()
    PropertyListing.objects.update(registry_version=2)
    assert codes(account, view, {"area_value": "0"}) == {"area_value": "invalid_area"}
    assert PropertyListing.objects.get().registry_version == 2
    saved = update(account, view, {"area_value": "4", "area_unit": "sqft"})
    assert saved.registry_version == registry.registry_version() == 9
    assert PropertyListing.objects.get().registry_version == 9


def test_ai_origin_area_is_unconfirmed_and_derived_stays_out_of_provenance():
    account, view = draft()
    saved = services.apply_updates(
        user(account), view.id, 1, {"area_value": "5", "area_unit": "marla"}, origin="ai"
    )
    assert {k: dict(v) for k, v in saved.provenance.items()} == {
        "area_value": {"origin": "ai", "confirmed": False},
        "area_unit": {"origin": "ai", "confirmed": False},
    }
    assert ListingRevision.objects.get(revision=2).origin == "ai"


def test_other_owners_and_visitors_get_not_found():
    account, view = draft()
    other = make_account("john.roe@example.com")
    with pytest.raises(DomainError) as caught:
        services.apply_updates(user(other), view.id, 1, {"area_value": "5"})
    assert caught.value.code == "not_found"
    assert ListingArea.objects.count() == 0


# --- the model-input event ----------------------------------------------------------------------


@pytest.fixture
def bus(monkeypatch):
    """The real event bus with one recording subscriber (delivery happens on commit)."""
    monkeypatch.setattr(events, "_registry", {})
    seen = []
    events.subscribe(ListingModelInputsChanged, seen.append)
    return seen


def delivered(bus, listing_id, count=1):
    assert len(bus) == count
    assert all(
        type(event) is ListingModelInputsChanged and event.listing_id == listing_id for event in bus
    )


def test_area_inputs_emit_model_inputs_changed_and_nothing_else_does(
    bus, django_capture_on_commit_callbacks
):
    account, view = draft()
    with django_capture_on_commit_callbacks(execute=True):
        saved = update(account, view, {"area_value": "5", "area_unit": "sqft"})
        assert bus == []  # nothing before the commit
    delivered(bus, view.id)
    bus.clear()
    with django_capture_on_commit_callbacks(execute=True):
        saved = update(account, saved, {"title": "Family house near the park"})
        update(account, saved, {"area_unit": "sqft"})  # no change
    assert bus == []


@pytest.mark.parametrize(
    "updates",
    [{"area_value": "6"}, {"area_unit": "acre"}, {"area_unit": "marla", "marla_basis": "225"}],
)
def test_each_area_input_change_emits_once(bus, django_capture_on_commit_callbacks, updates):
    account, view = draft()
    saved = update(account, view, {"area_value": "5", "area_unit": "sqft"})
    bus.clear()
    with django_capture_on_commit_callbacks(execute=True):
        update(account, saved, updates)
    delivered(bus, view.id)


def test_a_basis_cleared_by_a_unit_change_emits_once(bus, django_capture_on_commit_callbacks):
    account, view = draft()
    saved = update(account, view, {"area_value": "5", "area_unit": "marla", "marla_basis": "225"})
    bus.clear()
    with django_capture_on_commit_callbacks(execute=True):
        update(account, saved, {"area_unit": "sqft"})
    delivered(bus, view.id)


def test_clearing_the_basis_while_the_unit_is_marla(bus, django_capture_on_commit_callbacks):
    account, view = draft()
    saved = update(account, view, {"area_value": "5", "area_unit": "marla", "marla_basis": "225"})
    assert saved.fields["area_sqft"] == "1125.0000"
    bus.clear()
    with django_capture_on_commit_callbacks(execute=True):
        cleared = update(account, saved, {"marla_basis": None})
    assert cleared.revision == 3 and cleared.fields["marla_basis"] is None
    assert cleared.fields["area_sqft"] is None and cleared.fields["area_unit"] == "marla"
    assert "marla_basis" not in cleared.provenance
    assert revision_changes(3) == {"marla_basis": None, "area_sqft": None}
    stored = ListingArea.objects.get()
    assert stored.marla_basis is None and stored.area_sqft is None
    delivered(bus, view.id)


def test_a_failure_after_the_write_rolls_the_area_back_and_emits_nothing(
    bus, monkeypatch, django_capture_on_commit_callbacks
):
    account, view = draft()

    def boom(*args, **kwargs):
        raise RuntimeError("after the write")

    monkeypatch.setattr(ListingRevision.objects, "create", boom)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(RuntimeError):
            update(account, view, {"area_value": "5", "area_unit": "sqft"})
    assert ListingArea.objects.count() == 0 and PropertyListing.objects.get().revision == 1
    assert callbacks == [] and bus == []


def test_an_outer_rollback_after_a_good_save_delivers_nothing(
    bus, django_capture_on_commit_callbacks
):
    account, view = draft()
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(RuntimeError), transaction.atomic():
            update(account, view, {"area_value": "5", "area_unit": "sqft"})
            raise RuntimeError("the caller's transaction fails")
    assert callbacks == [] and bus == []
    assert ListingArea.objects.count() == 0


# --- stale derived values are repaired ----------------------------------------------------------


@pytest.mark.parametrize("stale", [None, "1.0000"])
def test_a_save_repairs_a_stale_stored_square_feet_figure(stale):
    account, view = draft()
    saved = update(account, view, {"area_value": "10", "area_unit": "sqyd"})
    ListingArea.objects.update(area_sqft=stale)  # stale or null although computable
    same = {"area_value": "10", "area_unit": "sqyd"}
    repaired = update(
        account, saved, {"area_value": "10", "area_unit": "sqyd"}
    )  # looks like a no-op
    assert repaired.revision == 3 and repaired.fields["area_sqft"] == "90.0000"
    assert revision_changes(3) == {"area_sqft": "90.0000"}
    assert ListingArea.objects.get().area_sqft == D("90")
    again = update(account, repaired, same)
    assert again.revision == 3  # now a true no-op


def test_an_unrelated_save_repairs_the_figure_too():
    account, view = draft()
    saved = update(account, view, {"area_value": "2", "area_unit": "acre"})
    ListingArea.objects.update(area_sqft=None)
    repaired = update(account, saved, {"title": "Family house near the park"})
    assert repaired.fields["area_sqft"] == "87120.0000"
    assert revision_changes(3) == {"title": "Family house near the park", "area_sqft": "87120.0000"}


# --- the wire shape of reads --------------------------------------------------------------------


def test_reads_and_lists_carry_the_area_in_the_same_shape():
    account, view = draft()
    saved = update(account, view, {"area_value": "2.5", "area_unit": "kanal", "marla_basis": "250"})
    assert saved.fields["area_value"] == "2.5000" and saved.fields["area_sqft"] == "12500.0000"
    page = selectors.list_owned(user(account))
    assert page.items == (saved,)
    assert selectors.get_owned_listing(user(account), saved.id).fields["marla_basis"] == "250"


def test_the_wire_never_uses_a_float_or_a_number_for_area():
    _, saved = save({"area_value": "5", "area_unit": "marla", "marla_basis": "225"})
    for name in ("area_value", "marla_basis", "area_sqft"):
        assert isinstance(saved.fields[name], str)
