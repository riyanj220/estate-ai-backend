"""How stored files are named: THE one place that decides the folder layout of the store.

    users/<account id>/listings/<listing id>/<random name>    Listing photos and floor plans
    users/<account id>/profile/<random name>                  profile picture or agency logo
    <random name>                                             files stored before the folders

Folders hold ids only. A new kind of file gets its folder here.
"""

import re
import secrets
import uuid

KEY_BYTES = 32
# `secrets.token_urlsafe(32)` is 43 characters of the URL-safe base64 alphabet.
_NAME = r"[A-Za-z0-9_-]{43}"
_ID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
_KEY = re.compile(rf"(?:users/{_ID}/(?:listings/{_ID}|profile)/)?{_NAME}")


class InvalidKey(ValueError):
    """The key is not in a format `new_key` produces."""


def new_key(owner: uuid.UUID | None = None, listing: uuid.UUID | None = None) -> str:
    """A fresh random key. With an `owner` it is filed under that account: in the Listing's folder,
    or in `profile` without one."""
    name = secrets.token_urlsafe(KEY_BYTES)
    if owner is None:
        return name
    folder = f"listings/{listing}" if listing is not None else "profile"
    return f"users/{owner}/{folder}/{name}"


def is_valid_key(key: object) -> bool:
    return isinstance(key, str) and _KEY.fullmatch(key) is not None and key.isascii()
