"""The Field registry loader, accessors and lock (Story 2.1)."""

import copy
import dataclasses
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from django.core.exceptions import ImproperlyConfigured

from apps.catalog import registry
from tests.listings.support import TAIL_IDS, TERMS_IDS

GOOD = yaml.safe_load(registry.FIELDS_PATH.read_text())


def text(mutate=None) -> str:
    data = copy.deepcopy(GOOD)
    if mutate:
        mutate(data)
    return yaml.safe_dump(data)


def refused(mutate, reason=None):
    with pytest.raises(ImproperlyConfigured) as caught:
        registry.parse(text(mutate))
    message = str(caught.value)
    assert message.startswith("Field registry: ") and "\n" not in message
    if reason:
        assert reason in message
    return message


def add_field(**overrides):
    base = {
        "id": "extra_field",
        "type": "text",
        "max_length": 50,
        "requirement": "optional",
        "applicability": {"purposes": [], "categories": [], "subtypes": []},
        "class": "public",
    }
    base.update(overrides)
    if base["type"] != "text":
        base.pop("max_length", None)  # only a text field has lengths
    return lambda data: data["fields"].append(base)


# --- the shipped file -----------------------------------------------------------------------


def test_the_shipped_registry_loads_with_the_identity_and_editing_fields():
    loaded = registry.load()
    assert loaded.version == 6
    assert [f.id for f in registry.fields()] == [
        "purpose",
        "category",
        "subtype",
        "title",
        "description",
        "asking_price",
        "price_basis",
        "rental_period",
        "area_value",
        "area_unit",
        "marla_basis",
        "area_sqft",
        *TAIL_IDS,
    ]
    assert registry.purposes() == ("sale", "rent")
    assert [c.id for c in registry.categories()] == ["house", "apartment", "plot", "commercial"]
    for definition in registry.fields()[:3]:
        assert definition.requirement == "required_for_publication"
        assert definition.field_class == "public" and definition.quota_identity
        assert not (definition.searchable or definition.sortable or definition.valuation_input)
    for definition in registry.fields():
        assert definition.field_class == "public"
    assert {f.id for f in registry.fields() if f.valuation_input} == {
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
    }


def test_the_editing_fields_are_defined_by_the_registry():
    title, description = registry.field("title"), registry.field("description")
    assert (title.type, title.min_length, title.max_length) == ("text", 10, 100)
    assert title.requirement == "required_for_publication" and title.ai_extractable
    assert (description.type, description.min_length, description.max_length) == ("text", 0, 5000)
    assert description.requirement == "recommended" and description.ai_extractable
    price = registry.field("asking_price")
    assert (price.type, price.currency) == ("money", "PKR")
    assert (price.minimum, price.maximum) == (Decimal("0.01"), Decimal("999999999999.99"))
    assert price.ai_extractable and not price.valuation_input
    assert price.requirement == "required_for_publication"
    assert registry.field("price_basis").requirement == "required_for_publication"
    assert registry.field("rental_period").requirement == "required_for_publication"
    basis = registry.field("price_basis")
    assert basis.allowed_values == ("total", "per_unit", "rental_period")
    assert basis.values_for("sale") == ("total", "per_unit")
    assert basis.values_for("rent") == ("rental_period",)
    assert basis.values_for("lease") == ()
    period = registry.field("rental_period")
    assert period.allowed_values == ("day", "week", "month", "year")
    assert period.applicability.purposes == ("rent",)
    assert period.applies_to("rent", "house", "house")
    assert not period.applies_to("sale", "house", "house")
    assert registry.field("purpose").values_for("sale") == ("sale", "rent")


def test_plot_file_is_a_distinct_subtype_of_plot():
    assert registry.subtypes_of("plot") == ("residential_plot", "commercial_plot", "plot_file")
    assert registry.subtypes_of("commercial") == ("shop", "office")
    assert registry.valid_subtype("plot", "plot_file")
    assert registry.valid_subtype("plot", "residential_plot")
    assert not registry.valid_subtype("apartment", "shop")
    assert not registry.valid_subtype("nope", "house")
    assert registry.subtypes_of("nope") == ()


def test_accessors_return_immutable_values():
    assert registry.registry_version() == 6
    assert registry.field("purpose").allowed_values == ("sale", "rent")
    assert registry.field("nope") is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        registry.field("purpose").id = "x"
    with pytest.raises(dataclasses.FrozenInstanceError):
        registry.load().version = 2
    assert isinstance(registry.fields(), tuple) and isinstance(registry.categories(), tuple)


def test_taxonomy_enums_match_the_taxonomy():
    assert set(registry.field("category").allowed_values) == {c.id for c in registry.categories()}
    assert set(registry.field("subtype").allowed_values) == set(registry.all_subtypes())


def test_load_is_cached():
    assert registry.load() is registry.load()


# --- one bad fixture per loader rule --------------------------------------------------------


def test_unknown_top_level_key():
    refused(lambda d: d.update(extra=1), "unknown key")


def test_unknown_field_key():
    refused(lambda d: d["fields"][0].update(colour="red"), "unknown key")


def test_unknown_taxonomy_key():
    refused(lambda d: d["taxonomy"].update(extra=[]), "unknown key")


@pytest.mark.parametrize("version", [None, "1", 1.5, True, 0, -2])
def test_missing_or_non_integer_version(version):
    refused(lambda d: d.update(version=version), "version")


def test_missing_version_key():
    refused(lambda d: d.pop("version"), "missing")


def test_duplicate_field_id():
    refused(lambda d: d["fields"].append(copy.deepcopy(d["fields"][0])), "duplicate field id")


