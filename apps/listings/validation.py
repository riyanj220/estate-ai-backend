"""Pure validation of editable Listing fields, driven by the Field registry (AD-4).

No database access and no actor: `evaluate` judges an update against the CURRENT registry and
the Listing's resulting state, and returns normalised values plus a stable code per failing field.
Codes only; a submitted value is never copied into a result or an error.
"""

import re
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote, urlsplit

from apps.catalog import registry
from apps.catalog.selectors import parse_uuid
from apps.kernel.privacy.patterns import (
    ContactKind,
    find_contact_details,
    reject_contact_details,
)
from apps.listings.area import MARLA_UNITS
from apps.listings.fields import EDITABLE_FIELDS

RAW_TEXT_LIMIT = 20_000  # characters; checked before any scanning or normalisation
NON_FIELD = "non_field_errors"
_JOINERS = (
    "\N{ZERO WIDTH NON-JOINER}\N{ZERO WIDTH JOINER}"  # legitimate inside Urdu and Persian words
)
_MARKS = "\N{LEFT-TO-RIGHT MARK}\N{RIGHT-TO-LEFT MARK}"  # in pasted Urdu; overrides are refused
# Look-blank letters and symbols that are not whitespace (Hangul fillers, braille blank).
_BLANKS = frozenset(
    "\N{HANGUL FILLER}\N{HANGUL CHOSEONG FILLER}\N{HANGUL JUNGSEONG FILLER}"
    "\N{HALFWIDTH HANGUL FILLER}\N{BRAILLE PATTERN BLANK}"
)
_NO_MARKS = {ord(mark): None for mark in _MARKS}
_NEWLINES = ("\r\n", "\r", "\N{LINE SEPARATOR}", "\N{PARAGRAPH SEPARATOR}")
_MONEY_TEXT = re.compile(r"^(-)?(\d{1,30})(?:\.(\d{1,30}))?$", re.ASCII)
_MONEY_INPUT_MAX = 40
_INT_PRICE_LIMIT = 10**30
_DECIMAL_DIGITS_MAX = 30


def check_shape(base_revision, updates) -> dict[str, str]:
    """Problems with the request itself, before any Listing data is read."""
    errors: dict[str, str] = {}
    if base_revision is None:
        errors["base_revision"] = "required"
    elif isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 1:
        errors["base_revision"] = "invalid"
    if updates is None:
        errors["fields"] = "required"
    elif not isinstance(updates, Mapping):
        errors["fields"] = "not_a_dict"
    elif not updates:
        errors["fields"] = "empty"
    else:
        for name in updates:
            if not isinstance(name, str) or registry.field(name) is None:
                errors[NON_FIELD] = "unknown_field"  # never keyed by what the client sent
            elif name not in EDITABLE_FIELDS:
                errors[name] = "not_editable"
    return errors


def _has_letter_or_digit(text: str) -> bool:
    return any(char.isalpha() or unicodedata.category(char) == "Nd" for char in text)


def _refused_character(char: str) -> bool:
    if char in _JOINERS or char in _MARKS:
        return False
    if char in "\t\n\r" or char in _NEWLINES:
        return False  # whitespace: collapsed below
    return unicodedata.category(char)[0] == "C" or char in _BLANKS


