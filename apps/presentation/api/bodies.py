"""Request-body helpers shared by the API views. Codes only; submitted values are never echoed."""

from collections.abc import Mapping

from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail

from apps.kernel.errors import DomainError


def field_errors(fields: Mapping[str, str]) -> serializers.ValidationError:
    """Field errors keyed by field name, carrying only stable codes (never submitted values)."""
    return serializers.ValidationError(
        {field: [ErrorDetail(code, code=code)] for field, code in fields.items()}
    )


def exact_fields(data, names: tuple[str, ...]) -> dict:
    """The allowlisted fields present in a body; an unknown field or a non-object is refused.
    Presence and types are checked by the caller."""
    if not isinstance(data, Mapping):
        raise serializers.ValidationError(
            {"non_field_errors": [ErrorDetail("not_a_dict", code="not_a_dict")]}
        )
    if set(data) - set(names):
        raise serializers.ValidationError(
            {"non_field_errors": [ErrorDetail("unknown_field", code="unknown_field")]}
        )
    return {name: data[name] for name in names if name in data}


def single_field(data, name: str):
    """The value of the only accepted field; any other body shape is a validation error."""
    values = exact_fields(data, (name,))
    if name not in values:
        raise serializers.ValidationError({name: [ErrorDetail("required", code="required")]})
    return values[name]


class ConflictWithCurrent(DomainError):
    """`409 stale_revision` whose body also carries the current resource under `current`.

    Raised by a view with the already serialised resource; the shared exception handler adds
    it to the standard envelope, so request id, logging and headers behave as for any error.
    """

    def __init__(self, current: dict) -> None:
        super().__init__("stale_revision")
        self.extra = {"current": current}


class ConflictWithDetails(DomainError):
    """A conflict whose body also carries a small `details` object of the caller's own numbers
    (for example the publication allowance); never values the caller sent."""

    def __init__(self, code: str, details: dict) -> None:
        super().__init__(code)
        self.extra = {"details": details}