def test_duplicate_category_id():
    refused(
        lambda d: d["taxonomy"]["categories"].append({"id": "house", "subtypes": ["villa"]}),
        "duplicate category",
    )


def test_subtype_listed_under_two_categories():
    refused(
        lambda d: d["taxonomy"]["categories"][1]["subtypes"].append("shop"),
        "two categories",
    )


def test_enum_values_that_do_not_match_the_taxonomy():
    refused(lambda d: d["fields"][2]["allowed_values"].remove("shop"), "do not match")
    refused(lambda d: d["fields"][1]["allowed_values"].append("villa"), "do not match")
    refused(lambda d: d["fields"][0]["allowed_values"].append("lease"), "do not match")


def test_enum_without_allowed_values():
    refused(add_field(id="mood", type="enum"), "allowed_values")


def test_allowed_values_on_a_non_enum():
    refused(add_field(allowed_values=["a"]), "only an enum")


@pytest.mark.parametrize("flag", ["searchable", "sortable", "valuation_input"])
def test_private_field_cannot_carry_the_public_flags(flag):
    refused(add_field(**{"class": "private", flag: True}), "private field")


@pytest.mark.parametrize("flag", ["ai_extractable", "quota_identity"])
def test_private_field_may_carry_the_other_flags(flag):
    parsed = registry.parse(text(add_field(**{"class": "private", flag: True})))
    assert parsed.fields[-1].field_class == "private"


def test_public_field_may_be_searchable():
    parsed = registry.parse(text(add_field(searchable=True, sortable=True)))
    assert parsed.fields[-1].searchable and parsed.fields[-1].sortable


@pytest.mark.parametrize(
    "mutate",
    [
        add_field(type="blob"),
        add_field(requirement="mandatory"),
        add_field(**{"class": "secret"}),
        add_field(id="Bad Id"),
        add_field(searchable="yes"),
        add_field(applicability={"purposes": ["lease"], "categories": [], "subtypes": []}),
        add_field(applicability={"purposes": [], "categories": ["castle"], "subtypes": []}),
        add_field(applicability={"purposes": []}),
    ],
)
def test_other_invalid_field_definitions(mutate):
    refused(mutate)


def test_malformed_yaml_and_wrong_shapes():
    for bad in ("a: [", "- just\n- a list\n", "", "version: 1\nversion: 2\n"):
        with pytest.raises(ImproperlyConfigured):
            registry.parse(bad)


def test_applicability_may_narrow_a_field():
    parsed = registry.parse(
        text(
            add_field(applicability={"purposes": ["rent"], "categories": ["plot"], "subtypes": []})
        )
    )
    assert parsed.fields[-1].applicability.categories == ("plot",)


def test_hash_ignores_layout_and_comments_but_not_content():
    base = registry.parse(text()).content_hash
    assert registry.parse("# a comment\n" + registry.FIELDS_PATH.read_text()).content_hash == base
    assert registry.parse(text(add_field())).content_hash != base


def test_startup_fails_on_an_invalid_file(monkeypatch, tmp_path):
    bad = tmp_path / "fields.yaml"
    bad.write_text(text(lambda d: d.pop("version")))
    monkeypatch.setattr(registry, "FIELDS_PATH", bad)
    registry.load.cache_clear()
    try:
        with pytest.raises(ImproperlyConfigured):
            registry.load()
    finally:
        monkeypatch.undo()
        registry.load.cache_clear()
    assert registry.load().version == 6


def test_catalog_ready_loads_the_registry(monkeypatch):
    from django.apps import apps

    calls = []
    monkeypatch.setattr(registry, "load", lambda: calls.append(1))
    apps.get_app_config("catalog").ready()
    assert calls == [1]


# --- the lock -------------------------------------------------------------------------------


def test_lock_matches_the_registry():
    version, digest = registry.read_lock(registry.LOCK_PATH.read_text())
    current = registry.load()
    assert (version, digest) == (current.version, current.content_hash), (
        "fields.yaml changed without the lock: bump `version` in fields.yaml, then run "
        "`uv run python scripts/registry_lock.py` and commit fields.lock."
    )


def test_lock_detects_a_change_without_a_bump():
    version, digest = registry.read_lock(registry.LOCK_PATH.read_text())
    changed = registry.parse(text(add_field()))
    assert changed.content_hash != digest and changed.version == version


def test_lock_round_trips_and_rejects_garbage():
    current = registry.load()
    assert registry.read_lock(registry.render_lock(current)) == (
        current.version,
        current.content_hash,
    )
    with pytest.raises(ImproperlyConfigured):
        registry.read_lock("nonsense: true")


def test_lock_script_refuses_a_change_without_a_bump(monkeypatch, tmp_path, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "registry_lock", Path(__file__).resolve().parents[2] / "scripts" / "registry_lock.py"
    )
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    fields = tmp_path / "fields.yaml"
    fields.write_text(text(add_field()))
    lock = tmp_path / "fields.lock"
    lock.write_text(registry.LOCK_PATH.read_text())
    monkeypatch.setattr(registry, "FIELDS_PATH", fields)
    monkeypatch.setattr(registry, "LOCK_PATH", lock)
    with pytest.raises(SystemExit, match="bump"):
        script.main()
    bumped = registry.registry_version() + 1
    fields.write_text(text(lambda d: (add_field()(d), d.update(version=bumped))))
    script.main()
    assert registry.read_lock(lock.read_text())[0] == bumped
    assert f"version {bumped}" in capsys.readouterr().out