def check_text(
    definition: registry.FieldDef, value, paragraphs: bool
) -> tuple[str | None, str | None]:
    """(normalised text or None to clear, code).

    Order: size bound on the raw input, character classes, trim and collapse (a description
    keeps single paragraph breaks), NFC, length, shape, then the shared contact-detail check.
    """
    if not isinstance(value, str):
        return None, "invalid"
    if len(value) > RAW_TEXT_LIMIT:
        return None, "too_long"
    if not value.strip():
        return None, None  # empty is null
    if any(_refused_character(char) for char in value):
        return None, "invalid"
    text = unicodedata.normalize("NFC", value)
    if paragraphs:
        for newline in _NEWLINES:
            text = text.replace(newline, "\n")
        lines: list[str] = []
        for line in text.split("\n"):
            line = " ".join(line.split())
            if line or (lines and lines[-1]):  # at most one blank line in a row
                lines.append(line)
        while lines and not lines[-1]:
            lines.pop()
        text = "\n".join(lines)
        edges = [line for line in lines if line]
    else:
        text = " ".join(text.split())
        edges = [text]
    if any(line[0] in _JOINERS or line[-1] in _JOINERS for line in edges):
        return None, "invalid"
    if definition.max_length is not None and len(text) > definition.max_length:
        return None, "too_long"
    if len(text.translate(_NO_MARKS)) < (definition.min_length or 0):
        return None, "too_short"
    if not _has_letter_or_digit(text):
        return None, "invalid"
    refused = reject_contact_details(text) is not None
    if refused and definition.field_class == "private":
        # the private address fields exist to hold an address; phone and email stay refused
        refused = any(f.kind is not ContactKind.ADDRESS for f in find_contact_details(text))
    if refused:
        return None, "contains_contact_details"
    return text, None


_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}", re.ASCII)
# hosts whose links are a way to message someone: contact details by another name
_CHAT_HOSTS = ("wa.me", "whatsapp.com", "t.me", "telegram.me", "m.me")


def check_url(definition: registry.FieldDef, value) -> tuple[str | None, str | None]:
    """(the link or None to clear, code). Only `https://` followed by a named host: no user
    part, port, IP address, space or non-ASCII character. A chat link, or a link whose text
    holds a phone number, email address or street address, is `contains_contact_details`.
    The server never opens the link; clients show it as a link only."""
    if not isinstance(value, str):
        return None, "invalid"
    if len(value) > RAW_TEXT_LIMIT:
        return None, "too_long"
    text = value.strip()
    if not text:
        return None, None
    if len(text) > definition.max_length:
        return None, "too_long"
    if not text.isascii() or not text.isprintable() or any(c in text for c in " \\<>\"'`"):
        return None, "invalid_url"
    if not text.startswith("https://"):
        return None, "invalid_url"
    try:
        host = urlsplit(text).netloc.lower()
    except ValueError:
        return None, "invalid_url"
    if _HOST.fullmatch(host) is None:  # also refuses `user@host` and `host:port`
        return None, "invalid_url"
    if any(host == chat or host.endswith(f".{chat}") for chat in _CHAT_HOSTS):
        return None, "contains_contact_details"
    if reject_contact_details(unquote(text)) is not None:
        return None, "contains_contact_details"
    return text, None


def check_money(definition: registry.FieldDef, value) -> tuple[Decimal | None, str | None]:
    """(amount with two decimals, code). Floats and booleans are never money."""
    if isinstance(value, bool) or isinstance(value, float):
        return None, "invalid"
    if isinstance(value, int):
        if abs(value) >= _INT_PRICE_LIMIT:
            return None, "invalid_price"
        text = str(value)
    elif isinstance(value, Decimal):
        if not value.is_finite() or value.adjusted() > _DECIMAL_DIGITS_MAX:
            return None, "invalid_price"
        text = format(value, "f")
    elif isinstance(value, str):
        text = value.strip()
        if len(text) > _MONEY_INPUT_MAX:
            return None, "invalid"
    else:
        return None, "invalid"
    match = _MONEY_TEXT.fullmatch(text)
    if match is None:
        return None, "invalid"
    negative, whole, fraction = match.groups()
    if negative or len(fraction or "") > registry.MONEY_DECIMALS:
        return None, "invalid_price"
    try:
        amount = Decimal(f"{whole}.{fraction or '0'}")
    except InvalidOperation:  # unreachable for the pattern above
        return None, "invalid"
    if amount < definition.minimum or amount > definition.maximum:
        return None, "invalid_price"
    return amount.quantize(Decimal("0.01")), None


def decimal_code(definition: registry.FieldDef) -> str:
    """The code for a well-formed decimal outside its rules, declared by the registry field
    (`error_code`: `invalid_area` for the area fields, otherwise `invalid_measure`)."""
    return definition.error_code


