"""Error code catalogue. Pure Python: the API layer turns these into responses."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorCode:
    code: str
    status: int
    message: str


def _catalogue(*entries: ErrorCode) -> dict[str, ErrorCode]:
    return {entry.code: entry for entry in entries}


# Adding a code changes the published schema, which the freshness test enforces.
CATALOGUE: dict[str, ErrorCode] = _catalogue(
    ErrorCode("bad_request", 400, "The request could not be understood."),
    ErrorCode("validation_error", 400, "The request contains invalid values."),
    ErrorCode("link_invalid", 400, "This link is not valid. Request a new one."),
    ErrorCode("unauthenticated", 401, "Authentication is required."),
    ErrorCode("invalid_credentials", 401, "The email or password is incorrect."),
    ErrorCode("forbidden", 403, "You do not have permission to perform this action."),
    ErrorCode("not_found", 404, "The requested resource was not found."),
    ErrorCode("method_not_allowed", 405, "This method is not allowed for this resource."),
    ErrorCode("not_acceptable", 406, "The requested response format is not available."),
    ErrorCode("stale_revision", 409, "The resource changed since it was loaded."),
    ErrorCode("link_used", 409, "This link has already been used or replaced by a newer one."),
    ErrorCode("link_expired", 410, "This link has expired. Request a new one."),
    ErrorCode("unsupported_media_type", 415, "The request content type is not supported."),
    ErrorCode(
        "idempotency_key_reused", 422, "This idempotency key was used for a different request."
    ),
    ErrorCode("throttled", 429, "Too many requests. Try again later."),
    ErrorCode("server_error", 500, "Something went wrong on our side."),
)

# Static field messages keyed by DRF validation error code. Default DRF messages can
# echo submitted values (for example invalid choices), so they are never used.
FIELD_MESSAGES: dict[str, str] = {
    "required": "This field is required.",
    "null": "This field may not be null.",
    "blank": "This field may not be blank.",
    "empty": "This field may not be empty.",
    "invalid": "Enter a valid value.",
    "invalid_choice": "Select a valid choice.",
    "max_length": "This value is too long.",
    "min_length": "This value is too short.",
    "max_value": "This value is too large.",
    "min_value": "This value is too small.",
    "max_string_length": "This value is too long.",
    "max_digits": "This number has too many digits.",
    "max_decimal_places": "This number has too many decimal places.",
    "max_whole_digits": "This number has too many digits.",
    "not_a_list": "Expected a list of items.",
    "not_a_dict": "Expected an object.",
    "unique": "This value is already in use.",
    "date": "Enter a valid date.",
    "datetime": "Enter a valid date and time.",
    "does_not_exist": "The referenced item does not exist.",
    "unknown_field": "The request contains fields that are not accepted.",
    "invalid_name": "Enter a name of 2 to 80 letters, without phone numbers or email addresses.",
    "invalid_email": "Enter a valid email address.",
    "password_too_short": "Use at least 8 characters.",
    "password_too_long": "Use at most 1024 characters.",
    "password_too_common": "This password is too common. Choose a less guessable one.",
    "password_matches_email": "The password must not be the same as the email address.",
    "incorrect": "The current password is incorrect.",
    "password_same_as_current": "Choose a password different from your current one.",
    "invalid_phone": "Enter a Pakistani mobile number or a valid international number.",
}
DEFAULT_FIELD_MESSAGE = "Enter a valid value."


def error_codes() -> list[str]:
    return list(CATALOGUE)


def get_error(code: str) -> ErrorCode:
    return CATALOGUE[code]


def field_message(code: str | None) -> str:
    return FIELD_MESSAGES.get(code or "", DEFAULT_FIELD_MESSAGE)


class DomainError(Exception):
    """Raised by services for a catalogued failure; carries no message or values."""

    def __init__(self, code: str) -> None:
        if code not in CATALOGUE:
            raise KeyError(code)
        super().__init__(code)
        self.code = code
