"""Request body and base for administrator reads of private data (AD-9).

A private read is a POST so the per-read reason travels in the body, never in a URL or log.
The view passes `reason` to its selector, whose `admin_read_*` function records it first.
"""

from rest_framework import serializers

from apps.kernel.privacy.reads import REASON_MAX, REASON_MIN


class _StrictReasonField(serializers.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class ReasonedReadSerializer(serializers.Serializer):
    reason = _StrictReasonField(min_length=REASON_MIN, max_length=REASON_MAX, trim_whitespace=True)


class ReasonedReadMixin:
    """Mix into an APIView: only POST (and OPTIONS) are accepted."""

    http_method_names = ["post", "options"]
    reason_serializer_class = ReasonedReadSerializer

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.http_method_names != ["post", "options"]:
            raise TypeError("a private read view must be POST only: list the mixin before APIView")

    def read_reason(self, request) -> str:
        serializer = self.reason_serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data["reason"]