def check_decimal(definition: registry.FieldDef, value) -> tuple[Decimal | None, str | None]:
    """(value at the field's scale, code). Text only: numbers, floats and commas are refused.

    Valid text is ASCII `^\\d{1,10}(\\.\\d{1,scale})?$` (the field's scale, with a leading minus
    sign only for a field whose range reaches below zero, such as a coordinate) without leading
    zeros (`0.5` is fine, `0010` is not) and without surrounding whitespace; anything else that is
    not a plain decimal number is `invalid`. A well-formed number that is zero, negative, below the
    minimum, above the maximum or has too many decimals is `invalid_area` for an area and
    `invalid_measure` for any other decimal (feet, kilowatts); the registry's `error_code` decides.
    """
    if not isinstance(value, str) or len(value) > _MONEY_INPUT_MAX:
        return None, "invalid"
    match = _MONEY_TEXT.fullmatch(value)
    if match is None:
        return None, "invalid"
    negative, whole, fraction = match.groups()
    if len(whole) > 1 and whole.startswith("0"):
        return None, "invalid"  # zero-padded
    if (negative and not definition.signed) or len(fraction or "") > definition.scale:
        return None, decimal_code(definition)  # only a `signed` field may be negative
    amount = Decimal(f"{whole}.{fraction or '0'}")
    if negative:
        amount = -amount
    if amount == 0:
        amount = abs(amount)  # `-0` and `-0.000000` are plain zero, never a negative zero
    if amount < definition.minimum or amount > definition.maximum:
        return None, decimal_code(definition)
    return amount.quantize(Decimal(1).scaleb(-definition.scale)), None


