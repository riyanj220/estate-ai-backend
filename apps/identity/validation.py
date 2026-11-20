"""Field rules for registration, as one pure set of validators.

Each returns the cleaned value and an error code (None when valid). Codes are stable
(`required`, `invalid_name`, `invalid_email`, `password_*`, `invalid_phone`, `invalid_address`,
`invalid_city`; `invalid` for a value of the wrong type) and the values themselves are never put in
an error. No request or model state.
"""

import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from apps.identity.passwords import password_error
from apps.kernel.privacy.patterns import (
    EMAIL_ADDRESS_SHAPE,
    normalise_phone,
    reject_contact_details,
)

EMAIL_MAX = 254
NAME_MIN = 2
NAME_MAX = 80
_NAME_PUNCTUATION = frozenset(" -'’.")
AGENCY_NAME_MIN = 2
AGENCY_NAME_MAX = 100
_AGENCY_NAME_PUNCTUATION = frozenset(" &,.-'’()/")
DESCRIPTION_MAX = 1000
ADDRESS_MIN = 5
ADDRESS_MAX = 300
_JOINERS = frozenset("\u200c\u200d")  # ZWNJ and ZWJ, part of Urdu and Persian spelling


@dataclass(frozen=True)
class IndividualDetails:
    full_name: str
    email: str
    password: str
    phone: str  # E.164


@dataclass(frozen=True)
class AgencyDetails:
    agency_name: str
    email: str
    password: str
    phone: str  # E.164, private
    office_address: str  # private
    city_id: uuid.UUID


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def check_full_name(value) -> tuple[str, str | None]:
    if _blank(value):
        return "", "required"
    if not isinstance(value, str):
        return "", "invalid"
    name = " ".join(unicodedata.normalize("NFC", value).split())
    if name[:1] in _JOINERS or name[-1:] in _JOINERS:
        return "", "invalid_name"
    if not NAME_MIN <= len(name) <= NAME_MAX:
        return "", "invalid_name"
    for char in name:
        if (
            char not in _NAME_PUNCTUATION
            and char not in _JOINERS
            and not (char.isalpha() or unicodedata.category(char).startswith("M"))
        ):
            return "", "invalid_name"
    if not any(char.isalpha() for char in name) or reject_contact_details(name) is not None:
        return "", "invalid_name"
    return name, None


def check_email(value) -> tuple[str, str | None]:
    """Case is kept as entered; uniqueness is decided case-insensitively elsewhere."""
    if _blank(value):
        return "", "required"
    if not isinstance(value, str):
        return "", "invalid"
    email = value.strip()
    if (
        len(email) > EMAIL_MAX
        or not email.isprintable()
        or not EMAIL_ADDRESS_SHAPE.fullmatch(email)
    ):
        return "", "invalid_email"
    domain = email.rpartition("@")[2]
    if not domain.isascii() or "" in domain.split("."):  # mail needs an ASCII host, no empty label
        return "", "invalid_email"
    return email, None


def check_password(value, email: str = "") -> tuple[str, str | None]:
    code = password_error(value, email)
    return ("" if code else value), code


def check_phone(value) -> tuple[str, str | None]:
    if _blank(value):
        return "", "required"
    if not isinstance(value, str):
        return "", "invalid"
    phone = normalise_phone(value)
    return (phone, None) if phone else ("", "invalid_phone")


def validate_individual(
    full_name, email, password, phone
) -> tuple[IndividualDetails | None, dict[str, str]]:
    """Every failing field with its code, or the cleaned details."""
    name, name_error = check_full_name(full_name)
    address, email_error = check_email(email)
    secret, password_code = check_password(password, address or email)
    number, phone_error = check_phone(phone)
    errors = {
        field: code
        for field, code in (
            ("full_name", name_error),
            ("email", email_error),
            ("password", password_code),
            ("phone", phone_error),
        )
        if code
    }
    if errors:
        return None, errors
    return IndividualDetails(name, address, secret, number), {}


def _marks_follow_a_letter_or_mark(text: str) -> bool:
    """A combining mark must follow a letter or another mark (not punctuation or the start)."""
    previous = ""
    for char in text:
        if unicodedata.category(char).startswith("M") and not (
            previous and (previous.isalpha() or unicodedata.category(previous).startswith("M"))
        ):
            return False
        previous = char
    return True


