"""The single definition of phone, email and private-address patterns (AD-9).

Every contact-data regex in the repository lives here; a repository test fails if another
appears. Listing text, Agency profile text and AI redaction call `find_contact_details` or
`reject_contact_details` and nothing else. Matching runs on normalised text (NFKC, Unicode
digits as ASCII digits, zero-width characters dropped, case folded) and offsets are mapped
back to the original text. Every pattern is linear: unbounded quantifiers are never nested or
adjacent, and runs are anchored so a long input is scanned once.

Text longer than `MAX_TEXT_LENGTH` characters raises ValueError.

Never log matched text, offsets with text, or the input.
"""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

# Metadata (audit) has no legitimate long numbers, so it keeps a stricter rule than phones:
# an unseparated run of seven or more digits is refused there, but is not a phone by itself
# (prices and ids look the same).
LONG_DIGIT_RUN = re.compile(r"\d{7,}")

# One plain address, as accepted for mail recipients and account registration.
EMAIL_ADDRESS_SHAPE = re.compile(
    r"^[^\s@<>,;:\"()\[\]\\]+@[^\s@<>,;:\"()\[\]\\]+\.[^\s@<>,;:\"()\[\]\\]+$"
)

PHONE_MIN_DIGITS = 10  # unseparated digits
PHONE_MIN_GROUPED_DIGITS = 7  # digits (or digit words) joined by separators
MAX_TEXT_LENGTH = 100_000  # longer input raises ValueError
DIGIT_WORDS_MIN = 7


class ContactKind(StrEnum):
    PHONE = "phone"
    EMAIL = "email"
    ADDRESS = "address"


@dataclass(frozen=True)
class Finding:
    kind: ContactKind
    start: int  # offsets within the original text
    end: int


_EXPLANATION_NAMES = {
    ContactKind.PHONE: "a phone number",
    ContactKind.EMAIL: "an email address",
    ContactKind.ADDRESS: "a private address",
}

# --- phone -----------------------------------------------------------------------------------

_WORD = r"(?:zero|oh|o|one|two|three|four|five|six|seven|eight|nine)"
_ALNUM_EDGE = r"(?<![^\W_])", r"(?![^\W_])"  # word edges that treat "_" as a separator
_WORD_EDGED = rf"{_ALNUM_EDGE[0]}{_WORD}{_ALNUM_EDGE[1]}"
_ITEM = rf"(?:\d|{_WORD_EDGED})"
_ITEM_RE = re.compile(_ITEM)
# Separators (dashes are normalised to "-"); never the comma, which groups prices.
_SEP = r"[ \t\r\n\-./_()]"
_CANDIDATE = re.compile(rf"[+(]{{0,2}}{_ITEM}(?:{_SEP}{{0,3}}{_ITEM})*")
_WORDS_ONLY = re.compile(rf"\b{_WORD}\b(?:[ ,.\-/_]{{1,3}}\b{_WORD}\b){{{DIGIT_WORDS_MIN - 1},}}")
_DAY, _MONTH = r"(?:0?[1-9]|[12]\d|3[01])", r"(?:0?[1-9]|1[0-2])"
_DATE = re.compile(
    rf"(?<!\d)(?<!\d[-./])(?:\d{{4}}[-./]{_MONTH}[-./]{_DAY}|{_DAY}[-./]{_MONTH}[-./](?:\d{{4}}|\d{{2}}))"
    r"(?!\d)(?![-./]\d)"
)
_YEAR_RANGE = re.compile(r"(?:19|20)\d{2}[ ]?-[ ]?(?:19|20)?\d{2}")
_SPACED_PRICE = re.compile(r"\d{1,3}(?: \d{2,3})+")
_AFTER_CURRENCY = re.compile(r"(?:rs|pkr|usd|aed|inr|gbp|eur)\.?[ ]{0,2}$")
_UNIT_WORDS = (
    r"(?:sq|sqft|sqyd|square|marla|kanal|acres?|percent|km|kms|m|meters?|metres?|feet|ft|yards?|yd|"
    r"beds?|bedrooms?|bathrooms?|baths?|kitchens?|floors?|rooms?|storeys?|stories|bhk|"
    r"lakh|crore|years?|months?)"
)
_UNIT_AFTER = re.compile(rf"[ ]{{0,2}}(?:{_UNIT_WORDS}\b|%)")
_TRIM_LEFT = " \t\r\n-./_)"
_TRIM_RIGHT = " \t\r\n-./_(+"


def _phone_like(text: str, start: int, end: int) -> bool:
    segment = text[start:end]
    units = len(_ITEM_RE.findall(segment))
    separated = any(not ch.isdigit() for ch in segment.lstrip("+("))
    if units < (PHONE_MIN_GROUPED_DIGITS if separated else PHONE_MIN_DIGITS):
        return False
    if _YEAR_RANGE.fullmatch(segment):
        return False
    if _SPACED_PRICE.fullmatch(segment) and _AFTER_CURRENCY.search(text[max(0, start - 8) : start]):
        return False
    if segment[0] not in "+0" and _UNIT_AFTER.match(text, end):  # ranges and lists of measures
        return False
    return True


