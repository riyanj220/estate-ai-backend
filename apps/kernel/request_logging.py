"""Request-ID middleware and allow-listed JSON logging (AD-26).

Logs never carry bodies, query strings, headers, cookies or exception messages:
the formatter emits only the fields named in `ALLOWED_FIELDS`.
"""

import json
import logging
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

from asgiref.sync import iscoroutinefunction, markcoroutinefunction

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_LOGGER = "estate.request"
HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"})

ALLOWED_FIELDS = (
    "event",
    "request_id",
    "method",
    "route",
    "status",
    "duration_ms",
    "exception_class",
    "handler",
)

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
logger = logging.getLogger(REQUEST_LOGGER)


def new_request_id() -> str:
    return str(uuid.uuid4())


def current_request_id() -> str | None:
    return _request_id.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        for field in ALLOWED_FIELDS:
            value = getattr(record, field, None)
            if isinstance(value, str | int | float) and not isinstance(value, bool):
                payload[field] = value
        if "exception_class" not in payload and record.exc_info and record.exc_info[0]:
            payload["exception_class"] = record.exc_info[0].__name__
        if "request_id" not in payload and (rid := current_request_id()):
            payload["request_id"] = rid
        return json.dumps(payload, separators=(",", ":"))


class RequestLoggingMiddleware:
    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.is_async = iscoroutinefunction(get_response)
        if self.is_async:
            markcoroutinefunction(self)

    def __call__(self, request):
        if self.is_async:
            return self._acall(request)
        started, token = self._start(request)
        try:
            response = self.get_response(request)
        finally:
            _request_id.reset(token)
        return self._finish(request, response, started)

    async def _acall(self, request):
        started, token = self._start(request)
        try:
            response = await self.get_response(request)
        finally:
            _request_id.reset(token)
        return self._finish(request, response, started)

    def process_exception(self, request, exception):
        request.exception_class = type(exception).__name__
        return None

    def _start(self, request):
        # Always generated here: a client-supplied ID is never trusted.
        request.request_id = new_request_id()
        return time.perf_counter(), _request_id.set(request.request_id)

    def _finish(self, request, response, started):
        response[REQUEST_ID_HEADER] = request.request_id
        match = getattr(request, "resolver_match", None)
        logger.log(
            logging.ERROR if response.status_code >= 500 else logging.INFO,
            "request",
            extra={
                "event": "request",
                "request_id": request.request_id,
                "method": request.method if request.method in HTTP_METHODS else "OTHER",
                "route": (match.route if match else None) or "unresolved",
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "exception_class": getattr(request, "exception_class", None),
            },
        )
        return response
