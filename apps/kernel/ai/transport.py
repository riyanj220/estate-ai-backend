"""JSON over HTTPS to an allowlisted provider host (AD-8).

`http.client` follows no redirect and reads no proxy variable. Failures raise `ProviderError`,
which carries a category and a status code only: never the URL, a header, a body or the original
exception (it is raised outside the `except` block, so nothing is chained to it).
"""

import http.client
import json
from urllib.parse import urlsplit

from apps.kernel.ai.egress import check_url

MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_CATEGORIES = {400: "bad_request", 401: "auth", 403: "auth", 404: "not_found", 429: "quota"}


class ProviderError(Exception):
    def __init__(self, category: str, status: int | None = None) -> None:
        super().__init__(f"provider error: {category}" + (f" ({status})" if status else ""))
        self.category = category
        self.status = status


def _connect(host: str, timeout: float) -> http.client.HTTPSConnection:
    return http.client.HTTPSConnection(host, timeout=timeout)


def request_json(url: str, headers: dict, body: dict | None = None, *, timeout: float) -> dict:
    """POST `body` as JSON, or GET when it is None, and return the JSON object answered."""
    parts = urlsplit(check_url(url))
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    payload = None if body is None else json.dumps(body).encode()
    status, raw, failure = 0, b"", None
    try:
        connection = _connect(parts.hostname, timeout)
        try:
            connection.request(
                "GET" if payload is None else "POST",
                path,
                body=payload,
                headers={**headers, "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            status = response.status
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        finally:
            connection.close()
    except TimeoutError:
        failure = "timeout"
    except Exception:  # its text may quote a header value
        failure = "network"
    if failure:
        raise ProviderError(failure)
    if status != 200:
        category = _CATEGORIES.get(status, "server" if status >= 500 else "http")
        raise ProviderError(category, status)
    try:
        data = json.loads(raw) if len(raw) <= MAX_RESPONSE_BYTES else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise ProviderError("invalid_response", status)
    return data
