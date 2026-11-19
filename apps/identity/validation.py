"""Field rules for registration, as one pure set of validators.

Each returns the cleaned value and an error code (None when valid). Codes are stable
(`required`, `invalid_name`, `invalid_email`, `password_*`, `invalid_phone`; `invalid` for a value
of the wrong type) and the values themselves are never put in an error. No request or model state.
"""

import unicodedata
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
_JOINERS = frozenset("\u200c\u200d")  # ZWNJ and ZWJ, part of Urdu and Persian spelling


@dataclass(frozen=True)
class IndividualDetails:
    full_name: str
    email: str
    password: str
    phone: str  # E.164


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