def test_the_files_are_found_from_any_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    registry.load.cache_clear()
    try:
        assert registry.load().version == 6
        assert registry.read_lock(registry.LOCK_PATH.read_text())[0] == 6
    finally:
        registry.load.cache_clear()


def test_taxonomy_ids_must_fit_the_listing_columns():
    long = "a" * 33
    refused(lambda d: d["taxonomy"]["purposes"].append(long), "longer than 32")
    refused(lambda d: d["taxonomy"]["categories"].append({"id": long, "subtypes": ["x"]}), "longer")
    refused(lambda d: d["taxonomy"]["categories"][0]["subtypes"].append(long), "longer")
    ok = "a" * 32

    def grow(d):
        d["taxonomy"]["purposes"].append(ok)
        d["fields"][0]["allowed_values"].append(ok)
        basis = next(f for f in d["fields"] if f["id"] == "price_basis")
        basis["allowed_values_by_purpose"][ok] = ["total"]

    parsed = registry.parse(text(grow))
    assert ok in parsed.purposes


def test_merge_keys_and_non_string_keys_are_refused():
    for bad in (
        "version: 1\n<<: {a: 1}\n",
        "? [a, b]\n: 1\n",
        "1: 2\nversion: 1\n",
        "{a: 1, a: 2}",
        "version: &x 1\nbase: &m {a: 1}\nfields: {<<: *m}\n",
    ):
        with pytest.raises(ImproperlyConfigured):
            registry.parse(bad)


def test_contradictory_applicability_is_refused():
    refused(
        add_field(applicability={"purposes": [], "categories": ["house"], "subtypes": ["shop"]}),
        "outside its listed categories",
    )
    parsed = registry.parse(
        text(
            add_field(
                applicability={"purposes": [], "categories": ["commercial"], "subtypes": ["shop"]}
            )
        )
    )
    assert parsed.fields[-1].applicability.subtypes == ("shop",)


def test_enum_values_may_start_with_a_digit_but_stay_lowercase():
    parsed = registry.parse(text(add_field(id="rooms", type="enum", allowed_values=["1", "2_5"])))
    assert parsed.fields[-1].allowed_values == ("1", "2_5")
    refused(add_field(id="rooms", type="enum", allowed_values=["Big"]), "allowed_values")
    refused(add_field(id="rooms", type="enum", allowed_values=["a-b"]), "allowed_values")
    refused(lambda d: d["taxonomy"]["purposes"].append("1x"), "purposes")


def test_lock_script_refuses_a_lower_version(monkeypatch, tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "registry_lock", Path(__file__).resolve().parents[2] / "scripts" / "registry_lock.py"
    )
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    fields = tmp_path / "fields.yaml"
    fields.write_text(registry.FIELDS_PATH.read_text())
    lock = tmp_path / "fields.lock"
    higher = registry.registry_version() + 1
    lock.write_text(f"version: {higher}\nsha256: " + "0" * 64 + "\n")
    monkeypatch.setattr(registry, "FIELDS_PATH", fields)
    monkeypatch.setattr(registry, "LOCK_PATH", lock)
    with pytest.raises(SystemExit, match="lower"):
        script.main()


# --- the text, money and by-purpose rules (Story 2.2) ----------------------------------------


def money(**overrides):
    base = {"id": "fee", "type": "money", "min": "0.01", "max": "500.00", "currency": "PKR"}
    base.update(overrides)
    return add_field(**base)


def test_a_text_field_carries_lengths():
    parsed = registry.parse(text(add_field(min_length=3, max_length=9)))
    assert (parsed.fields[-1].min_length, parsed.fields[-1].max_length) == (3, 9)
    assert registry.parse(text(add_field())).fields[-1].min_length == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_length": None},
        {"max_length": 0},
        {"max_length": True},
        {"max_length": "5"},
        {"max_length": 20_001},
        {"min_length": -1},
        {"min_length": 60},
        {"min_length": 1.5},
    ],
)
def test_bad_text_lengths(overrides):
    def mutate(data):
        add_field(**overrides)(data)
        if overrides.get("max_length", 0) is None:
            data["fields"][-1].pop("max_length")

    refused(mutate)


def test_lengths_belong_to_text_only():
    refused(add_field(type="enum", allowed_values=["a"], min_length=2), "only a text field")

    def money_with_length(data):
        money()(data)
        data["fields"][-1]["max_length"] = 5

    refused(money_with_length, "only a text field")


def test_a_money_field_carries_bounds_and_currency():
    parsed = registry.parse(text(money()))
    fee = parsed.fields[-1]
    assert (fee.minimum, fee.maximum, fee.currency) == (Decimal("0.01"), Decimal("500.00"), "PKR")


@pytest.mark.parametrize(
    "overrides",
    [
        {"min": 1},
        {"min": 0.5},
        {"min": "-1"},
        {"min": "1.005"},
        {"min": "abc"},
        {"max": "1" * 13},
        {"max": "1000000000000.00"},
        {"min": "600"},
        {"currency": "USD"},
        {"currency": None},
    ],
)
def test_bad_money_definitions(overrides):
    refused(money(**overrides))


def test_money_needs_all_three_keys_and_only_money_has_them():
    def drop(key):
        def mutate(data):
            money()(data)
            data["fields"][-1].pop(key)

        return mutate

    for key in ("min", "max", "currency"):
        refused(drop(key), "needs min, max and currency")
    refused(add_field(min="1"), "only a money field")
    refused(add_field(type="enum", allowed_values=["a"], currency="PKR"), "only a money field")


def test_the_shipped_price_ceiling_fits_the_column():
    assert registry.field("asking_price").maximum == registry.MONEY_CEILING


