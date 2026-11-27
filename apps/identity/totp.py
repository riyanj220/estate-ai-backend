"""Time-based one-time codes (RFC 6238: HMAC-SHA1, 6 digits, 30 seconds) and the encryption of
their secrets at rest (Story 10.10). No database access.

The encryption key is derived from `SECRET_KEY`, so there is no extra setting to manage. Changing
`SECRET_KEY` makes stored secrets unreadable (codes are then refused); the recovery is
`provision_admin reset-second-factor`.
"""

import base64
import hashlib
import hmac
import secrets
from datetime import datetime
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

STEP_SECONDS = 30
DIGITS = 6
SKEW_STEPS = 1
SECRET_BYTES = 20  # 160 bits, the RFC 4226 recommendation
ISSUER = "EstateAI"


def _cipher() -> Fernet:
    key = hmac.new(
        settings.SECRET_KEY.encode(), b"estate-ai/second-factor/v1", hashlib.sha256
    ).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def new_secret() -> str:
    """A new secret in base32, the form authenticator applications accept."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode().rstrip("=")


def encrypt(secret: str) -> str:
    return _cipher().encrypt(secret.encode()).decode()


def decrypt(stored: str) -> str | None:
    """None when it cannot be read (another key, or a damaged value): fail closed."""
    try:
        return _cipher().decrypt(stored.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def otpauth_uri(secret: str, account_name: str) -> str:
    label = quote(f"{ISSUER}:{account_name}", safe=":@")
    query = urlencode(
        {"secret": secret, "issuer": ISSUER, "digits": DIGITS, "period": STEP_SECONDS}
    )
    return f"otpauth://totp/{label}?{query}"


def step_at(moment: datetime) -> int:
    return int(moment.timestamp()) // STEP_SECONDS


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, step.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def matching_step(secret: str, code, moment: datetime, after: int | None) -> int | None:
    """The step within one of now whose code this is and that is later than `after` (the last one
    used, so a code never works twice), else None."""
    if not isinstance(code, str):
        return None
    code = code.replace(" ", "")
    if len(code) != DIGITS or not (code.isascii() and code.isdigit()):
        return None
    now = step_at(moment)
    for step in range(now - SKEW_STEPS, now + SKEW_STEPS + 1):
        if hmac.compare_digest(code_at(secret, step), code) and (after is None or step > after):
            return step
    return None
