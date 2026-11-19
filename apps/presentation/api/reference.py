from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog import selectors
from apps.presentation.api.permissions import AllowPublic


class CitySerializer(serializers.Serializer):
    """Allowlist. Names are plain strings: city values are managed in the database."""

    id = serializers.UUIDField()
    name = serializers.CharField()


class CityListSerializer(serializers.Serializer):
    items = CitySerializer(many=True)


class CityView(APIView):
    permission_classes = [AllowPublic]
    authentication_classes: list = []

    @extend_schema(
        operation_id="list_cities",
        summary="Cities available for selection",
        responses={200: OpenApiResponse(CityListSerializer, description="Active cities by name")},
    )
    def get(self, request):
        """Unpaginated: the list is small and bounded."""
        cities = selectors.list_cities()
        return Response({"items": CitySerializer(cities, many=True).data})
