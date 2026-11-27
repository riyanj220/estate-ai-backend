"""AI and ML oversight (Story 10.6). Counts and states only: no conversation content, prompt or
account id. AI-content Reports are the Report queue filtered by `ai_generated_content`."""

from datetime import timedelta

from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response

from apps.kernel import ai_log
from apps.kernel.ai import oversight
from apps.presentation.adminapi.params import REFUSALS, AdminView
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import field_errors, single_field
from apps.valuation import selectors as valuation

WINDOW_DAYS = 7


class AiSwitchSerializer(serializers.Serializer):
    name = serializers.CharField()
    label = serializers.CharField()
    enabled = serializers.BooleanField()


class AiCallCountsSerializer(serializers.Serializer):
    feature = serializers.CharField()
    total = serializers.IntegerField()
    failed = serializers.IntegerField()
    outcomes = serializers.DictField(child=serializers.IntegerField())


class ValuationModelSerializer(serializers.Serializer):
    name = serializers.CharField()
    version = serializers.IntegerField()
    trained_at = serializers.DateTimeField()


class FailedValuationSerializer(serializers.Serializer):
    created_at = serializers.DateTimeField()
    model = serializers.CharField()
    outcome = serializers.CharField()
    latency_ms = serializers.IntegerField()


class AiOversightSerializer(serializers.Serializer):
    switches = AiSwitchSerializer(many=True)
    window_days = serializers.IntegerField()
    calls = AiCallCountsSerializer(many=True)
    valuation_model = ValuationModelSerializer(allow_null=True)
    failed_valuations = FailedValuationSerializer(many=True, help_text="Newest 20.")


class AiSwitchChangeSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()


class AiOversightView(AdminView):
    @extend_schema(
        operation_id="get_admin_ai_oversight",
        summary="AI feature switches, failures and the valuation model",
        description=f"Counts cover the last {WINDOW_DAYS} days. " + REFUSALS,
        responses={200: OpenApiResponse(AiOversightSerializer, description="Oversight")},
    )
    def get(self, request):
        switches = oversight.states(get_actor(request))
        since = timezone.now() - timedelta(days=WINDOW_DAYS)
        calls = [
            {
                "feature": feature,
                "total": sum(outcomes.values()),
                "failed": sum(n for name, n in outcomes.items() if name != "success"),
                "outcomes": outcomes,
            }
            for feature, outcomes in ai_log.outcome_counts(since).items()
        ]
        return Response(
            AiOversightSerializer(
                {
                    "switches": switches,
                    "window_days": WINDOW_DAYS,
                    "calls": calls,
                    "valuation_model": valuation.current_model(),
                    "failed_valuations": ai_log.recent_failures(oversight.PRICE_ESTIMATE, since),
                }
            ).data
        )


class AiSwitchView(AdminView):
    @extend_schema(
        operation_id="set_admin_ai_switch",
        summary="Turn an AI feature off or on",
        description=(
            "Needs `X-CSRFToken`. Applies to the next request, without a restart, and is "
            "audited. An unknown name is `404 not_found`. " + REFUSALS
        ),
        request=AiSwitchChangeSerializer,
        responses={200: OpenApiResponse(AiSwitchSerializer, description="The switch")},
    )
    def post(self, request, name):
        enabled = single_field(request.data, "enabled")
        if not isinstance(enabled, bool):
            raise field_errors({"enabled": "invalid"})
        state = oversight.set_switch(get_actor(request), name, enabled)
        return Response(AiSwitchSerializer(state).data)