def by_purpose(mapping, **overrides):
    return add_field(
        id="mode",
        type="enum",
        allowed_values=["a", "b", "c"],
        allowed_values_by_purpose=mapping,
        **overrides,
    )


def test_allowed_values_by_purpose_narrows_an_enum():
    parsed = registry.parse(text(by_purpose({"sale": ["a", "b"], "rent": ["c"]})))
    mode = parsed.fields[-1]
    assert mode.values_for("sale") == ("a", "b") and mode.values_for("rent") == ("c",)


@pytest.mark.parametrize(
    "mapping",
    [
        {"sale": ["a"]},  # a purpose is missing
        {"sale": ["a"], "rent": ["c"], "lease": ["a"]},  # not a purpose
        {"sale": [], "rent": ["c"]},  # empty
        {"sale": ["z"], "rent": ["c"]},  # outside allowed_values
        {"sale": ["a", "a"], "rent": ["c"]},  # duplicate
        {"sale": "a", "rent": ["c"]},  # not a list
        ["a"],
    ],
)
def test_bad_allowed_values_by_purpose(mapping):
    refused(by_purpose(mapping))


def test_allowed_values_by_purpose_is_for_enums_only():
    refused(add_field(allowed_values_by_purpose={"sale": ["a"], "rent": ["a"]}), "only an enum")


# --- decimal, derived and the version 3 area fields (Story 2.3) ---------------------------------


def decimal_field(**overrides):
    base = {"id": "depth", "type": "decimal", "min": "0.01", "max": "500.5", "scale": 2}
    base.update(overrides)
    return add_field(**base)


def test_the_area_fields_are_defined_by_the_registry():
    value, unit = registry.field("area_value"), registry.field("area_unit")
    basis, sqft = registry.field("marla_basis"), registry.field("area_sqft")
    assert (value.type, value.scale) == ("decimal", 4)
    assert (value.minimum, value.maximum) == (Decimal("0.0001"), Decimal("9999999999.9999"))
    assert value.requirement == "required_for_publication"
    assert value.searchable and value.sortable and value.ai_extractable and value.valuation_input
    assert unit.allowed_values == ("sqft", "sqyd", "marla", "kanal", "acre")
    assert unit.requirement == "required_for_publication"
    assert unit.searchable and unit.ai_extractable and unit.valuation_input
    assert basis.allowed_values == ("225", "250", "272.25") and basis.requirement == "optional"
    assert basis.description == (
        "Square feet in one marla. Applies only while the unit is marla or kanal."
    )
    assert basis.ai_extractable and basis.valuation_input and not basis.searchable
    assert (sqft.type, sqft.scale, sqft.derived) == ("decimal", 4, True)
    assert sqft.searchable and sqft.sortable
    assert not (sqft.valuation_input or sqft.ai_extractable)
    for definition in (value, unit, basis, sqft):
        assert definition.field_class == "public"
        assert definition.applicability == registry.Applicability()
        assert definition.applies_to("rent", "plot", "plot_file")
    assert not any(f.derived for f in registry.fields() if f.id != "area_sqft")


def test_a_decimal_field_carries_bounds_and_scale():
    parsed = registry.parse(text(decimal_field()))
    depth = parsed.fields[-1]
    assert (depth.minimum, depth.maximum, depth.scale) == (Decimal("0.01"), Decimal("500.5"), 2)
    assert depth.currency is None and not depth.derived


@pytest.mark.parametrize(
    "overrides",
    [
        {"min": 1},
        {"min": "-1"},
        {"min": "0.001"},  # more decimals than the scale
        {"max": "abc"},
        {"max": "1" * 17},
        {"min": "600"},
        {"scale": 0},
        {"scale": 9},
        {"scale": "2"},
        {"scale": True},
        {"currency": "PKR"},
    ],
)
def test_bad_decimal_definitions(overrides):
    refused(decimal_field(**overrides))


def test_a_decimal_needs_min_max_and_scale_and_only_a_decimal_has_scale():
    for key in ("min", "max", "scale"):

        def drop(data, key=key):
            decimal_field()(data)
            data["fields"][-1].pop(key)

        refused(drop, "needs min, max and scale")
    refused(add_field(scale=2), "only a decimal field has scale")
    refused(money(scale=2), "only a decimal field has scale")
    refused(add_field(type="enum", allowed_values=["a"], scale=2), "only a decimal field")


def test_an_enum_value_may_carry_a_decimal_part():
    parsed = registry.parse(text(add_field(type="enum", allowed_values=["225", "272.25"])))
    assert parsed.fields[-1].allowed_values == ("225", "272.25")
    for bad in ("272.", ".5", "1.23456", "a.b", "1.2.3", "2,5", "12345.5", "1.234"):
        refused(add_field(type="enum", allowed_values=[bad]))


def test_a_decimal_part_needs_an_all_numeric_enum_without_equal_values():
    refused(add_field(type="enum", allowed_values=["225", "big.5"]))
    refused(add_field(type="enum", allowed_values=["225", "abc", "1.5"]), "every value")
    refused(add_field(type="enum", allowed_values=["225", "225.00"]), "numerically equal")
    refused(add_field(type="enum", allowed_values=["1.5", "1.50"]), "numerically equal")
    ok = registry.parse(text(add_field(type="enum", allowed_values=["1", "1.5", "9999.99"])))
    assert ok.fields[-1].allowed_values == ("1", "1.5", "9999.99")
    plain = registry.parse(text(add_field(type="enum", allowed_values=["a", "2_5", "10"])))
    assert plain.fields[-1].allowed_values == ("a", "2_5", "10")


