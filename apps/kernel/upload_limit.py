"""Cap on the request body of the image upload routes (Story 2.9).

Only `POST /api/v1/listings/<id>/images` is limited here (6 MiB), and `PATCH` on the same route
(the small JSON reorder body) gets a much smaller cap, `MAX_IMAGE_ORDER_BODY_BYTES` (64 KiB); every
other route keeps Django's defaults. Two layers:

* `BodyLimitASGI` wraps the ASGI application and bounds buffering BEFORE Django reads anything:
  a declared `Content-Length` above the cap is refused without reading the body, and a body with no
  or a wrong length (chunked) is counted as it arrives and refused the moment it passes the cap.
  The refusal is the standard `validation_error` envelope (`file: too_large`) with status 400.
* `BodySizeMiddleware` (before the CSRF check, which would otherwise parse a multipart body when the
  token header is missing) flags a request whose length is absent, malformed or above the cap as
  `request.body_too_large` and zeroes the announced length, so nothing is parsed; the view answers
  `too_large`.

A reverse proxy should cap the body as well (see `docs/media.md`).
"""

import re
from collections.abc import Callable

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.conf import settings

ROUTE = re.compile(r"^/api/v1/listings/[^/]+/images/?$")


def is_upload(method: str, path: str) -> bool:
    return method in ("POST", "PATCH") and ROUTE.match(path) is not None


def cap_for(method: str) -> int:
    """The body cap of an image-route request: the upload cap, or the small JSON one for PATCH."""
    if method == "PATCH":
        return settings.MAX_IMAGE_ORDER_BODY_BYTES
    return settings.MAX_REQUEST_BODY_BYTES


def _announced_length(raw) -> int | None:
    if isinstance(raw, str) and raw.isascii() and raw.isdigit() and len(raw) <= 12:
        return int(raw)
    return None


def body_too_large(request) -> bool:
    return bool(getattr(request, "body_too_large", False))


class BodySizeMiddleware:
    async_capable = True
    sync_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        if iscoroutinefunction(get_response):
            markcoroutinefunction(self)

    def _limit(self, request) -> None:
        if not is_upload(request.method, request.path_info):
            return
        length = _announced_length(request.META.get("CONTENT_LENGTH"))
        if length is None or length > cap_for(request.method):
            request.body_too_large = True
            request.META["CONTENT_LENGTH"] = "0"

    def __call__(self, request):
        if iscoroutinefunction(self):
            return self.__acall__(request)
        self._limit(request)
        return self.get_response(request)

    async def __acall__(self, request):
        self._limit(request)
        return await self.get_response(request)


Reject = Callable[[], tuple[int, list[tuple[bytes, bytes]], bytes]]


class BodyLimitASGI:
    """ASGI wrapper: never lets more than the cap be buffered for an upload request."""

    def __init__(self, app, reject: Reject, reject_order: Reject | None = None) -> None:
        self.app = app
        self.reject = reject  # the upload refusal (`file: too_large`)
        self.reject_order = reject_order or reject  # the reorder refusal (PATCH)

    async def _refuse(self, send, method: str = "POST") -> None:
        status, headers, body = (self.reject_order if method == "PATCH" else self.reject)()
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not is_upload(scope["method"], scope["path"]):
            return await self.app(scope, receive, send)
        method = scope["method"]
        cap = cap_for(method)
        declared = None
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                declared = _announced_length(value.decode("latin-1"))
        if declared is not None and declared > cap:
            return await self._refuse(send, method)  # refused without reading a byte
        buffered, total = [], 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                break  # a disconnect: pass it on
            total += len(message.get("body", b""))
            if total > cap:
                return await self._refuse(send, method)
            if not message.get("more_body", False):
                break
        pending = iter(buffered)

        async def replay():
            for message in pending:
                return message
            return await receive()

        await self.app(scope, replay, send)
