"""Spectacular post-processing: publish the error catalogue and default error responses."""

from apps.kernel.errors import CATALOGUE

ENVELOPE_REF = {"$ref": "#/components/schemas/ErrorEnvelope"}
DEFAULT_ERROR_STATUSES = (400, 401, 403, 404, 429, 500)
ERROR_DESCRIPTIONS = {
    400: "Bad request or validation error",
    401: "Authentication required",
    403: "Forbidden",
    404: "Not found",
    429: "Too many requests",
    500: "Server error",
}


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
    if status == 429:
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
            for status in DEFAULT_ERROR_STATUSES:
                operation["responses"].setdefault(str(status), _error_response(status))
    return result