def check_agency_name(value) -> tuple[str, str | None]:
    """The agency name is shown publicly, so it gets the same contact-detail check as a name."""
    if _blank(value):
        return "", "required"
    if not isinstance(value, str):
        return "", "invalid"
    name = " ".join(unicodedata.normalize("NFC", value).split())
    if name[:1] in _JOINERS or name[-1:] in _JOINERS:
        return "", "invalid_name"
    if not AGENCY_NAME_MIN <= len(name) <= AGENCY_NAME_MAX:
        return "", "invalid_name"
    for char in name:
        category = unicodedata.category(char)
        if (
            char not in _AGENCY_NAME_PUNCTUATION
            and char not in _JOINERS
            and not (char.isalpha() or category == "Nd" or category.startswith("M"))
        ):
            return "", "invalid_name"
    if not any(char.isalpha() for char in name) or not _marks_follow_a_letter_or_mark(name):
        return "", "invalid_name"
    if reject_contact_details(name) is not None:
        return "", "invalid_name"
    return name, None


def check_description(value) -> tuple[str, str | None]:
    """The optional public agency description: blank or null clears it to an empty string.

    Whitespace (paragraph breaks, NEL, VT and FF included) collapses to single spaces. Other
    control and invisible characters are refused before the collapse could hide them, and the
    shared public-text check rejects phone numbers, email addresses and street addresses.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return "", None
    if not isinstance(value, str):
        return "", "invalid_description"
    if any(
        unicodedata.category(char)[0] == "C" and char not in _JOINERS and not char.isspace()
        for char in value
    ):
        return "", "invalid_description"
    text = " ".join(unicodedata.normalize("NFC", value).split())
    if text[:1] in _JOINERS or text[-1:] in _JOINERS:
        return "", "invalid_description"
    # The cap comes first: the contact finder refuses input beyond its size limit.
    if len(text) > DESCRIPTION_MAX:
        return "", "invalid_description"
    if not any(char.isalpha() or unicodedata.category(char) == "Nd" for char in text):
        return "", "invalid_description"
    if not _marks_follow_a_letter_or_mark(text):
        return "", "invalid_description"
    if reject_contact_details(text) is not None:
        return "", "contains_contact_details"
    return text, None


def check_office_address(value) -> tuple[str, str | None]:
    """Private, so only sanity checks: digits and phone-looking runs are fine in an address."""
    if _blank(value):
        return "", "required"
    if not isinstance(value, str):
        return "", "invalid"
    # Control and format characters (NUL, newlines, tabs, invisibles) are refused before the
    # whitespace collapse could hide them. ZWNJ and ZWJ stay valid inside Urdu text.
    if any(unicodedata.category(char)[0] == "C" and char not in _JOINERS for char in value):
        return "", "invalid_address"
    address = " ".join(unicodedata.normalize("NFC", value).split())
    if address[:1] in _JOINERS or address[-1:] in _JOINERS:
        return "", "invalid_address"
    if not ADDRESS_MIN <= len(address) <= ADDRESS_MAX:
        return "", "invalid_address"
    if sum(1 for char in address if char.isalpha() or unicodedata.category(char) == "Nd") < 3:
        return "", "invalid_address"
    return address, None


def check_city_id(value) -> tuple[uuid.UUID | None, str | None]:
    """Shape only: a canonical hyphenated UUID. Whether the city exists is the caller's lookup."""
    if _blank(value):
        return None, "required"
    if isinstance(value, uuid.UUID):
        return value, None
    if not isinstance(value, str):
        return None, "invalid"
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return None, "invalid"
    return (parsed, None) if str(parsed) == value.lower() else (None, "invalid")


def validate_agency(
    agency_name,
    email,
    password,
    phone,
    office_address,
    city_id,
    city_is_active: Callable[[uuid.UUID], bool],
) -> tuple[AgencyDetails | None, dict[str, str]]:
    """Every failing field with its code, or the cleaned details.

    `city_is_active` is the caller's lookup (the validators stay free of database state).
    """
    name, name_error = check_agency_name(agency_name)
    address, email_error = check_email(email)
    secret, password_code = check_password(password, address or email)
    number, phone_error = check_phone(phone)
    office, office_error = check_office_address(office_address)
    city, city_error = check_city_id(city_id)
    cheap = (name_error, email_error, password_code, phone_error, office_error, city_error)
    if city is not None and not any(cheap) and not city_is_active(city):
        city_error = "invalid_city"  # the database lookup runs only once everything else passes
    errors = {
        field: code
        for field, code in (
            ("agency_name", name_error),
            ("email", email_error),
            ("password", password_code),
            ("phone", phone_error),
            ("office_address", office_error),
            ("city_id", city_error),
        )
        if code
    }
    if errors:
        return None, errors
    return AgencyDetails(name, address, secret, number, office, city), {}