def check_integer(definition: registry.FieldDef, value) -> tuple[int | None, str | None]:
    """(whole number, code). Only a JSON integer: text, floats and booleans are `invalid`; one
    outside the registry bounds is `invalid_range`."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None, "invalid"
    if value < definition.minimum or value > definition.maximum:
        return None, "invalid_range"
    return value, None


def check_boolean(value) -> tuple[bool | None, str | None]:
    """(true or false, code). Only JSON true or false; `1`, `"yes"` and the like are `invalid`."""
    if not isinstance(value, bool):
        return None, "invalid"
    return value, None


def check_date(definition: registry.FieldDef, value) -> tuple[date | None, str | None]:
    """(calendar date, code). Only JSON text `YYYY-MM-DD` in ASCII digits naming a real date:
    numbers, timestamps, other layouts (`2027-3-1`, `20270301`), other digits and a date that does
    not exist (`2027-02-30`) are `invalid`. A real date outside the registry window is
    `invalid_date`. No time zone or time of day is ever involved."""
    if not registry.iso_date_shape(value):
        return None, "invalid"
    parsed = registry.calendar_date(value)
    if parsed is None:
        return None, "invalid"
    if parsed < definition.minimum or parsed > definition.maximum:
        return None, "invalid_date"
    return parsed, None


Resolver = Callable[[str, uuid.UUID], "uuid.UUID | None"]


def check_reference(
    definition: registry.FieldDef, value, resolve: Resolver | None
) -> tuple[uuid.UUID | None, str | None, uuid.UUID | None]:
    """(uuid, code, parent). The uuid text of an ACTIVE city or locality in any common form (the
    catalog's one `parse_uuid`: any case, braced, hyphenated or not; the canonical lowercase
    hyphenated form is what is stored and served); `resolve(kind, id)` asks the catalog (the
    city's own id, the locality's city id, None when unknown or inactive). A name, a number or
    anything else is `invalid`. For a locality the parent is its city id, for the pair check.
    A reference is never judged without a resolver: that is a programming error."""
    if resolve is None:
        raise RuntimeError("a reference field needs a resolver")
    key = parse_uuid(value)
    if key is None:
        return None, "invalid", None
    parent = resolve(definition.reference, key)
    if parent is None:
        return None, "invalid", None
    return key, None, parent


AMENITY_STATES = ("present", "none")
AMENITIES_MAX = 40  # entries in one map; the whole catalogue is smaller


def check_amenities(
    value, current, codes: frozenset[str] | None
) -> tuple[dict[str, str] | None, str | None]:
    """(the map of catalogue code to `present` or `none`, code). The map REPLACES the stored one.
    `null` and `{}` clear it (None: nothing is answered). Anything but a JSON object, more than 40
    entries, a key that is not an active catalogue code (`codes`, the catalogue's active codes), or
    a state that is not exactly the text `present` or `none` is `invalid`. An entry the Listing
    already stores with the same state is kept even if the catalogue dropped the code since (the
    client re-sends what it read). A code left out is unanswered: it is never turned into `none`.
    Without `codes` the catalogue is treated as EMPTY (fail safe: nothing new is accepted, only an
    unchanged stored entry), never as "anything goes"."""
    codes = codes if codes is not None else frozenset()
    if not isinstance(value, dict):
        return None, "invalid"
    if len(value) > AMENITIES_MAX:
        return None, "invalid"
    stored = current if isinstance(current, dict) else {}
    result: dict[str, str] = {}
    for code, state in value.items():
        if not isinstance(code, str) or not isinstance(state, str):
            return None, "invalid"
        if state not in AMENITY_STATES:
            return None, "invalid"
        if code not in codes and stored.get(code) != state:
            return None, "invalid"
        result[code] = state
    return (result or None), None


# Enums whose values are numbers written as text: a JSON number is a wrong choice, not a wrong type.
_NUMERIC_ENUMS = frozenset({"marla_basis"})


def check_enum(
    definition: registry.FieldDef, value, purpose: str, subtype: str | None = None
) -> tuple[str | None, str | None]:
    """The value must be one the registry allows for this purpose and Subtype (a Plot File only
    allows `file_only`, for instance); anything else is `invalid_choice`."""
    if not isinstance(value, str):
        return None, "invalid_choice" if definition.id in _NUMERIC_ENUMS else "invalid"
    if value not in (definition.values_for(purpose, subtype) or ()):
        return None, "invalid_choice"
    return value, None


def _check_one(definition, value, purpose, subtype):
    if definition.type == "text":
        return check_text(definition, value, paragraphs=definition.id == "description")
    if definition.type == "money":
        return check_money(definition, value)
    if definition.type == "decimal":
        return check_decimal(definition, value)
    if definition.type == "enum":
        return check_enum(definition, value, purpose, subtype)
    if definition.type == "integer":
        return check_integer(definition, value)
    if definition.type == "boolean":
        return check_boolean(value)
    if definition.type == "date":
        return check_date(definition, value)
    if definition.type == "url":
        return check_url(definition, value)
    return None, "invalid"  # a type that has no editor yet


def evaluate(
    purpose: str,
    category: str,
    subtype: str,
    current: Mapping[str, object],
    updates: Mapping[str, object],
    resolve: Resolver | None = None,
    amenity_codes: frozenset[str] | None = None,
) -> tuple[dict[str, object], dict[str, str]]:
    """Normalised values for every update (None clears) and a code per failing field.

    Cross-field: the marla basis against the resulting unit (`_check_marla_basis`) and a
    floor against its building (`_check_floor`).
    Every update is judged against the current registry: the value rules and whether the field
    applies to this Listing. Failing fields are all reported at once. Completeness (a rental
    priced per rental period naming its period, required fields) is a publication rule
    (Story 2.15): saving never requires complete data.
    """
    values: dict[str, object] = {}
    errors: dict[str, str] = {}
    parents: dict[str, uuid.UUID] = {}  # a chosen locality's city
    unchanged: set[str] = set()  # references re-sent with their stored value
    for name, raw in updates.items():
        definition = registry.field(name)
        if definition is None:  # callers check the shape first; stay safe regardless
            errors[NON_FIELD] = "unknown_field"
        elif definition.derived:
            errors[name] = "not_editable"
        elif raw is None:
            values[name] = None
        elif not definition.applies_to(purpose, category, subtype):
            errors[name] = "not_applicable"
        elif (
            definition.type == "reference"
            and current.get(name) is not None
            and parse_uuid(raw) == current[name]
        ):
            # Re-saving the STORED value (it may have been deactivated since) is not judged
            # again; only a new value must be active.
            values[name] = current[name]
            unchanged.add(name)
        elif definition.type == "amenities":
            value, code = check_amenities(raw, current.get(name), amenity_codes)
            if code:
                errors[name] = code
            else:
                values[name] = value
        elif definition.type == "reference":
            value, code, parent = check_reference(definition, raw, resolve)
            if code:
                errors[name] = code
            else:
                values[name] = value
                if parent is not None and definition.reference == "locality":
                    parents[name] = parent
        else:
            value, code = _check_one(definition, raw, purpose, subtype)
            if code:
                errors[name] = code
            else:
                values[name] = value
    _check_marla_basis(current, values, errors)
    _check_floor(purpose, category, subtype, current, values, errors)
    _check_locality(current, values, errors, parents, unchanged, resolve)
    _check_coordinates(current, values, errors)
    return values, errors


def _check_locality(current, values, errors, parents, unchanged, resolve) -> None:
    """A locality belongs to the Listing's city, judged on the RESULTING pair. Changing the city
    (or clearing it) without sending a locality clears the stored locality in the same write, so a
    stale pair is never stored. A locality with no city is `invalid`; one of another city is
    `locality_not_in_city`. Stored data is only re-judged when this update touches the pair."""
    if "city" in errors or "locality" in errors:
        return
    if "city" in values and "locality" not in values:
        if values["city"] != current.get("city") and current.get("locality") is not None:
            values["locality"] = None  # reported as a changed field like any other
        return
    if "locality" not in values or values["locality"] is None:
        return
    city = values["city"] if "city" in values else current.get("city")
    if city is None:
        errors["locality"] = "invalid"
        return
    if "locality" in unchanged and city == current.get("city"):
        return  # the stored pair, re-sent as it is
    parent = parents.get("locality")
    if parent is None and "locality" in unchanged:  # the stored locality against a NEW city
        parent = resolve("locality", values["locality"]) if resolve else None
        if parent is None:
            errors["locality"] = "invalid"  # no longer active: it cannot follow a city change
            return
    if parent != city:
        errors["locality"] = "locality_not_in_city"


def _check_coordinates(current, values, errors) -> None:
    """Latitude and longitude are both set or both empty, judged on the RESULTING state; the code
    is `invalid` on the one that is missing."""
    if "latitude" not in values and "longitude" not in values:
        return
    if "latitude" in errors or "longitude" in errors:
        return
    latitude = values["latitude"] if "latitude" in values else current.get("latitude")
    longitude = values["longitude"] if "longitude" in values else current.get("longitude")
    if latitude is None and longitude is not None:
        errors["latitude"] = "invalid"
    elif longitude is None and latitude is not None:
        errors["longitude"] = "invalid"


def _check_floor(purpose, category, subtype, current, values, errors) -> None:
    """A floor cannot be above its building's floors, judged on the RESULTING state
    and only when this update touches one of the two (stored data is never re-judged). The code is
    on `floor_number`, and also on `building_floors` when the request sent it."""
    if "floor_number" not in values and "building_floors" not in values:
        return
    if "floor_number" in errors or "building_floors" in errors:
        return
    definition = registry.field("floor_number")
    if definition is None or not definition.applies_to(purpose, category, subtype):
        return
    floor = values["floor_number"] if "floor_number" in values else current.get("floor_number")
    building = (
        values["building_floors"] if "building_floors" in values else current.get("building_floors")
    )
    if floor is not None and building is not None and floor > building:
        errors["floor_number"] = "invalid_floor"
        if "building_floors" in values:  # the client finds the code under a field it sent
            errors["building_floors"] = "invalid_floor"


def _check_marla_basis(current, values, errors) -> None:
    """A marla basis applies only while the RESULTING unit is marla or kanal. A basis sent for any
    other unit is `not_applicable`; changing the unit away clears a stored basis (the service
    keeps stored data consistent), which is reported as `None` in `values`."""
    if "area_unit" in errors or "marla_basis" in errors:
        return
    unit = values["area_unit"] if "area_unit" in values else current.get("area_unit")
    if unit in MARLA_UNITS:
        return
    if values.get("marla_basis") is not None:
        errors["marla_basis"] = "not_applicable"
    elif "marla_basis" not in values and current.get("marla_basis") is not None:
        values["marla_basis"] = None
