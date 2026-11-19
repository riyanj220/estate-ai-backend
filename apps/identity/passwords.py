"""Password policy for registration. Pure: no request, model or session state.

Returns a stable error code or None; never echoes the password.
"""

from functools import cache

from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import CommonPasswordValidator
from django.core.exceptions import ValidationError

PASSWORD_MIN = 8
PASSWORD_MAX = 1024

_common = CommonPasswordValidator()


def _is_common(password: str) -> bool:
    try:
        _common.validate(password)
    except ValidationError:
        return True
    return False


def password_error(password, email: str = "") -> str | None:
    """`required`, `password_too_short`, `password_too_long`, `password_matches_email`,
    `password_too_common`, or None when the password is acceptable."""
    if password is None or password == "":
        return "required"
    if not isinstance(password, str):
        return "invalid"
    if not password.strip():
        return "required"
    if len(password) > PASSWORD_MAX:
        return "password_too_long"
    if len(password) < PASSWORD_MIN:
        return "password_too_short"
    lowered = password.lower()
    address = email.strip().lower() if isinstance(email, str) else ""
    if address and lowered in (address, address.partition("@")[0]):
        return "password_matches_email"
    if _is_common(password):
        return "password_too_common"
    return None


@cache
def dummy_hash() -> str:
    """A hash in the current preferred format; unknown emails are checked against it so they cost
    the same as known ones. Warmed at start-up (`IdentityConfig.ready`), not on a first request."""
    return make_password("dummy-password-for-timing-only")
