"""AI Price Estimate requests (Story 4.3) and the estimate of a published Listing (Stories 4.5 to
4.7). Rules live in `valuation`.

Both request routes share the `estimates` throttle (20 per hour per account); a Visitor is refused
by the permission class before anything is computed. The read of a published Listing's estimate is
public and composes the Listing (`listings`) with its value (`valuation`)."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.listings import selectors as listings
from apps.listings.services import FieldsInvalid
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import exact_fields, field_errors
from apps.presentation.api.listings import CATEGORIES, PURPOSES, SUBTYPES, _field_serializer
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import AllowPublic, RequireSignedIn
from apps.presentation.api.serializers import ErrorBodySerializer
from apps.presentation.api.throttling import PolicyThrottle
from apps.valuation import comparison, selectors, services, supportability

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


class PriceComparisonSerializer(serializers.Serializer):
    verdict = serializers.ChoiceField(
        choices=list(comparison.VERDICTS),
        help_text="The Asking price against the estimate; `about_equal` is within 5%.",
    )
    percent = serializers.CharField(help_text="Percent above or below the estimate, one decimal.")


class PriceForecastSerializer(serializers.Serializer):
    """The 5-Year Price Forecast: assumed growth scenarios, not a guaranteed return."""

    horizon_years = serializers.IntegerField()
    low = serializers.CharField(help_text="PKR, a decimal string.")
    base = serializers.CharField(help_text="PKR, a decimal string.")
    high = serializers.CharField(help_text="PKR, a decimal string.")


class PriceDriverSerializer(serializers.Serializer):
    """One detail of the Listing and what it does to the model's estimate, against a typical one."""

    factor = serializers.CharField(
        help_text="`locality`, `bedrooms`, `bathrooms`, `covered_area`, `building_age`, "
        "`condition`, `corner_plot`, `road_width`, `development` or `amenities`."
    )
    percent = serializers.CharField(
        help_text="One decimal; positive raises the estimate. Effects do not add up to a total."
    )


class ListingEstimateSerializer(EstimateSerializer):
    """A published Listing's estimate beside its total Asking price."""

    asking_total = serializers.CharField(
        allow_null=True,
        help_text="Total Asking price in PKR (a per-unit price times the area); null for rent.",
    )
    comparison = PriceComparisonSerializer(allow_null=True, help_text="Null when unavailable.")
    forecast = PriceForecastSerializer(allow_null=True, help_text="Null when unavailable.")
    drivers = PriceDriverSerializer(
        many=True, help_text="What moves the estimate, largest first; empty when unavailable."
    )
    can_recompute = serializers.BooleanField(
        help_text="True for the signed-in owner: `POST /api/v1/listings/{id}/estimate`."
    )


def _values(outcome: selectors.EstimateOutcome) -> dict:
    available = outcome.reason is None
    return {
        "status": "available" if available else "unavailable",
        "estimated_value": f"{outcome.value:.2f}" if available else None,
        "currency": "PKR",
        "model_version": outcome.model_version,
        "estimated_at": outcome.estimated_at,
        "reason": outcome.reason,
        "message": None if available else supportability.MESSAGES[outcome.reason],
    }


def _data(outcome: selectors.EstimateOutcome) -> dict:
    return EstimateSerializer(_values(outcome)).data


def _responses(*statuses: int) -> dict:
    meaning = {
        400: "`validation_error`: invalid or unaccepted values",
        401: "`unauthenticated`: a Visitor",
        403: "`forbidden`: not allowed for this account, or a missing CSRF token",
        404: "`not_found`: not the account's own Listing",
        409: "`conflict`: the Listing is not a Draft or Published",
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
        summary="AI Price Estimate for one of the account's own Drafts or Published Listings",
        description=(
            "Needs `X-CSRFToken`. No body. Uses the saved Listing; for a Published one this is "
            "the owner's recompute. The Asking price is neither read nor changed. Never cached."
        ),
        request=None,
        responses=_responses(401, 403, 404, 409, 429),
    )
    def post(self, request, listing_id):
        return Response(_data(services.estimate_for_listing(get_actor(request), listing_id)))


class PublicListingEstimateView(NoStoreMixin, APIView):
    permission_classes = [AllowPublic]

    @extend_schema(
        operation_id="get_listing_estimate",
        summary="Estimated Property Value and forecast of one published Listing",
        description=(
            "Public. The stored estimate of the Listing's current details with the comparison "
            "and 5-year forecast. A Visitor only reads what is stored; for a signed-in account a "
            "missing or outdated estimate is computed first. A Listing that is not publicly "
            "visible, an unknown and a malformed id are the same `404 not_found`. Never cached."
        ),
        responses={
            200: OpenApiResponse(ListingEstimateSerializer, description="The estimate"),
            404: OpenApiResponse(ErrorBodySerializer, description="`not_found`"),
        },
    )
    def get(self, request, listing_id):
        actor = get_actor(request)
        view = listings.public_listing(actor, listing_id)
        outcome = selectors.current_estimate(actor, view.id)
        asking = comparison.total_asking_price(
            view.fields.get("asking_price"),
            view.fields.get("price_basis"),
            view.fields.get("area_value"),
        )
        available = outcome.reason is None
        verdict = comparison.compare(asking, outcome.value) if available and asking else None
        return Response(
            ListingEstimateSerializer(
                {
                    **_values(outcome),
                    "asking_total": None if asking is None else f"{asking:.2f}",
                    "comparison": verdict
                    and {"verdict": verdict.verdict, "percent": f"{verdict.percent:.1f}"},
                    "forecast": outcome.forecast if available else None,
                    "drivers": (outcome.drivers or []) if available else [],
                    "can_recompute": selectors.can_recompute(actor, view.id),
                }
            ).data
        )
