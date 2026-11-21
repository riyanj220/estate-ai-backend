"""Pure validation of editable Listing fields, driven by the Field registry (AD-4).

No database access and no actor: `evaluate` judges an update against the CURRENT registry and
the Listing's resulting state, and returns normalised values plus a stable code per failing field.
Codes only; a submitted value is never copied into a result or an error.
"""

import re
import unicodedata
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from apps.catalog import registry
from apps.kernel.privacy.patterns import reject_contact_details
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
    if reject_contact_details(text) is not None:
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


def check_decimal(definition: registry.FieldDef, value) -> tuple[Decimal | None, str | None]:
    """(value at the field's scale, code). Text only: numbers, floats and commas are refused.

    Valid text is ASCII `^\\d{1,10}(\\.\\d{1,4})?$` without leading zeros (`0.5` is fine, `0010` is
    not) and without surrounding whitespace; anything else that is not a plain decimal number
    is `invalid`. A well-formed number that is zero, negative, below the minimum, above the
    maximum or has too many decimals is `invalid_area`.
    """
    if not isinstance(value, str) or len(value) > _MONEY_INPUT_MAX:
        return None, "invalid"
    match = _MONEY_TEXT.fullmatch(value)
    if match is None:
        return None, "invalid"
    negative, whole, fraction = match.groups()
    if len(whole) > 1 and whole.startswith("0"):
        return None, "invalid"  # zero-padded
    if negative or len(fraction or "") > definition.scale:
        return None, "invalid_area"
    amount = Decimal(f"{whole}.{fraction or '0'}")
    if amount < definition.minimum or amount > definition.maximum:
        return None, "invalid_area"
    return amount.quantize(Decimal(1).scaleb(-definition.scale)), None


# Enums whose values are numbers written as text: a JSON number is a wrong choice, not a wrong type.
_NUMERIC_ENUMS = frozenset({"marla_basis"})


def check_enum(definition: registry.FieldDef, value, purpose: str) -> tuple[str | None, str | None]:
    if not isinstance(value, str):
        return None, "invalid_choice" if definition.id in _NUMERIC_ENUMS else "invalid"
    if value not in (definition.values_for(purpose) or ()):
        return None, "invalid_choice"
    return value, None


def _check_one(definition, value, purpose):
    if definition.type == "text":
        return check_text(definition, value, paragraphs=definition.id == "description")
    if definition.type == "money":
        return check_money(definition, value)
    if definition.type == "decimal":
        return check_decimal(definition, value)
    if definition.type == "enum":
        return check_enum(definition, value, purpose)
    return None, "invalid"  # a type that has no editor yet


def evaluate(
    purpose: str,
    category: str,
    subtype: str,
    current: Mapping[str, object],
    updates: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, str]]:
    """Normalised values for every update (None clears) and a code per failing field.

    Cross-field: the marla basis against the resulting unit (`_check_marla_basis`).
    Every update is judged against the current registry: the value rules and whether the field
    applies to this Listing. Failing fields are all reported at once. Completeness (a rental
    priced per rental period naming its period, required fields) is a publication rule
    (Story 2.15): saving never requires complete data.
    """
    values: dict[str, object] = {}
    errors: dict[str, str] = {}
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
        else:
            value, code = _check_one(definition, raw, purpose)
            if code:
                errors[name] = code
            else:
                values[name] = value
    _check_marla_basis(current, values, errors)
    return values, errors


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
