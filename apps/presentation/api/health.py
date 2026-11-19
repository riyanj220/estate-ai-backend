import logging

from django.db import connection
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    renderer_classes,
)
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

logger = logging.getLogger(__name__)


class AlwaysJSONNegotiation(BaseContentNegotiation):
    """Probes may send any Accept header; health always answers JSON."""

    def select_parser(self, request, parsers):
        return parsers[0] if parsers else None

    def select_renderer(self, request, renderers, format_suffix=None):
        return (renderers[0], renderers[0].media_type)


class HealthSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok"])


class HealthUnavailableSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["unavailable"])


@extend_schema(
    operation_id="health",
    summary="Liveness plus database reachability",
    responses={
        200: OpenApiResponse(HealthSerializer, description="Service and database are reachable"),
        503: OpenApiResponse(HealthUnavailableSerializer, description="Database unreachable"),
    },
)
@api_view(["GET", "HEAD"])
@renderer_classes([JSONRenderer])
@authentication_classes([])
@permission_classes([])
def health(request):
    """Liveness plus database reachability. Never reveals connection details."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 - any failure means unavailable
        logger.error("Health check failed: %s", type(exc).__name__)
        return Response({"status": "unavailable"}, status=503)
    return Response({"status": "ok"})


health.cls.content_negotiation_class = AlwaysJSONNegotiation
