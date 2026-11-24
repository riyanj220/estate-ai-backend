"""The Field registry (AD-4): a versioned YAML file loaded and validated at start-up.

`parse(text)` is pure and raises `ImproperlyConfigured` with a one-line reason; the accessors read
the cached registry loaded from `registry/fields.yaml` and return frozen values. A change to the
file needs a version bump and a new lock (`scripts/registry_lock.py`); see `docs/registry.md`.
"""

import datetime
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

FIELD_TYPES = ("enum", "text", "money", "integer", "decimal", "boolean", "date", "reference")
REQUIREMENTS = ("required_for_publication", "recommended", "optional")
CLASSES = ("public", "private")
SECTIONS = ("details", "terms", "location")  # where a form groups a field; `details` by default
REFERENCES = ("city", "locality")  # what a `reference` field points at (catalog reference data)
FLAGS = ("searchable", "sortable", "ai_extractable", "valuation_input", "quota_identity")
PRIVATE_FORBIDDEN_FLAGS = ("searchable", "sortable", "valuation_input", "ai_extractable")
_ID = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
# A decimal part is only for an enum whose values are ALL numbers that fit NUMERIC(6,2)
# (`272.25`, square feet per marla); see `_check_numeric_enum`.
_VALUE_OR_NUMBER = re.compile(r"^(?:[a-z0-9_]{1,48}|\d{1,4}\.\d{1,2})$")
_NUMBER = re.compile(r"^\d{1,4}(?:\.\d{1,2})?$")
TAXONOMY_ID_MAX = 32  # the Listing columns are CharField(max_length=32)
_TOP_KEYS = {"version", "taxonomy", "fields"}
_TAXONOMY_KEYS = {"purposes", "categories"}
_CATEGORY_KEYS = {"id", "subtypes"}
_REQUIRED_FIELD_KEYS = {"id", "type", "requirement", "applicability", "class"}
_TEXT_KEYS = {"min_length", "max_length"}
_MONEY_KEYS = {"min", "max", "currency"}
_DECIMAL_KEYS = {"min", "max", "scale"}
_FIELD_KEYS = (
    _REQUIRED_FIELD_KEYS
    | {
        "allowed_values",
        "allowed_values_by_purpose",
        "allowed_values_by_subtype",
        "derived",
        "description",
        "seller_declared",
        "section",
        "error_code",
        "reference",
        "signed",
        *FLAGS,
    }
    | _TEXT_KEYS
    | _MONEY_KEYS
    | _DECIMAL_KEYS
)
DESCRIPTION_MAX = 200
DECIMAL_SCALE_MAX = 8
DECIMAL_WHOLE_DIGITS = 16  # NUMERIC(20,4) holds 16 whole digits
CURRENCY = "PKR"  # the only currency; money columns are NUMERIC(14,2)
MONEY_DECIMALS = 2
MONEY_CEILING = Decimal("999999999999.99")  # what NUMERIC(14,2) holds
INTEGER_CEILING = 1_000_000_000  # integer columns are 32-bit
TEXT_LENGTH_CEILING = 20_000  # a text field longer than this is a different kind of field
_MONEY_VALUE = re.compile(r"^\d{1,12}(?:\.\d{1,2})?$", re.ASCII)
_ASCII_DIGITS = frozenset("0123456789")
_DATE_KEYS = {"min", "max"}
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
    # money, decimal and integer: inclusive bounds (the minimum is the smallest amount allowed),
    # currency PKR; date: inclusive calendar dates (`datetime.date`, never a timestamp)
    minimum: Decimal | datetime.date | None = None
    maximum: Decimal | datetime.date | None = None
    currency: str | None = None
    # enum narrowed by Listing purpose: ((purpose, (value, ...)), ...); None means no narrowing
    allowed_values_by_purpose: tuple[tuple[str, tuple[str, ...]], ...] | None = None
    # enum narrowed by Listing Subtype: ((subtype, (value, ...)), ...), one entry per Subtype the
    # field applies to; None means no narrowing
    allowed_values_by_subtype: tuple[tuple[str, tuple[str, ...]], ...] | None = None
    # a claim the owner makes (legal, approval, possession): shown as "Seller-declared" and never
    # as a platform-verified fact
    seller_declared: bool = False
    # presentation grouping: `terms` (price and sale or rent terms) or `details` (the default)
    section: str = "details"
    # decimal: the code for a well-formed value outside its rules (`invalid_measure` unless the
    # registry says `invalid_area`)
    error_code: str = "invalid_measure"
    # decimal: inclusive bounds (`minimum`, `maximum`) and the number of decimals stored
    scale: int | None = None
    # decimal: the range may reach below zero (coordinates); false means min and max are >= 0
    signed: bool = False
    # reference: what the stored uuid points at (`city` or `locality`, catalog reference data)
    reference: str | None = None
    # derived: computed by the service from other fields; never accepted as input
    derived: bool = False
    description: str | None = None

    def values_for(self, purpose: str | None, subtype: str | None = None) -> tuple[str, ...] | None:
        """The enum values allowed for a Listing of this purpose and Subtype (all of them without
        a map; both maps narrow together; with a Subtype map and no Subtype nothing is allowed)."""
        values = self.allowed_values
        if self.allowed_values_by_purpose is not None:
            values = dict(self.allowed_values_by_purpose).get(purpose, ())
        if self.allowed_values_by_subtype is not None:
            if subtype is None:
                return ()  # a Subtype map needs the Subtype: nothing is allowed without it
            narrowed = dict(self.allowed_values_by_subtype).get(subtype, ())
            values = tuple(v for v in narrowed if values is None or v in values)
        return values

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
        subset = _id_list(raw[purpose], "allowed_values_by_purpose", _VALUE_OR_NUMBER)
        if not subset or set(subset) - set(values):
            _fail("allowed_values_by_purpose must be a non-empty subset of allowed_values")
        pairs.append((purpose, subset))
    return tuple(pairs)