def test_decimal_bounds_are_served_without_an_exponent():
    from apps.presentation.api import registry as api

    tiny = registry.parse(text(decimal_field(min="0.0000001", max="0.5", scale=7)))
    found = tiny.fields[-1]
    assert (found.minimum, found.scale) == (Decimal("0.0000001"), 7)
    assert str(found.minimum) == "1E-7"  # why the serializer must not use str()
    assert format(found.minimum, "f") == "0.0000001"
    import unittest.mock as mock

    with mock.patch.object(registry, "fields", lambda: tiny.fields[-1:]):
        served = api._body()["fields"][0]
    assert served["min"] == "0.0000001" and served["max"] == "0.5"
    from apps.presentation.api.listings import _help

    assert "E-" not in _help(found) and "0.0000001" in _help(found)


def test_a_derived_field_is_optional_and_never_an_input():
    parsed = registry.parse(text(decimal_field(derived=True)))
    assert parsed.fields[-1].derived is True
    for overrides in (
        {"requirement": "required_for_publication"},
        {"requirement": "recommended"},
        {"ai_extractable": True},
        {"valuation_input": True},
        {"quota_identity": True},
        {"derived": "yes"},
        {"derived": 1},
    ):
        refused(decimal_field(**{"derived": True, **overrides}))


def test_a_description_is_short_text():
    parsed = registry.parse(text(add_field(description="Square feet in one marla.")))
    assert parsed.fields[-1].description == "Square feet in one marla."
    for bad in ("", "x" * 201, 5, ["a"]):
        refused(add_field(description=bad))
    assert registry.field("title").description is None


# --- integer, boolean and the version 4 residential fields (Story 2.4) --------------------------


def integer_field(**overrides):
    base = {"id": "rooms", "type": "integer", "min": 0, "max": 50}
    base.update(overrides)
    return add_field(**base)


def test_an_integer_field_carries_whole_number_bounds():
    parsed = registry.parse(text(integer_field()))
    rooms = parsed.fields[-1]
    assert (rooms.type, rooms.minimum, rooms.maximum) == ("integer", Decimal(0), Decimal(50))
    assert rooms.scale is None and rooms.currency is None
    assert registry.parse(text(integer_field(min=5, max=5))).fields[-1].minimum == Decimal(5)


@pytest.mark.parametrize(
    "overrides",
    [
        {"min": "0"},
        {"max": "50"},
        {"min": 0.5},
        {"max": 50.0},
        {"min": True},
        {"max": False},
        {"min": -1},
        {"min": 10, "max": 5},
        {"max": 10**10},
        {"currency": "PKR"},
        {"scale": 2},
        {"min": None},
    ],
)
def test_bad_integer_definitions(overrides):
    refused(integer_field(**overrides))


def test_an_integer_needs_min_and_max():
    for key in ("min", "max"):

        def drop(data, key=key):
            integer_field()(data)
            data["fields"][-1].pop(key)

        refused(drop, "needs min and max")


def test_a_boolean_field_takes_no_bounds_values_or_scale():
    parsed = registry.parse(text(add_field(id="has_lift", type="boolean")))
    assert parsed.fields[-1].type == "boolean" and parsed.fields[-1].minimum is None
    for extra in (
        {"min": 0},
        {"max": 1},
        {"allowed_values": ["a"]},
        {"scale": 1},
        {"currency": "PKR"},
    ):
        refused(add_field(id="has_lift", type="boolean", **extra))
    refused(add_field(type="text", min=1), "only a money field")


def test_the_residential_fields_are_defined_by_the_registry():
    assert registry.registry_version() == 6
    bedrooms = registry.field("bedrooms")
    assert bedrooms.requirement == "required_for_publication"
    assert bedrooms.searchable and bedrooms.valuation_input and bedrooms.ai_extractable
    assert registry.field("construction_year").requirement == "recommended"
    assert registry.field("floor_number").applies_to("sale", "apartment", "apartment")
    assert not registry.field("floor_number").applies_to("sale", "house", "house")
    assert not registry.field("bedrooms").applies_to("sale", "plot", "residential_plot")
    assert registry.field("bedrooms").applies_to("rent", "house", "house")
    assert registry.field("lift").type == "boolean"


# --- seller_declared, allowed_values_by_subtype and the version 5 catalogue (Story 2.5) -----------


def by_subtype(
    mapping, subtypes=("residential_plot", "plot_file"), applicability_categories=(), **overrides
):
    return add_field(
        id="mode",
        type="enum",
        applicability={
            "purposes": [],
            "categories": list(applicability_categories),
            "subtypes": list(subtypes),
        },
        allowed_values=["a", "b", "c"],
        allowed_values_by_subtype=mapping,
        **overrides,
    )


def test_allowed_values_by_subtype_narrows_an_enum_and_combines_with_purpose():
    parsed = registry.parse(text(by_subtype({"residential_plot": ["a", "b"], "plot_file": ["c"]})))
    mode = parsed.fields[-1]
    assert mode.allowed_values_by_subtype == (
        ("residential_plot", ("a", "b")),
        ("plot_file", ("c",)),
    )
    assert mode.values_for("sale", "residential_plot") == ("a", "b")
    assert mode.values_for("sale", "plot_file") == ("c",)
    assert mode.values_for("sale", "house") == ()  # a Subtype the field does not list
    assert mode.values_for("sale") == ()  # a Subtype map without a Subtype allows nothing
    both = registry.parse(
        text(
            by_subtype(
                {"residential_plot": ["a", "b"], "plot_file": ["b", "c"]},
                allowed_values_by_purpose={"sale": ["a", "b"], "rent": ["b", "c"]},
            )
        )
    ).fields[-1]
    assert both.values_for("sale", "plot_file") == ("b",)
    assert both.values_for("rent", "plot_file") == ("b", "c")
    assert both.values_for("rent", "residential_plot") == ("b",)
    assert both.values_for("rent", "house") == ()


