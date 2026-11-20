"""The Field registry (AD-4): a versioned YAML file loaded and validated at start-up.

`parse(text)` is pure and raises `ImproperlyConfigured` with a one-line reason; the accessors read
the cached registry loaded from `registry/fields.yaml` and return frozen values. A change to the
file needs a version bump and a new lock (`scripts/registry_lock.py`); see `docs/registry.md`.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import yaml
from django.core.exceptions import ImproperlyConfigured

DIRECTORY = Path(__file__).resolve().parent / "registry"
FIELDS_PATH = DIRECTORY / "fields.yaml"
LOCK_PATH = DIRECTORY / "fields.lock"

FIELD_TYPES = ("enum", "text", "money", "integer", "decimal", "boolean")
REQUIREMENTS = ("required_for_publication", "recommended", "optional")
CLASSES = ("public", "private")
FLAGS = ("searchable", "sortable", "ai_extractable", "valuation_input", "quota_identity")
PRIVATE_FORBIDDEN_FLAGS = ("searchable", "sortable", "valuation_input")
_ID = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_VALUE = re.compile(r"^[a-z0-9_]{1,48}$")  # enum values may start with a digit
TAXONOMY_ID_MAX = 32  # the Listing columns are CharField(max_length=32)
_TOP_KEYS = {"version", "taxonomy", "fields"}
_TAXONOMY_KEYS = {"purposes", "categories"}
_CATEGORY_KEYS = {"id", "subtypes"}
_REQUIRED_FIELD_KEYS = {"id", "type", "requirement", "applicability", "class"}
_TEXT_KEYS = {"min_length", "max_length"}
_MONEY_KEYS = {"min", "max", "currency"}
_FIELD_KEYS = (
    _REQUIRED_FIELD_KEYS
    | {"allowed_values", "allowed_values_by_purpose", *FLAGS}
    | _TEXT_KEYS
    | _MONEY_KEYS
)
CURRENCY = "PKR"  # the only currency; money columns are NUMERIC(14,2)
MONEY_DECIMALS = 2
MONEY_CEILING = Decimal("999999999999.99")  # what NUMERIC(14,2) holds
TEXT_LENGTH_CEILING = 20_000  # a text field longer than this is a different kind of field
_MONEY_VALUE = re.compile(r"^\d{1,12}(?:\.\d{1,2})?$", re.ASCII)
_APPLICABILITY_KEYS = ("purposes", "categories", "subtypes")


@dataclass(frozen=True)
class Category:
    id: str
    subtypes: tuple[str, ...]


@dataclass(frozen=True)
class Applicability:
    """Empty means the field applies to every value of that dimension."""

    purposes: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    subtypes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FieldDef:
    id: str
    type: str
    requirement: str
    applicability: Applicability
    allowed_values: tuple[str, ...] | None
    field_class: str  # `class` on the wire
    searchable: bool
    sortable: bool
    ai_extractable: bool
    valuation_input: bool
    quota_identity: bool
    # text: length bounds in characters after normalisation (`min_length` 0 means no minimum)
    min_length: int | None = None
    max_length: int | None = None
    # money: inclusive bounds (the minimum is the smallest amount allowed), currency PKR
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    currency: str | None = None
    # enum narrowed by Listing purpose: ((purpose, (value, ...)), ...); None means no narrowing
    allowed_values_by_purpose: tuple[tuple[str, tuple[str, ...]], ...] | None = None

    def values_for(self, purpose: str | None) -> tuple[str, ...] | None:
        """The enum values allowed for a Listing of this purpose (all of them without a map)."""
        if self.allowed_values_by_purpose is not None:
            return dict(self.allowed_values_by_purpose).get(purpose, ())
        return self.allowed_values

    def applies_to(self, purpose: str, category: str, subtype: str) -> bool:
        """Whether the field applies to a Listing with this purpose, Category and Subtype."""
        scope = self.applicability
        return (
            (not scope.purposes or purpose in scope.purposes)
            and (not scope.categories or category in scope.categories)
            and (not scope.subtypes or subtype in scope.subtypes)
        )


@dataclass(frozen=True)
class Registry:
    version: int
    content_hash: str
    purposes: tuple[str, ...]
    categories: tuple[Category, ...]
    fields: tuple[FieldDef, ...]


class _Loader(yaml.SafeLoader):
    """Safe loading that refuses a mapping key written twice."""


def _construct_mapping(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            raise ImproperlyConfigured("Field registry: merge keys are not allowed")
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            raise ImproperlyConfigured("Field registry: every key must be a plain string")
        if key in seen:
            raise ImproperlyConfigured("Field registry: a key is written twice")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _fail(reason: str):
    raise ImproperlyConfigured(f"Field registry: {reason}")


def _is_id(value) -> bool:
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def _id_list(value, what: str, pattern=_ID) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(v, str) and pattern.fullmatch(v) for v in value
    ):
        _fail(f"{what} must be a list of snake_case ids")
    if len(set(value)) != len(value):
        _fail(f"{what} has a duplicate id")
    return tuple(value)


def _mapping(value, keys: set[str], what: str, required: set[str] | None = None) -> dict:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        _fail(f"{what} must be a mapping")
    if set(value) - keys:
        _fail(f"{what} has an unknown key")
    if (required or keys) - set(value):
        _fail(f"{what} is missing a key")
    return value


def canonical_hash(data) -> str:
    """SHA-256 of the parsed content in a canonical form, so layout and comments do not count."""
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _taxonomy(raw) -> tuple[tuple[str, ...], tuple[Category, ...]]:
    raw = _mapping(raw, _TAXONOMY_KEYS, "taxonomy")
    purposes = _id_list(raw["purposes"], "purposes")
    if not purposes:
        _fail("purposes is empty")
    if not isinstance(raw["categories"], list) or not raw["categories"]:
        _fail("categories must be a non-empty list")
    categories, seen_subtypes = [], set()
    for entry in raw["categories"]:
        entry = _mapping(entry, _CATEGORY_KEYS, "a category")
        if not _is_id(entry["id"]):
            _fail("a category id must be snake_case")
        subtypes = _id_list(entry["subtypes"], "subtypes")
        if not subtypes:
            _fail("a category has no subtypes")
        if seen_subtypes & set(subtypes):
            _fail("a subtype is listed under two categories")
        seen_subtypes |= set(subtypes)
        categories.append(Category(entry["id"], subtypes))
    if len({c.id for c in categories}) != len(categories):
        _fail("duplicate category id")
    ids = [*purposes, *(c.id for c in categories), *(s for c in categories for s in c.subtypes)]
    if any(len(i) > TAXONOMY_ID_MAX for i in ids):
        _fail(f"a purpose, category or subtype id is longer than {TAXONOMY_ID_MAX} characters")
    return purposes, tuple(categories)


def _flag(entry: dict, name: str) -> bool:
    value = entry.get(name, False)
    if not isinstance(value, bool):
        _fail(f"{name} must be true or false")
    return value


def _by_purpose(entry: dict, values, purposes) -> tuple | None:
    if "allowed_values_by_purpose" not in entry:
        return None
    if entry["type"] != "enum":
        _fail("only an enum has allowed_values_by_purpose")
    raw = entry["allowed_values_by_purpose"]
    if not isinstance(raw, dict) or set(raw) != set(purposes):
        _fail("allowed_values_by_purpose must list every purpose and only purposes")
    pairs = []
    for purpose in purposes:
        subset = _id_list(raw[purpose], "allowed_values_by_purpose", _VALUE)
        if not subset or set(subset) - set(values):
            _fail("allowed_values_by_purpose must be a non-empty subset of allowed_values")
        pairs.append((purpose, subset))
    return tuple(pairs)


def _int_bound(entry: dict, key: str, low: int, high: int) -> int | None:
    if key not in entry:
        return None
    value = entry[key]
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        _fail(f"{key} must be an integer from {low} to {high}")
    return value


def _text_rules(entry: dict) -> dict:
    present = _TEXT_KEYS & set(entry)
    if entry["type"] != "text":
        if present:
            _fail("only a text field has min_length or max_length")
        return {}
    if "max_length" not in entry:
        _fail("a text field needs max_length")
    maximum = _int_bound(entry, "max_length", 1, TEXT_LENGTH_CEILING)
    minimum = _int_bound(entry, "min_length", 0, maximum) or 0
    return {"min_length": minimum, "max_length": maximum}


def _money_amount(entry: dict, key: str) -> Decimal:
    value = entry.get(key)
    if not isinstance(value, str) or not _MONEY_VALUE.fullmatch(value):
        _fail(f"{key} must be a decimal string with at most {MONEY_DECIMALS} decimals")
    return Decimal(value)


def _money_rules(entry: dict) -> dict:
    present = _MONEY_KEYS & set(entry)
    if entry["type"] != "money":
        if present:
            _fail("only a money field has min, max or currency")
        return {}
    if present != _MONEY_KEYS:
        _fail("a money field needs min, max and currency")
    if entry["currency"] != CURRENCY:
        _fail(f"currency must be {CURRENCY}")
    minimum, maximum = _money_amount(entry, "min"), _money_amount(entry, "max")
    if minimum > maximum:
        _fail("min is above max")
    return {"minimum": minimum, "maximum": maximum, "currency": CURRENCY}


def _field(entry, purposes, categories) -> FieldDef:
    entry = _mapping(entry, _FIELD_KEYS, "a field", _REQUIRED_FIELD_KEYS)
    if not _is_id(entry["id"]):
        _fail("a field id must be snake_case")
    if entry["type"] not in FIELD_TYPES:
        _fail("a field has an unknown type")
    if entry["requirement"] not in REQUIREMENTS:
        _fail("a field has an unknown requirement")
    if entry["class"] not in CLASSES:
        _fail("a field class must be public or private")
    scope = _mapping(entry["applicability"], set(_APPLICABILITY_KEYS), "applicability")
    allowed = {
        "purposes": set(purposes),
        "categories": {c.id for c in categories},
        "subtypes": {s for c in categories for s in c.subtypes},
    }
    applicability = {}
    for key in _APPLICABILITY_KEYS:
        values = _id_list(scope[key], f"applicability {key}")
        if set(values) - allowed[key]:
            _fail(f"applicability {key} names a value outside the taxonomy")
        applicability[key] = values
    by_subtype = {s: c.id for c in categories for s in c.subtypes}
    if applicability["categories"] and any(
        by_subtype[s] not in applicability["categories"] for s in applicability["subtypes"]
    ):
        _fail("applicability lists a subtype outside its listed categories")
    flags = {name: _flag(entry, name) for name in FLAGS}
    if entry["class"] == "private" and any(flags[n] for n in PRIVATE_FORBIDDEN_FLAGS):
        _fail("a private field cannot be searchable, sortable or a valuation input")
    values = None
    if entry["type"] == "enum":
        values = _id_list(entry.get("allowed_values"), "allowed_values", _VALUE)
        if not values:
            _fail("an enum has no allowed values")
        bound = {
            "purpose": list(purposes),
            "category": [c.id for c in categories],
            "subtype": [s for c in categories for s in c.subtypes],
        }.get(entry["id"])
        if bound is not None and sorted(values) != sorted(bound):
            _fail("an enum's allowed values do not match the taxonomy")
    elif "allowed_values" in entry:
        _fail("only an enum has allowed values")
    by_purpose = _by_purpose(entry, values, purposes)
    text_rules = _text_rules(entry)
    money_rules = _money_rules(entry)
    return FieldDef(
        id=entry["id"],
        type=entry["type"],
        requirement=entry["requirement"],
        applicability=Applicability(**applicability),
        allowed_values=values,
        field_class=entry["class"],
        **flags,
        **text_rules,
        **money_rules,
        allowed_values_by_purpose=by_purpose,
    )


def parse(text: str) -> Registry:
    """Validate registry text and return it. Any problem is `ImproperlyConfigured`."""
    try:
        data = yaml.load(text, Loader=_Loader)  # noqa: S506 (a SafeLoader subclass)
    except yaml.YAMLError:
        _fail("the file is not valid YAML")
    data = _mapping(data, _TOP_KEYS, "the file")
    version = data["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        _fail("version must be a positive integer")
    purposes, categories = _taxonomy(data["taxonomy"])
    if not isinstance(data["fields"], list):
        _fail("fields must be a list")
    fields = tuple(_field(e, purposes, categories) for e in data["fields"])
    if len({f.id for f in fields}) != len(fields):
        _fail("duplicate field id")
    return Registry(version, canonical_hash(data), purposes, categories, fields)


def read_lock(text: str) -> tuple[int, str]:
    """The (version, hash) recorded in a lock file."""
    try:
        data = yaml.safe_load(text)
        return int(data["version"]), str(data["sha256"])
    except (yaml.YAMLError, KeyError, TypeError, ValueError):
        raise ImproperlyConfigured("Field registry: the lock file is malformed") from None


def render_lock(registry: Registry) -> str:
    return f"version: {registry.version}\nsha256: {registry.content_hash}\n"


@lru_cache(maxsize=1)
def load() -> Registry:
    """The registry from the packaged file, validated once per process."""
    return parse(FIELDS_PATH.read_text(encoding="utf-8"))


def registry_version() -> int:
    return load().version


def content_hash() -> str:
    return load().content_hash


def purposes() -> tuple[str, ...]:
    return load().purposes


def categories() -> tuple[Category, ...]:
    return load().categories


def fields() -> tuple[FieldDef, ...]:
    return load().fields


def field(field_id: str) -> FieldDef | None:
    return next((f for f in load().fields if f.id == field_id), None)


def subtypes_of(category: str) -> tuple[str, ...]:
    found = next((c for c in load().categories if c.id == category), None)
    return found.subtypes if found else ()


def all_subtypes() -> tuple[str, ...]:
    return tuple(s for c in load().categories for s in c.subtypes)


def valid_subtype(category: str, subtype: str) -> bool:
    return subtype in subtypes_of(category)