def _by_subtype(entry: dict, values, applicable: tuple[str, ...], by_purpose) -> tuple | None:
    if "allowed_values_by_subtype" not in entry:
        return None
    if entry["type"] != "enum":
        _fail("only an enum has allowed_values_by_subtype")
    raw = entry["allowed_values_by_subtype"]
    if not isinstance(raw, dict) or set(raw) != set(applicable):
        _fail(
            "allowed_values_by_subtype must list every subtype the field applies to, and only those"
        )
    pairs = []
    for subtype in applicable:
        subset = _id_list(raw[subtype], "allowed_values_by_subtype", _VALUE_OR_NUMBER)
        if not subset or set(subset) - set(values):
            _fail("allowed_values_by_subtype must be a non-empty subset of allowed_values")
        for _, by_purpose_subset in by_purpose or ():
            if not set(subset) & set(by_purpose_subset):
                _fail("allowed_values_by_subtype shares no value with allowed_values_by_purpose")
        pairs.append((subtype, subset))
    if set(values) - {v for _, subset in pairs for v in subset}:
        _fail("allowed_values_by_subtype leaves an allowed value that no subtype offers")
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
    if entry["type"] in ("decimal", "integer", "date"):
        return {}  # judged by _decimal_rules, _integer_rules and _date_rules
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


def _integer_rules(entry: dict) -> dict:
    if entry["type"] != "integer":
        return {}
    if "currency" in entry:
        _fail("an integer field has no currency")
    if "min" not in entry or "max" not in entry:
        _fail("an integer field needs min and max")
    minimum = _int_bound(entry, "min", 0, INTEGER_CEILING)
    maximum = _int_bound(entry, "max", minimum, INTEGER_CEILING)
    return {"minimum": Decimal(minimum), "maximum": Decimal(maximum)}


def iso_date_shape(value) -> bool:
    """Whether `value` is text written exactly `YYYY-MM-DD` in ASCII digits (checked by position,
    never by a number-group pattern; whether the date exists is `calendar_date`'s question)."""
    return (
        isinstance(value, str)
        and len(value) == 10
        and value[4] == value[7] == "-"
        and all(c in _ASCII_DIGITS for c in value[:4] + value[5:7] + value[8:])
    )


def calendar_date(value: str) -> datetime.date | None:
    """The real calendar date written `YYYY-MM-DD`, or None (a date that does not exist, as in
    `2027-02-30`, or year `0000`, is None). Only a plain `datetime.date`, never a timestamp."""
    try:
        return datetime.date(int(value[:4]), int(value[5:7]), int(value[8:]))
    except ValueError:
        return None


def _date_bound(entry: dict, key: str) -> datetime.date:
    value = entry.get(key)
    if isinstance(value, datetime.date):  # YAML turned an unquoted 2000-01-01 into a date
        _fail(f"{key} must be quoted text: quote the date")
    if not iso_date_shape(value):
        _fail(f"{key} must be a calendar date written YYYY-MM-DD")
    parsed = calendar_date(value)
    if parsed is None:
        _fail(f"{key} must be a real calendar date")
    return parsed


def _date_rules(entry: dict) -> dict:
    if entry["type"] != "date":
        return {}
    if "currency" in entry:
        _fail("a date field has no currency")
    if _DATE_KEYS - set(entry):
        _fail("a date field needs min and max")
    minimum, maximum = _date_bound(entry, "min"), _date_bound(entry, "max")
    if minimum > maximum:
        _fail("min is above max")
    return {"minimum": minimum, "maximum": maximum}


def _decimal_amount(entry: dict, key: str, scale: int, signed: bool) -> Decimal:
    value = entry.get(key)
    sign = "-?" if signed else ""
    pattern = rf"^{sign}\d{{1,{DECIMAL_WHOLE_DIGITS}}}(?:\.\d{{1,{scale}}})?$"
    if not isinstance(value, str) or not re.fullmatch(pattern, value, re.ASCII):
        _fail(f"{key} must be a decimal string with at most {scale} decimals")
    return Decimal(value)


