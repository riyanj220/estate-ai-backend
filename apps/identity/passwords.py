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


ADMIN_PASSWORD_MIN = 12


def admin_password_error(password, email: str = "") -> str | None:
    """The strong policy for administrators: 12 to 1024 characters with a letter and a digit, not
    common, not the email address or its local part, no leading or trailing whitespace.

    Codes: `required`, `invalid`, `password_too_long`, `admin_password_too_short`,
    `admin_password_whitespace`, `password_matches_email`, `password_too_common`,
    `admin_password_weak`; None when acceptable.
    """
    if password is None or password == "":
        return "required"
    if not isinstance(password, str):
        return "invalid"
    if not password.strip():
        return "required"
    if len(password) > PASSWORD_MAX:
        return "password_too_long"
    if len(password) < ADMIN_PASSWORD_MIN:
        return "admin_password_too_short"
    if password != password.strip():
        return "admin_password_whitespace"
    address = email.strip().lower() if isinstance(email, str) else ""
    if address and password.lower() in (address, address.partition("@")[0]):
        return "password_matches_email"
    if _is_common(password):
        return "password_too_common"
    # ASCII only, so other scripts' letters and digits do not count towards the rule.
    if not any(c.isascii() and c.isalpha() for c in password) or not any(
        c.isascii() and c.isdigit() for c in password
    ):
        return "admin_password_weak"
    return None


def policy_error(account_type: str, password, email: str = "") -> str | None:
    """The password rule for an account type: the strong one for administrators."""
    if account_type == "admin":
        return admin_password_error(password, email)
    return password_error(password, email)


@cache
def dummy_hash() -> str:
    """A hash in the current preferred format; unknown emails are checked against it so they cost
    the same as known ones. Warmed at start-up (`IdentityConfig.ready`), not on a first request."""
    return make_password("dummy-password-for-timing-only")