def test_a_field_without_a_subtype_map_is_not_narrowed():
    assert registry.field("condition").allowed_values_by_subtype is None
    assert (
        registry.field("condition").values_for("sale", "shop")
        == registry.field("condition").allowed_values
    )


@pytest.mark.parametrize(
    ("mapping", "reason"),
    [
        ({"residential_plot": ["a", "b", "c"]}, "list every subtype"),  # a subtype is missing
        (
            {"residential_plot": ["a", "b"], "plot_file": ["c"], "shop": ["a"]},
            "list every subtype",  # not an applicable subtype
        ),
        (
            {"residential_plot": ["a", "b"], "plot_file": ["c"], "lease": ["a"]},
            "list every subtype",  # not a subtype
        ),
        ({"residential_plot": [], "plot_file": ["a", "b", "c"]}, "non-empty subset"),
        ({"residential_plot": ["z"], "plot_file": ["a", "b", "c"]}, "non-empty subset"),
        ({"residential_plot": ["a", "a"], "plot_file": ["b", "c"]}, "duplicate"),
        ({"residential_plot": "a", "plot_file": ["b", "c"]}, "list of snake_case ids"),
        (["a"], "list every subtype"),
        ({}, "list every subtype"),
        ({"residential_plot": ["a", "b"], "plot_file": ["b"]}, "no subtype offers"),  # c is dead
    ],
)
def test_bad_allowed_values_by_subtype(mapping, reason):
    message = refused(by_subtype(mapping), "allowed_values_by_subtype")
    assert reason in message


def test_a_subtype_subset_must_overlap_every_purpose_subset():
    refused(
        by_subtype(
            {"residential_plot": ["a", "b"], "plot_file": ["c"]},
            allowed_values_by_purpose={"sale": ["a", "b"], "rent": ["a", "b", "c"]},
        ),
        "shares no value",
    )
    ok = by_subtype(
        {"residential_plot": ["a", "b"], "plot_file": ["b", "c"]},
        allowed_values_by_purpose={"sale": ["a", "b"], "rent": ["b", "c"]},
    )
    assert registry.parse(text(ok)).fields[-1].values_for("rent", "residential_plot") == ("b",)


def test_with_categories_only_the_subtypes_of_those_categories_are_required():
    plot = {"residential_plot": ["a"], "commercial_plot": ["b"], "plot_file": ["c"]}
    mode = registry.parse(
        text(by_subtype(plot, subtypes=(), applicability_categories=["plot"]))
    ).fields[-1]
    assert mode.values_for("sale", "plot_file") == ("c",)
    refused(
        by_subtype({**plot, "shop": ["a"]}, subtypes=(), applicability_categories=["plot"]),
        "allowed_values_by_subtype",
    )
    refused(
        by_subtype(
            {"residential_plot": ["a", "b", "c"]}, subtypes=(), applicability_categories=["plot"]
        ),
        "list every subtype",
    )


def test_allowed_values_by_subtype_without_a_subtype_applicability_covers_every_subtype():
    mapping = {s: ["a", "b", "c"] for s in registry.all_subtypes()}
    assert registry.parse(text(by_subtype(mapping, subtypes=()))).fields[-1].values_for(
        "sale", "shop"
    ) == ("a", "b", "c")
    refused(by_subtype({"house": ["a", "b", "c"]}, subtypes=()), "list every subtype")


def test_allowed_values_by_subtype_is_for_enums_only():
    refused(add_field(allowed_values_by_subtype={"shop": ["a"]}), "only an enum")


def test_seller_declared_is_a_boolean_that_defaults_to_false():
    assert registry.parse(text(add_field())).fields[-1].seller_declared is False
    assert registry.parse(text(add_field(seller_declared=True))).fields[-1].seller_declared is True
    assert (
        registry.parse(text(add_field(seller_declared=False))).fields[-1].seller_declared is False
    )
    for bad in ("true", 1, 0, None, [True]):
        refused(add_field(seller_declared=bad), "seller_declared must be true or false")


def test_the_seller_declared_fields_are_exactly_the_legal_and_possession_claims():
    declared = {f.id for f in registry.fields() if f.seller_declared}
    assert declared == {
        "possession_status",
        "balloting_status",
        "development_status",
        "approval_authority",
        "plot_file_status",
        "possession_availability",  # Story 2.6: the seller's own claim about possession
    }


def test_the_plot_file_possession_vocabulary_lives_in_the_registry():
    possession = registry.field("possession_status")
    assert possession.allowed_values == ("possession_ready", "possession_pending", "file_only")
    assert dict(possession.allowed_values_by_subtype) == {
        "residential_plot": ("possession_ready", "possession_pending"),
        "commercial_plot": ("possession_ready", "possession_pending"),
        "plot_file": ("file_only",),
    }
    assert possession.values_for("sale", "plot_file") == ("file_only",)
    assert possession.values_for("rent", "commercial_plot") == (
        "possession_ready",
        "possession_pending",
    )


