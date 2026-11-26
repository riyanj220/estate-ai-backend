"""AI Price Estimate requests (Story 4.3). Rules live in `valuation`.

Both routes share the `estimates` throttle (20 per hour per account); a Visitor is refused by the
permission class before anything is computed."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.listings.services import FieldsInvalid
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import exact_fields, field_errors
from apps.presentation.api.listings import CATEGORIES, PURPOSES, SUBTYPES, _field_serializer
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireSignedIn
from apps.presentation.api.serializers import ErrorBodySerializer
from apps.presentation.api.throttling import PolicyThrottle
from apps.valuation import services, supportability

BODY = ("purpose", "category", "subtype", "fields")

EstimateFieldsSerializer = _field_serializer(
    "EstimateFieldsSerializer",
    services.ATTRIBUTE_FIELDS,
    True,
    "The model inputs only. Send the ones that apply to the property type; no price, address or "
    "other field is accepted.",
)


class EstimateRequestSerializer(serializers.Serializer):
    purpose = serializers.ChoiceField(choices=PURPOSES)
    category = serializers.ChoiceField(choices=CATEGORIES)
    subtype = serializers.ChoiceField(choices=SUBTYPES)
    fields = EstimateFieldsSerializer()

    def to_internal_value(self, data):
        # Only these keys are accepted; the service judges every value.
        values = exact_fields(data, BODY)
        return {name: values.get(name) for name in BODY}


class EstimateSerializer(serializers.Serializer):
    """An AI Price Estimate, or the unavailable state with a short reason and no number."""

    status = serializers.ChoiceField(choices=["available", "unavailable"])
    estimated_value = serializers.CharField(
        allow_null=True,
        help_text="Estimated Property Value in PKR, a decimal string; null when unavailable.",
    )
    currency = serializers.CharField(help_text="Always `PKR`.")
    model_version = serializers.IntegerField(allow_null=True)
    estimated_at = serializers.DateTimeField(allow_null=True)
    reason = serializers.ChoiceField(choices=list(supportability.MESSAGES), allow_null=True)
    message = serializers.CharField(
        allow_null=True, help_text="Short text for the reason; null when available."
    )


def _data(outcome: services.EstimateOutcome) -> dict:
    available = outcome.reason is None
    return EstimateSerializer(
        {
            "status": "available" if available else "unavailable",
            "estimated_value": f"{outcome.value:.2f}" if available else None,
            "currency": "PKR",
            "model_version": outcome.model_version,
            "estimated_at": outcome.estimated_at,
            "reason": outcome.reason,
            "message": None if available else supportability.MESSAGES[outcome.reason],
        }
    ).data


def _responses(*statuses: int) -> dict:
    meaning = {
        400: "`validation_error`: invalid or unaccepted values",
        401: "`unauthenticated`: a Visitor",
        403: "`forbidden`: not allowed for this account, or a missing CSRF token",
        404: "`not_found`: not the account's own Listing",
        409: "`conflict`: the Listing is not a Draft",
        429: "`throttled`: more than 20 estimate requests in an hour; with `Retry-After`",
    }
    return {
        200: OpenApiResponse(EstimateSerializer, description="The estimate, or why there is none"),
        **{code: OpenApiResponse(ErrorBodySerializer, meaning[code]) for code in statuses},
    }


class _EstimateView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]
    throttle_classes = [PolicyThrottle]
    rate_limit_scope = "estimates"


class EstimateView(_EstimateView):
    @extend_schema(
        operation_id="request_estimate",
        summary="AI Price Estimate for entered property details",
        description=(
            "Needs `X-CSRFToken`. An unsupported property is `200` with `status: unavailable`. "
            "Shares the limit of 20 requests an hour with the Draft route. Never cached."
        ),
        request=EstimateRequestSerializer,
        responses=_responses(400, 401, 403, 429),
    )
    def post(self, request):
        body = EstimateRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        values = body.validated_data
        try:
            outcome = services.estimate_for_attributes(
                get_actor(request),
                values["purpose"],
                values["category"],
                values["subtype"],
                values["fields"],
            )
        except FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response(_data(outcome))


class ListingEstimateView(_EstimateView):
    @extend_schema(
        operation_id="request_listing_estimate",
        summary="AI Price Estimate for one of the signed-in account's own Drafts",
        description=(
            "Needs `X-CSRFToken`. No body. Uses the saved Draft. The Asking price is neither "
            "read nor changed. Never cached."
        ),
        request=None,
        responses=_responses(401, 403, 404, 409, 429),
    )
    def post(self, request, listing_id):
        return Response(_data(services.estimate_for_draft(get_actor(request), listing_id)))