def _phone_spans(text: str):
    for match in _CANDIDATE.finditer(text):
        cursor = match.start()
        pieces = [(m.start(), m.end()) for m in _DATE.finditer(text, match.start(), match.end())]
        for cut_start, cut_end in [*pieces, (match.end(), match.end())]:
            start, end = cursor, cut_start
            cursor = cut_end
            segment = text[start:end]
            start += len(segment) - len(segment.lstrip(_TRIM_LEFT))
            end -= len(segment) - len(segment.rstrip(_TRIM_RIGHT))
            if start < end and _phone_like(text, start, end):
                yield start, end
    for match in _WORDS_ONLY.finditer(text):
        yield match.span()


# --- email -----------------------------------------------------------------------------------

_LOCAL = r"[\w.%+\-]+"
_LABEL = r"\w[\w\-]*"
_DOT_ANY = r"(?:\.|[ ]{0,3}[\[(]dot[\])][ ]{0,3}|[ ]{1,3}dot[ ]{1,3})"
_DOT_WORD = r"(?:[ ]{0,3}[\[(]dot[\])][ ]{0,3}|[ ]{1,3}dot[ ]{1,3})"
# A bare " at " is ordinary English ("it is at h.no 5"), so it only counts when the dots are
# spelled out as well. A bare "@handle" counts, "meet @ 5pm" does not.
_AT_ANY = r"(?:@|[ ]{0,3}[\[(]at[\])][ ]{0,3})"
_EMAILS = [
    re.compile(rf"(?<![\w.%+\-]){_LOCAL}{_AT_ANY}{_LABEL}(?:{_DOT_ANY}{_LABEL})*"),
    re.compile(rf"(?<![\w.%+\-]){_LOCAL}[ ]{{1,3}}at[ ]{{1,3}}{_LABEL}(?:{_DOT_WORD}{_LABEL})+"),
    re.compile(r"(?<![\w.%+\-])@\w+(?:[.\-]\w+)*"),
]

# --- private address -------------------------------------------------------------------------

_NUMBER = r"[ ]{0,3}(?:no\.?|number|\#)?[ ]{0,3}\d{1,5}(?:-?[a-z])?\b"
# A number followed by a measure or count ("house 10 marla", "flat 3 bedrooms") is not an address.
_NOT_MEASURE = rf"(?![ ]{{0,2}}(?:{_UNIT_WORDS}\b|%))"
_ADDRESS = [
    re.compile(
        r"\b(?:house|h\.?[ ]?no|h(?=#)|flat|apartment|apt|unit|bungalow|villa|building)\b\.?"
        rf"{_NUMBER}{_NOT_MEASURE}"
    ),
    re.compile(rf"\b(?:street|st|lane|gali)\b\.?{_NUMBER}{_NOT_MEASURE}"),
    re.compile(r"\b\d{1,3}(?:st|nd|rd|th)[ ]{1,3}(?:street|lane|gali)\b"),
    re.compile(
        rf"\b\d{{1,5}}[ ]{{1,3}}(?!{_UNIT_WORDS}\b)(?:[a-z]{{2,15}}[ ]{{1,3}})?"
        r"(?:street|lane|gali)\b"
    ),
    re.compile(r"(?<![\d.])-?\d{1,3}\.\d{3,}[ ]{0,2}[,;][ ]{0,2}-?\d{1,3}\.\d{3,}(?!\d)"),
]

_BLANKS = frozenset("ㅤᅟᅠﾠ⠀")


def _dropped(char: str) -> bool:
    return char in _BLANKS or unicodedata.category(char) in ("Cf", "Mn", "Me")


def _normalise(text: str) -> tuple[str, list[int]]:
    """Normalised text and, for each of its characters, the index in `text` it came from."""
    out: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        if _dropped(char):  # zero-width, format, combining and blank characters
            continue
        for piece in unicodedata.normalize("NFKC", char).casefold():
            if _dropped(piece):
                continue
            if piece.isdecimal():
                piece = str(unicodedata.decimal(piece))
            elif unicodedata.category(piece) == "Pd" or piece == "−":
                piece = "-"
            out.append(piece)
            origin.append(index)
    return "".join(out), origin


def find_contact_details(text: str) -> tuple[Finding, ...]:
    """Phone, email and private-address findings with offsets into `text`, in order.

    Offsets are Python code-point indices (not UTF-16 units). Raises TypeError for a
    non-string and ValueError for more than `MAX_TEXT_LENGTH` characters.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError("text is too long")
    if not text:
        return ()
    normal, origin = _normalise(text)
    spans: set[tuple[int, int, ContactKind]] = set()
    for start, end in _phone_spans(normal):
        spans.add((start, end, ContactKind.PHONE))
    for pattern in _EMAILS:
        for match in pattern.finditer(normal):
            spans.add((*match.span(), ContactKind.EMAIL))
    for pattern in _ADDRESS:
        for match in pattern.finditer(normal):
            spans.add((*match.span(), ContactKind.ADDRESS))
    findings = [
        Finding(kind, origin[start], origin[end - 1] + 1)
        for start, end, kind in sorted(spans, key=lambda s: (s[0], s[1], s[2].value))
    ]
    return tuple(findings)


def reject_contact_details(text: str) -> str | None:
    """None for clean text, else an explanation naming the kinds found, never the matches."""
    kinds = {finding.kind for finding in find_contact_details(text)}
    if not kinds:
        return None
    names = [_EXPLANATION_NAMES[kind] for kind in ContactKind if kind in kinds]
    if len(names) > 1:
        names = [", ".join(names[:-1]), names[-1]]
    return f"The text appears to contain {' and '.join(names)}."