def test_version_5_widens_the_shared_fields_without_new_columns():
    expected = {
        "condition": ("house", "apartment", "shop", "office"),
        "furnishing": ("house", "apartment", "shop", "office"),
        "parking_spaces": ("house", "apartment", "shop", "office"),
        "occupancy_status": ("house", "apartment", "shop", "office"),
        "floor_number": ("apartment", "shop", "office"),
        "building_floors": ("apartment", "shop", "office"),
        "lift": ("apartment", "office"),
        "building_security": ("apartment", "shop", "office"),
        "building_lobby": ("apartment", "shop", "office"),
        "maintenance_charges": ("apartment", "shop", "office"),
    }
    for name, subtypes in expected.items():
        assert registry.field(name).applicability.subtypes == subtypes, name
    assert registry.field("backup_electricity").applicability.subtypes == ("apartment",)


def test_the_utility_plot_and_commercial_definitions():
    plots = ("residential_plot", "commercial_plot", "plot_file")
    for name, requirement in [
        ("electricity", "recommended"),
        ("water", "recommended"),
        ("gas", "optional"),
    ]:
        definition = registry.field(name)
        assert definition.type == "boolean" and definition.requirement == requirement
        assert definition.applicability.subtypes == (*plots, "shop", "office")
    for name in ("plot_width", "plot_depth"):
        definition = registry.field(name)
        assert (definition.type, definition.scale) == ("decimal", 2)
        assert (definition.minimum, definition.maximum) == (Decimal("0.01"), Decimal("99999.99"))
        assert (
            definition.requirement == "recommended" and definition.applicability.subtypes == plots
        )
    road = registry.field("road_width_ft")
    assert (road.minimum, road.maximum, road.scale) == (Decimal("1.00"), Decimal("999.99"), 2)
    frontage = registry.field("frontage_ft")
    assert (frontage.minimum, frontage.maximum) == (Decimal("0.01"), Decimal("9999.99"))
    assert frontage.applicability.subtypes == ("shop",) and frontage.requirement == "recommended"
    ceiling = registry.field("ceiling_height_ft")
    assert (ceiling.minimum, ceiling.maximum) == (Decimal("1.00"), Decimal("99.99"))
    power = registry.field("power_load_kw")
    assert (power.minimum, power.maximum) == (Decimal("0.01"), Decimal("99999.99"))
    rooms = registry.field("conference_rooms")
    assert (rooms.type, rooms.minimum, rooms.maximum) == ("integer", Decimal(0), Decimal(50))
    assert rooms.applicability.subtypes == ("office",)
    assert registry.field("business_centre_facilities").applicability.subtypes == ("office",)
    assert registry.field("plot_file_status").applicability.subtypes == ("plot_file",)
    assert registry.field("orientation").allowed_values == (
        "north",
        "south",
        "east",
        "west",
        "north_east",
        "north_west",
        "south_east",
        "south_west",
    )
    assert registry.field("approval_authority").allowed_values[-3:] == (
        "private_society",
        "none",
        "other",
    )
    for name in ("business_use", "road_access"):
        assert registry.field(name).requirement == "recommended"
        assert registry.field(name).applicability.subtypes == ("shop", "office")
    assert not registry.field("business_use").seller_declared


def test_unsupported_attributes_are_not_in_the_registry():
    for name in (
        "installment_available",
        "down_payment",
        "title_document_type",
        "mortgage_declaration",
        "approval_reference_number",
    ):
        assert registry.field(name) is None


def test_error_code_is_a_decimal_property_defaulting_to_invalid_measure():
    def decimal(**extra):
        return add_field(id="width", type="decimal", min="1", max="9", scale=2, **extra)

    assert registry.parse(text(decimal())).fields[-1].error_code == "invalid_measure"
    assert (
        registry.parse(text(decimal(error_code="invalid_area"))).fields[-1].error_code
        == "invalid_area"
    )
    for bad in ("invalid", "invalid_price", 5, None):
        refused(decimal(error_code=bad), "error_code")
    refused(add_field(error_code="invalid_area"), "only a decimal field has error_code")
    assert {f.id for f in registry.fields() if f.error_code == "invalid_area"} == {
        "area_value",
        "covered_area_sqft",
    }


# --- the date type and the version 6 terms (Story 2.6) -------------------------------------------


def date_field(**overrides):
    base = {"id": "move_in", "type": "date", "min": "2000-01-01", "max": "2100-12-31"}
    base.update(overrides)
    return add_field(**base)


def test_a_date_field_carries_calendar_date_bounds():
    import datetime

    parsed = registry.parse(text(date_field())).fields[-1]
    assert parsed.type == "date"
    assert (parsed.minimum, parsed.maximum) == (
        datetime.date(2000, 1, 1),
        datetime.date(2100, 12, 31),
    )
    assert type(parsed.minimum) is datetime.date  # a date, never a timestamp or a Decimal
    assert parsed.currency is None and parsed.scale is None and parsed.allowed_values is None
    same_day = registry.parse(text(date_field(min="2027-03-01", max="2027-03-01"))).fields[-1]
    assert same_day.minimum == same_day.maximum
    leap = registry.parse(text(date_field(min="2028-02-29"))).fields[-1]
    assert leap.minimum == datetime.date(2028, 2, 29)


@pytest.mark.parametrize(
    "overrides",
    [
        {"min": "2000-1-1"},
        {"min": "20000101"},
        {"min": "2000-01-01T00:00:00"},
        {"min": " 2000-01-01"},
        {"min": "2000-01-01\n"},
        {"min": "٢٠٠٠-٠١-٠١"},
        {"min": "2000/01/01"},
        {"min": ""},
        {"min": 2000},
        {"min": 20000101},
        {"min": None},
        {"min": True},
        {"max": "2100-12-3"},
        {"max": "2100-12-31 "},
        {"max": 2100},
        {"max": None},
    ],
)
def test_a_date_bound_must_be_plain_iso_text(overrides):
    refused(date_field(**overrides), "must be a calendar date written YYYY-MM-DD")


