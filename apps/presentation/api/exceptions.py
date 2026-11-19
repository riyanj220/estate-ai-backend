"""DRF exception handler producing the single error envelope (AD-16, AD-26).

Messages come from the catalogue, never from exception text, so submitted
values cannot leak into an error body.
"""

import math

from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import Http404
from rest_framework import exceptions as drf
from rest_framework.response import Response
from rest_framework.views import set_rollback

from apps.kernel.errors import CATALOGUE, DomainError, field_message, get_error
from apps.presentation.api.serializers import ErrorEnvelopeSerializer

_BY_TYPE: tuple[tuple[type[Exception], str], ...] = (
    (drf.ValidationError, "validation_error"),
    (drf.ParseError, "bad_request"),
    (SuspiciousOperation, "bad_request"),
    (drf.NotAuthenticated, "unauthenticated"),
    (drf.AuthenticationFailed, "unauthenticated"),
    (drf.PermissionDenied, "forbidden"),
    (PermissionDenied, "forbidden"),
    (drf.NotFound, "not_found"),
    (Http404, "not_found"),
    (drf.MethodNotAllowed, "method_not_allowed"),
    (drf.NotAcceptable, "not_acceptable"),
    (drf.UnsupportedMediaType, "unsupported_media_type"),
    (drf.Throttled, "throttled"),
)


def code_for(exc: Exception) -> str:
    if isinstance(exc, DomainError):
        return exc.code
    for exc_type, code in _BY_TYPE:
        if isinstance(exc, exc_type):
            return code
    if isinstance(exc, drf.APIException):
        for entry in CATALOGUE.values():
            if entry.status == exc.status_code and entry.code != "validation_error":
                return entry.code
        return "bad_request" if exc.status_code < 500 else "server_error"
    return "server_error"


def _codes(detail) -> list[str | None]:
    if isinstance(detail, dict):
        return [c for value in detail.values() for c in _codes(value)]
    if isinstance(detail, list):
        return [c for item in detail for c in _codes(item)]
    if isinstance(detail, drf.ErrorDetail):
        return [detail.code]
    return []


def _messages(detail) -> list[str]:
    messages: list[str] = []
    for code in _codes(detail):
        text = field_message(code)
        if text not in messages:
            messages.append(text)
    return messages


def validation_fields(exc: drf.ValidationError) -> dict[str, list[str]]:
    """Key by the top-level field name only; nested keys and indices can be caller-controlled."""
    detail = exc.detail
    out: dict[str, list[str]] = {}
    if isinstance(detail, dict):
        for key, value in detail.items():
            if messages := _messages(value):
                out[str(key)] = messages
    elif messages := _messages(detail):
        out["non_field_errors"] = messages
    return out or {"non_field_errors": [get_error("validation_error").message]}


def _http_request(context):
    request = context.get("request") if context else None
    return getattr(request, "_request", request)


def build_envelope(code: str, request_id: str, fields: dict | None = None) -> dict:
    return ErrorEnvelopeSerializer(
        {
            "error": {
                "code": code,
                "message": get_error(code).message,
                "fields": fields or {},
                "request_id": request_id,
            }
        }
    ).data


def error_response(code: str, request, fields=None, headers=None) -> Response:
    request_id = getattr(request, "request_id", "")
    return Response(
        build_envelope(code, request_id, fields),
        status=get_error(code).status,
        headers=headers,
    )


def exception_handler(exc, context):
    code = code_for(exc)
    request = _http_request(context)
    if code == "server_error" and request is not None:
        request.exception_class = type(exc).__name__
    fields = validation_fields(exc) if isinstance(exc, drf.ValidationError) else None
    headers = {}
    if isinstance(exc, drf.Throttled) and exc.wait is not None:
        headers["Retry-After"] = str(math.ceil(exc.wait))
    set_rollback()
    return error_response(code, request, fields, headers)
