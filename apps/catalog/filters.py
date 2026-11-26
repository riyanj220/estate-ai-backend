"""The public filter (Story 3.3): an abstract specification generated from the Field registry.

`filterable()` says which fields can be filtered and how, `parse()` turns query parameters into a
`ListingFilter`, and `listings.selectors.search_public` is the only code that turns one into a
query. Nothing here knows how a Listing is stored. A newly `searchable` registry field of a
structured type is filterable with no change here.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from apps.catalog import registry, selectors

# Always filterable: the taxonomy every Listing has (not flagged `searchable` in the registry).
TAXONOMY = ("purpose", "category", "subtype")
# Searchable but not a filter of its own: area is filtered by the normalised `area_sqft`.
NOT_FILTERED = ("area_value", "area_unit")

CHOICE, RANGE, BOOLEAN, AMENITIES = "choice", "range", "boolean", "amenities"
# registry type -> how it is filtered; a type that is not here (free text) has no filter
KINDS = {
    "enum": CHOICE,
    "reference": CHOICE,
    "integer": RANGE,
    "decimal": RANGE,
    "money": RANGE,
    "date": RANGE,
    "boolean": BOOLEAN,
    "amenities": AMENITIES,
}
PREFER = "prefer_"
MAX_VALUES = 50  # in one comma-separated parameter
_NUMBER = re.compile(r"\d{1,16}(?:\.\d{1,8})?", re.ASCII)
_INTEGER = re.compile(r"\d{1,10}", re.ASCII)


@dataclass(frozen=True)
class Filterable:
    definition: registry.FieldDef
    kind: str

    @property
    def id(self) -> str:
        return self.definition.id

    @property
    def params(self) -> tuple[str, ...]:
        return (f"{self.id}_min", f"{self.id}_max") if self.kind == RANGE else (self.id,)


@dataclass(frozen=True)
class Criterion:
    """One condition on one registry field. CHOICE: any of `values`. BOOLEAN: equals `values[0]`.
    AMENITIES: the code `values[0]` is `present`. RANGE: `low` to `high`, inclusive, either open.
    A Listing that has not answered the field never satisfies a criterion on it."""

    field: str
    kind: str
    values: tuple = ()
    low: object = None
    high: object = None


@dataclass(frozen=True)
class ListingFilter:
    """`required` criteria must all hold. `preferred` ones never exclude a Listing: each one it
    satisfies adds one to its relevance."""

    required: tuple[Criterion, ...] = ()
    preferred: tuple[Criterion, ...] = ()


class InvalidFilter(ValueError):
    """`errors` maps each bad parameter to a stable code; submitted values are not kept."""

    def __init__(self, errors: Mapping[str, str]) -> None:
        super().__init__("invalid filter")
        self.errors = dict(errors)


def filterable() -> tuple[Filterable, ...]:
    """The filters, in registry order. A field of class `private` is never one."""
    return tuple(
        Filterable(f, KINDS[f.type])
        for f in registry.fields()
        if f.field_class == "public"
        and (f.searchable or f.id in TAXONOMY)
        and f.id not in NOT_FILTERED
        and f.type in KINDS
    )


def _scalar(definition: registry.FieldDef, text: str):
    """One bound of a range. Below the registry minimum is allowed (`0` is a natural lower
    bound); above its maximum is `out_of_range`."""
    if definition.type == "date":
        try:
            value = date.fromisoformat(text)
        except ValueError:
            raise InvalidFilter({"": "invalid"}) from None
        if value.isoformat() != text:  # `YYYY-MM-DD` only
            raise InvalidFilter({"": "invalid"})
    else:
        pattern = _INTEGER if definition.type == "integer" else _NUMBER
        if not pattern.fullmatch(text):
            raise InvalidFilter({"": "invalid"})
        value = int(text) if definition.type == "integer" else Decimal(text)
    if definition.maximum is not None and value > definition.maximum:
        raise InvalidFilter({"": "out_of_range"})
    return value


def _values(item: Filterable, text: str, codes) -> tuple:
    """The distinct values of a comma-separated (or boolean) parameter, sorted."""
    if item.kind == BOOLEAN:
        if text not in ("true", "false"):
            raise InvalidFilter({"": "invalid"})
        return (text == "true",)
    parts = text.split(",")
    if len(parts) > MAX_VALUES:
        raise InvalidFilter({"": "too_many_values"})
    if item.definition.type == "reference":
        keys = [selectors.parse_uuid(part) for part in parts]
        if None in keys:
            raise InvalidFilter({"": "invalid"})
        return tuple(sorted({str(key) for key in keys}))
    allowed = codes() if item.kind == AMENITIES else item.definition.allowed_values
    if not set(parts) <= set(allowed):
        raise InvalidFilter({"": "invalid_choice"})
    return tuple(sorted(set(parts)))


def parse(params: Mapping[str, Sequence[str]]) -> ListingFilter:
    """The filter in `params` (name -> every value given). Raises `InvalidFilter` naming each
    unknown, repeated or bad parameter, and `<field>_max` for an inverted range."""
    left = dict(params)
    errors: dict[str, str] = {}
    cached: list = []

    def codes():  # read once, and only when an amenity is asked for
        if not cached:
            cached.append(selectors.amenity_codes())
        return cached[0]

    def take(name, convert):
        given = left.pop(name, None)
        if given is None:
            return None
        try:
            if len(given) != 1:
                raise InvalidFilter({"": "repeated_parameter"})
            return convert(given[0])
        except InvalidFilter as problem:
            errors[name] = problem.errors[""]
            return None

    def criteria(prefix: str) -> tuple[Criterion, ...]:
        found = []
        for item in filterable():
            if item.kind == RANGE:
                low_name, high_name = (prefix + name for name in item.params)
                low = take(low_name, lambda text, d=item.definition: _scalar(d, text))
                high = take(high_name, lambda text, d=item.definition: _scalar(d, text))
                if low is not None and high is not None and low > high:
                    errors[high_name] = "inverted_range"
                elif low is not None or high is not None:
                    found.append(Criterion(item.id, item.kind, low=low, high=high))
                continue
            values = take(prefix + item.id, lambda text, i=item: _values(i, text, codes))
            if values is None:
                continue
            if item.kind == AMENITIES:  # one criterion per code: all required, each counted
                found.extend(Criterion(item.id, item.kind, (code,)) for code in values)
            else:
                found.append(Criterion(item.id, item.kind, values))
        return tuple(found)

    spec = ListingFilter(criteria(""), criteria(PREFER))
    errors.update({name[:64]: "unknown_parameter" for name in left})
    if errors:
        raise InvalidFilter(errors)
    return spec