@pytest.mark.parametrize(
    "overrides",
    [
        {"min": "2027-02-29"},
        {"min": "2027-02-30"},
        {"min": "2000-13-01"},
        {"min": "2000-00-10"},
        {"min": "0000-01-01"},
        {"max": "2100-04-31"},
    ],
)
def test_a_date_bound_must_be_a_real_calendar_date(overrides):
    refused(date_field(**overrides), "must be a real calendar date")


def test_a_date_window_is_ordered_and_needs_both_bounds():
    refused(date_field(min="2100-12-31", max="2000-01-01"), "min is above max")
    for key in ("min", "max"):

        def drop(data, key=key):
            date_field()(data)
            data["fields"][-1].pop(key)

        refused(drop, "a date field needs min and max")


def test_a_date_field_takes_no_currency_scale_values_or_lengths():
    refused(date_field(currency="PKR"), "a date field has no currency")
    refused(date_field(scale=2), "only a decimal field has scale")
    refused(date_field(allowed_values=["a"]), "only an enum has allowed values")
    refused(date_field(error_code="invalid_measure"), "only a decimal field has error_code")

    def with_length(data):
        date_field()(data)
        data["fields"][-1]["max_length"] = 5

    refused(with_length, "only a text field has min_length or max_length")


def test_only_a_date_may_carry_date_text_in_its_bounds():
    refused(
        add_field(id="amount", type="money", min="2000-01-01", max="2100-12-31", currency="PKR")
    )
    refused(add_field(id="rooms", type="integer", min="2000-01-01", max="2100-12-31"))
    refused(add_field(id="width", type="decimal", min="2000-01-01", max="2100-12-31", scale=2))
    refused(add_field(id="flag", type="boolean", min="2000-01-01"), "only a money field")
    refused(add_field(type="text", min="2000-01-01"), "only a money field")


def test_a_date_field_is_served_with_iso_text_bounds_and_the_loader_hash_changes():
    parsed = registry.parse(text(date_field()))
    assert parsed.content_hash != registry.parse(text()).content_hash
    assert "date" in registry.FIELD_TYPES


def test_a_private_date_field_is_allowed_but_cannot_be_searchable():
    assert (
        registry.parse(text(date_field(**{"class": "private"}))).fields[-1].field_class == "private"
    )
    refused(date_field(**{"class": "private", "searchable": True}), "private field")


def test_version_6_adds_the_terms_after_every_details_field():
    assert registry.registry_version() == 6
    ids = [f.id for f in registry.fields()]
    assert ids[-len(TAIL_IDS) :] == list(TAIL_IDS)
    assert registry.field("availability_date").type == "date"
    assert [f.id for f in registry.fields() if f.type == "date"] == ["availability_date"]


def test_the_terms_applicability_is_by_purpose_only():
    for name in TERMS_IDS:
        scope = registry.field(name).applicability
        assert scope.categories == () and scope.subtypes == (), name
    assert registry.field("price_negotiable").applicability.purposes == ()
    assert registry.field("possession_availability").applicability.purposes == ("sale",)
    assert registry.field("possession_availability").applies_to("sale", "house", "house")
    assert not registry.field("possession_availability").applies_to("rent", "house", "house")
    assert registry.field("security_deposit").applies_to("rent", "plot", "plot_file")
    assert not registry.field("security_deposit").applies_to("sale", "plot", "plot_file")
    assert registry.field("rental_period").applicability.purposes == ("rent",)


def test_the_unsupported_terms_are_not_in_the_registry():
    for name in (
        "payment_type",
        "down_payment",
        "total_installment_price",
        "monthly_installment",
        "remaining_installments",
        "ownership_document_type",
        "title_document_type",
        "approval_information",
        "installment_available",
    ):
        assert registry.field(name) is None


def test_a_date_bound_change_changes_the_loader_hash():
    base = registry.parse(text(date_field())).content_hash
    assert registry.parse(text(date_field(max="2100-12-30"))).content_hash != base
    assert registry.parse(text(date_field(min="2000-01-02"))).content_hash != base
    assert registry.parse(text(date_field())).content_hash == base


def test_an_unquoted_yaml_date_asks_for_quotes():
    for key in ("min", "max"):
        raw = yaml.safe_dump(
            {**GOOD, "fields": [*GOOD["fields"], _unquoted(**{key: "2027-03-01"})]}
        ).replace("'2027-03-01'", "2027-03-01")
        with pytest.raises(ImproperlyConfigured, match="quote the date"):
            registry.parse(raw)


def _unquoted(**overrides):
    return {
        "id": "move_in",
        "type": "date",
        "min": "2000-01-01",
        "max": "2100-12-31",
        "requirement": "optional",
        "applicability": {"purposes": [], "categories": [], "subtypes": []},
        "class": "public",
        **overrides,
    }


def test_section_is_terms_or_details_and_defaults_to_details():
    assert registry.parse(text(add_field())).fields[-1].section == "details"
    assert registry.parse(text(add_field(section="details"))).fields[-1].section == "details"
    assert registry.parse(text(add_field(section="terms"))).fields[-1].section == "terms"
    for bad in ("Terms", "price", "", None, 1, True, ["terms"]):
        refused(add_field(section=bad), "section must be one of details, terms")


def test_the_shipped_terms_fields_are_in_the_terms_section():
    terms = {f.id for f in registry.fields() if f.section == "terms"}
    assert terms == {*TERMS_IDS, "rental_period"}
    assert registry.field("asking_price").section == "details"
