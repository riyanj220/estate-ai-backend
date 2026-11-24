"""Spectacular post-processing: publish the error catalogue and default error responses."""

from drf_spectacular.views import SpectacularAPIView

from apps.kernel.errors import CATALOGUE
from apps.presentation.api.permissions import AllowPublic


class PublicSchemaView(SpectacularAPIView):
    permission_classes = [AllowPublic]


ENVELOPE_REF = {"$ref": "#/components/schemas/ErrorEnvelope"}
DEFAULT_ERROR_STATUSES = (400, 401, 403, 404, 429, 500)
ERROR_DESCRIPTIONS = {
    400: "Bad request or validation error",
    401: "Authentication required",
    403: "Forbidden",
    404: "Not found",
    409: "Conflict",
    429: "Too many requests",
    500: "Server error",
    503: "Busy: retry shortly",
}
# Declared by an operation with `extend_schema(responses={409: OpenApiResponse(description=...)})`;
# the envelope content (and the `Retry-After` of a 503) is filled in here.
DECLARED_ERROR_STATUSES = (409, 503)


def _error_components() -> dict:
    return {
        "ErrorCode": {
            "type": "string",
            "enum": list(CATALOGUE),
            "description": "Stable error code catalogue.",
        },
        "ErrorBody": {
            "type": "object",
            "properties": {
                "code": {"$ref": "#/components/schemas/ErrorCode"},
                "message": {"type": "string"},
                "fields": {
                    "type": "object",
                    "additionalProperties": {"type": "array", "items": {"type": "string"}},
                },
                "request_id": {"type": "string"},
            },
            "required": ["code", "message", "fields", "request_id"],
        },
        "ErrorEnvelope": {
            "type": "object",
            "properties": {"error": {"$ref": "#/components/schemas/ErrorBody"}},
            "required": ["error"],
        },
    }


def _error_response(status: int) -> dict:
    response: dict = {
        "description": ERROR_DESCRIPTIONS[status],
        "content": {"application/json": {"schema": ENVELOPE_REF}},
    }
    if status in (429, 503):
        response["headers"] = {
            "Retry-After": {
                "description": "Seconds to wait before retrying, when known.",
                "schema": {"type": "integer"},
            }
        }
    return response


def add_error_contract(result, generator, request, public):
    schemas = result.setdefault("components", {}).setdefault("schemas", {})
    schemas.update(_error_components())
    for methods in result.get("paths", {}).values():
        for operation in methods.values():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            for status in DECLARED_ERROR_STATUSES:
                declared = operation["responses"].get(str(status))
                if declared is not None and "content" not in declared:
                    filled = _error_response(status)
                    filled["description"] = declared.get("description") or filled["description"]
                    operation["responses"][str(status)] = filled
            for status in DEFAULT_ERROR_STATUSES:
                operation["responses"].setdefault(str(status), _error_response(status))
    return result
