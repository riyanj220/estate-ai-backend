"""The Field registry loader, accessors and lock (Story 2.1)."""

import copy
import dataclasses
from pathlib import Path

import pytest
import yaml
from django.core.exceptions import ImproperlyConfigured

from apps.catalog import registry

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
        "requirement": "optional",
        "applicability": {"purposes": [], "categories": [], "subtypes": []},
        "class": "public",
    }
    base.update(overrides)
    return lambda data: data["fields"].append(base)


# --- the shipped file -----------------------------------------------------------------------


def test_the_shipped_registry_loads_with_the_three_identity_fields():
    loaded = registry.load()
    assert loaded.version == 1
    assert [f.id for f in registry.fields()] == ["purpose", "category", "subtype"]
    assert registry.purposes() == ("sale", "rent")
    assert [c.id for c in registry.categories()] == ["house", "apartment", "plot", "commercial"]
    for definition in registry.fields():
        assert definition.requirement == "required_for_publication"
        assert definition.field_class == "public" and definition.quota_identity
        assert not (definition.searchable or definition.sortable or definition.valuation_input)


def test_plot_file_is_a_distinct_subtype_of_plot():
    assert registry.subtypes_of("plot") == ("residential_plot", "commercial_plot", "plot_file")
    assert registry.subtypes_of("commercial") == ("shop", "office")
    assert registry.valid_subtype("plot", "plot_file")
    assert registry.valid_subtype("plot", "residential_plot")
    assert not registry.valid_subtype("apartment", "shop")
    assert not registry.valid_subtype("nope", "house")
    assert registry.subtypes_of("nope") == ()


def test_accessors_return_immutable_values():
    assert registry.registry_version() == 1
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
    assert registry.load().version == 1


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
    fields.write_text(text(lambda d: (add_field()(d), d.update(version=2))))
    script.main()
    assert registry.read_lock(lock.read_text())[0] == 2
    assert "version 2" in capsys.readouterr().out


def test_the_files_are_found_from_any_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    registry.load.cache_clear()
    try:
        assert registry.load().version == 1
        assert registry.read_lock(registry.LOCK_PATH.read_text())[0] == 1
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
    lock.write_text("version: 5\nsha256: " + "0" * 64 + "\n")
    monkeypatch.setattr(registry, "FIELDS_PATH", fields)
    monkeypatch.setattr(registry, "LOCK_PATH", lock)
    with pytest.raises(SystemExit, match="lower"):
        script.main()
