"""Natural-language search (Stories 6.1 and 6.2). Rules live in `search`.

The request is in a POST body, never a URL. The answer is the interpretation only: the filter
parameters to give `GET /api/v1/public/listings`, which does the searching."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.kernel import ratelimit
from apps.presentation.api.actors import get_actor
from apps.presentation.api.authentication import enforce_csrf
from apps.presentation.api.bodies import field_errors, single_field
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import AllowPublic
from apps.presentation.api.serializers import ErrorBodySerializer
from apps.search import services


class SearchRequestSerializer(serializers.Serializer):
    query = serializers.CharField(
        help_text=f"The request in the buyer's words, at most {services.MAX_QUERY} characters."
    )


class SearchInterpretationSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=["interpreted", "fallback"],
        help_text="`fallback`: nothing was understood; offer the conventional filters.",
    )
    message = serializers.CharField(allow_blank=True, help_text="Why, on a fallback.")
    params = serializers.DictField(
        child=serializers.CharField(),
        help_text=(
            "Query parameters for `GET /api/v1/public/listings`; a `prefer_` one only ranks. "
            "Empty on a fallback."
        ),
    )
    unsupported = serializers.ListField(
        child=serializers.CharField(),
        help_text="Parts of the request that were not searched for, in the buyer's words.",
    )
    notices = serializers.ListField(child=serializers.CharField())


def _query(data) -> str:
    value = single_field(data, "query")
    if not isinstance(value, str):
        raise field_errors({"query": "invalid"})
    value = value.strip()
    if not value:
        raise field_errors({"query": "blank"})
    if len(value) > services.MAX_QUERY:
        raise field_errors({"query": "too_long"})
    return value


class PublicSearchView(NoStoreMixin, APIView):
    permission_classes = [AllowPublic]

    @extend_schema(
        operation_id="interpret_search",
        summary="Turn a search written in plain words into filters",
        description=(
            "Public; needs `X-CSRFToken`. One AI call on redacted text; the request is not "
            "stored. `200` with `status: fallback` when the AI is off, unavailable, too slow, "
            "out of budget or gave nothing usable. Limits: 10 a minute per Visitor, 30 an hour "
            "per account (`429` with `Retry-After`). Never cached."
        ),
        request=SearchRequestSerializer,
        responses={
            200: OpenApiResponse(SearchInterpretationSerializer, description="The filters"),
            400: OpenApiResponse(ErrorBodySerializer, description="`validation_error`"),
            403: OpenApiResponse(ErrorBodySerializer, description="`forbidden`: CSRF token"),
            429: OpenApiResponse(ErrorBodySerializer, description="`throttled`"),
        },
    )
    def post(self, request):
        enforce_csrf(request)
        actor = get_actor(request)
        query = _query(request.data)
        visitor_key = None
        if actor.account_id is None:
            visitor_key = ratelimit.client_key(actor, request, "client")
        result = services.interpret_query(actor, query, visitor_key=visitor_key)
        read = result.read
        return Response(
            SearchInterpretationSerializer(
                {
                    "status": "interpreted" if result.interpreted else "fallback",
                    "message": result.message,
                    "params": read.params if result.interpreted else {},
                    "unsupported": list(read.unsupported),
                    "notices": list(read.notices) if result.interpreted else [],
                }
            ).data
        )
