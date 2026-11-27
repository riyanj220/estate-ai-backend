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
    ErrorCode("email_not_verified", 403, "Verify your email address to publish."),
    ErrorCode("publishing_unavailable", 403, "Your account cannot publish right now."),
    ErrorCode("not_found", 404, "The requested resource was not found."),
    ErrorCode("method_not_allowed", 405, "This method is not allowed for this resource."),
    ErrorCode("not_acceptable", 406, "The requested response format is not available."),
    ErrorCode("stale_revision", 409, "The resource changed since it was loaded."),
    ErrorCode("conflict", 409, "This action cannot be done in the current state."),
    ErrorCode("quota_exhausted", 409, "You have used all your publications for this month."),
    ErrorCode(
        "correction_unavailable",
        409,
        "The correction window is closed. Archive the listing to change these details.",
    ),
    ErrorCode("storage_cap_exceeded", 409, "Your storage limit is reached. Remove an image first."),
    ErrorCode("too_many_images", 409, "A listing can have at most 10 images."),
    ErrorCode("link_used", 409, "This link has already been used or replaced by a newer one."),
    ErrorCode("link_expired", 410, "This link has expired. Request a new one."),
    ErrorCode("unsupported_media_type", 415, "The request content type is not supported."),
    ErrorCode(
        "idempotency_key_reused", 422, "This idempotency key was used for a different request."
    ),
    ErrorCode("throttled", 429, "Too many requests. Try again later."),
    ErrorCode("server_error", 500, "Something went wrong on our side."),
    ErrorCode("busy", 503, "This feature is busy right now. Try again shortly."),
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
    "subtype_not_in_category": "This subtype does not belong to the selected category.",
    "unknown_field": "The request contains fields that are not accepted.",
    "unknown_parameter": "This parameter is not accepted.",
    "repeated_parameter": "Give this parameter only once.",
    "too_many_values": "Too many values.",
    "inverted_range": "The upper bound is below the lower bound.",
    "invalid_name": "Enter a valid name without phone numbers or email addresses.",
    "invalid_email": "Enter a valid email address.",
    "password_too_short": "Use at least 8 characters.",
    "password_too_long": "Use at most 1024 characters.",
    "admin_password_too_short": "Administrator passwords need at least 12 characters.",
    "admin_password_whitespace": ("Administrator passwords must not start or end with whitespace."),
    "admin_password_weak": "Administrator passwords need at least one letter and one digit.",
    "password_too_common": "This password is too common. Choose a less guessable one.",
    "password_matches_email": "The password must not be the same as the email address.",
    "incorrect": "The current password is incorrect.",
    "password_same_as_current": "Choose a password different from your current one.",
    "invalid_phone": "Enter a Pakistani mobile number or a valid international number.",
    "invalid_address": "Enter an office address of 5 to 300 characters, without line breaks.",
    "invalid_city": "Choose a city from the list.",
    "invalid_description": "Enter a description of up to 1000 characters, or leave it empty.",
    "contains_contact_details": (
        "This text cannot contain phone numbers, email addresses or street addresses."
    ),
    "not_editable": "This field cannot be changed here.",
    "not_applicable": "This field does not apply to this listing.",
    "too_short": "This text is too short.",
    "too_long": "This text is too long.",
    "invalid_price": "Enter an amount within the allowed range with at most two decimals.",
    "invalid_area": "Enter an area above zero with at most four decimals, using digits only.",
    "invalid_measure": (
        "Enter a number above zero within the allowed range, "
        "using digits and an optional decimal point."
    ),
    "invalid_date": "Enter a date within the allowed range.",
    "invalid_range": "Enter a whole number within the allowed range.",
    "too_large": "The image is too large. Use a file of up to 5 MB.",
    "unsupported_type": "Use a JPEG, PNG or WebP image.",
    "too_large_dimensions": (
        "The image has too many pixels. Use at most 10,000 pixels on a side and 40 megapixels."
    ),
    "too_small": "The image is too small. Use at least 200 by 200 pixels.",
    "invalid_image": "The file is not a usable image.",
    "invalid_floor": "The floor cannot be above the number of floors in the building.",
    "out_of_range": "Enter a number within the allowed range, using digits and a decimal point.",
    "locality_not_in_city": "Choose a locality that belongs to the selected city.",
    "images_required": "Add at least one image.",
    "no_change": "Nothing would change. Enter a different locality or area.",
    "confirm_required": "Confirm this value before publishing.",
    "manual_description": "You wrote this description. Choose to replace it.",
    "nothing_confirmed": "Fill in and confirm some details first.",
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