ERROR_CODES = ("invalid_measure", "invalid_area", "out_of_range")


def _decimal_rules(entry: dict) -> dict:
    if entry["type"] != "decimal":
        if "signed" in entry:
            _fail("only a decimal field has signed")
        if "scale" in entry:
            _fail("only a decimal field has scale")
        if "error_code" in entry:
            _fail("only a decimal field has error_code")
        return {}
    if entry.get("error_code", "invalid_measure") not in ERROR_CODES:
        _fail(f"error_code must be one of {', '.join(ERROR_CODES)}")
    if "currency" in entry:
        _fail("a decimal field has no currency")
    if _DECIMAL_KEYS - set(entry):
        _fail("a decimal field needs min, max and scale")
    scale = entry["scale"]
    if isinstance(scale, bool) or not isinstance(scale, int) or not 1 <= scale <= DECIMAL_SCALE_MAX:
        _fail(f"scale must be an integer from 1 to {DECIMAL_SCALE_MAX}")
    signed = entry.get("signed", False)
    if not isinstance(signed, bool):
        _fail("signed must be true or false")
    minimum = _decimal_amount(entry, "min", scale, signed)
    maximum = _decimal_amount(entry, "max", scale, signed)
    if minimum > maximum:
        _fail("min is above max")
    code = entry.get("error_code", "invalid_measure")
    return {
        "minimum": minimum,
        "maximum": maximum,
        "scale": scale,
        "error_code": code,
        "signed": signed,
    }


def _reference_rules(entry: dict) -> dict:
    if entry["type"] != "reference":
        if "reference" in entry:
            _fail("only a reference field has reference")
        return {}
    if entry.get("reference") not in REFERENCES:
        _fail(f"a reference field needs reference: one of {', '.join(REFERENCES)}")
    return {"reference": entry["reference"]}


def _check_numeric_enum(values: tuple[str, ...]) -> None:
    """A value with a decimal point needs every value to be a NUMERIC(6,2) number, all different
    as numbers (`225` and `225.00` would be the same stored value)."""
    if not any("." in v for v in values):
        return
    if not all(_NUMBER.fullmatch(v) for v in values):
        _fail("a decimal enum value needs every value to be a number of up to 4 + 2 digits")
    if len({Decimal(v) for v in values}) != len(values):
        _fail("enum values are numerically equal")


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
    category_of = {s: c.id for c in categories for s in c.subtypes}
    if applicability["categories"] and any(
        category_of[s] not in applicability["categories"] for s in applicability["subtypes"]
    ):
        _fail("applicability lists a subtype outside its listed categories")
    flags = {name: _flag(entry, name) for name in FLAGS}
    if entry["class"] == "private" and any(flags[n] for n in PRIVATE_FORBIDDEN_FLAGS):
        _fail("a private field cannot be searchable, sortable, ai_extractable or a valuation input")
    values = None
    if entry["type"] == "enum":
        values = _id_list(entry.get("allowed_values"), "allowed_values", _VALUE_OR_NUMBER)
        if not values:
            _fail("an enum has no allowed values")
        _check_numeric_enum(values)
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
    if applicability["subtypes"]:
        applicable = applicability["subtypes"]
    else:  # the subtypes of the listed categories, or every subtype when none are listed
        listed = applicability["categories"] or [c.id for c in categories]
        applicable = tuple(s for c in categories if c.id in listed for s in c.subtypes)
    by_subtype = _by_subtype(entry, values, applicable, by_purpose)
    section = entry.get("section", "details")
    if section not in SECTIONS:
        _fail(f"section must be one of {', '.join(SECTIONS)}")
    seller_declared = entry.get("seller_declared", False)
    if not isinstance(seller_declared, bool):
        _fail("seller_declared must be true or false")
    text_rules = _text_rules(entry)
    money_rules = _money_rules(entry)
    decimal_rules = _decimal_rules(entry)
    integer_rules = _integer_rules(entry)
    date_rules = _date_rules(entry)
    reference_rules = _reference_rules(entry)
    derived = entry.get("derived", False)
    if not isinstance(derived, bool):
        _fail("derived must be true or false")
    if derived and (
        entry["requirement"] != "optional"
        or flags["ai_extractable"]
        or flags["valuation_input"]
        or flags["quota_identity"]
    ):
        _fail("a derived field is optional and cannot be ai_extractable or a model input")
    description = entry.get("description")
    if description is not None and (
        not isinstance(description, str) or not 1 <= len(description) <= DESCRIPTION_MAX
    ):
        _fail(f"description must be text of 1 to {DESCRIPTION_MAX} characters")
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
        **decimal_rules,
        **integer_rules,
        **date_rules,
        **reference_rules,
        allowed_values_by_purpose=by_purpose,
        allowed_values_by_subtype=by_subtype,
        seller_declared=seller_declared,
        section=section,
        derived=derived,
        description=description,
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
