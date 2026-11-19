"""Absolute links are built from FRONTEND_BASE_URL, never from the Host header."""

import re
from urllib.parse import unquote

from django.conf import settings

_ENCODED_SLASH = re.compile(r"%(2f|5c)", re.IGNORECASE)


def frontend_url(path: str) -> str:
    if (
        not path.startswith("/")
        or path.startswith("//")
        or any(ch in path for ch in "\\?# ")
        or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path)
        or _ENCODED_SLASH.search(path)
        or ".." in unquote(path).split("/")
    ):
        raise ValueError("path must be a plain site-relative path starting with a single '/'")
    return settings.FRONTEND_BASE_URL.rstrip("/") + path
