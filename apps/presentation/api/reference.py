from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog import selectors
from apps.kernel.errors import DomainError
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


class LocalitySerializer(serializers.Serializer):
    """Allowlist. Names are plain strings: locality values are managed in the database. `kind`
    is `area` or `society`, a closed vocabulary the form groups by."""

    id = serializers.UUIDField()
    name = serializers.CharField()
    kind = serializers.ChoiceField(choices=[("area", "area"), ("society", "society")])


class LocalityListSerializer(serializers.Serializer):
    items = LocalitySerializer(many=True)


class LocalityView(APIView):
    permission_classes = [AllowPublic]
    authentication_classes: list = []

    @extend_schema(
        operation_id="list_localities",
        summary="Areas and societies of a city",
        description=(
            "Public. The active localities of an active city, ordered by name. An unknown, "
            "inactive or malformed city id is `404 not_found`. Unpaginated: the list is small "
            "and bounded."
        ),
        responses={
            200: OpenApiResponse(LocalityListSerializer, description="Active localities by name"),
        },
    )
    def get(self, request, city_id):
        localities = selectors.list_localities(city_id)
        if localities is None:
            raise DomainError("not_found")
        return Response({"items": LocalitySerializer(localities, many=True).data})


class AmenitySerializer(serializers.Serializer):
    """Allowlist. Codes, labels and groups are plain strings (managed in the database, never
    OpenAPI enums). `kind` is the closed vocabulary the form groups by; `nearby` entries are
    declared by the seller and are never verified."""

    code = serializers.CharField()
    label = serializers.CharField()
    kind = serializers.ChoiceField(choices=[(k, k) for k in selectors.AMENITY_KINDS])
    group = serializers.CharField()


class AmenityListSerializer(serializers.Serializer):
    items = AmenitySerializer(many=True)


class AmenityView(APIView):
    permission_classes = [AllowPublic]
    authentication_classes: list = []

    @extend_schema(
        operation_id="list_amenities",
        summary="The amenity and nearby facility catalogue",
        description=(
            "Public. The active catalogue ordered by `kind` (`on_property`, `community`, `nearby`) "
            "and then by display order. `code` is the stable identifier a listing stores; `label` "
            "and `group` are for display. A `nearby` entry is the seller's own declaration: "
            'clients must label it "Seller-declared"; there is no distance and no verification. '
            "Unpaginated: the list is small and bounded."
        ),
        responses={200: OpenApiResponse(AmenityListSerializer, description="Active amenities")},
    )
    def get(self, request):
        return Response({"items": AmenitySerializer(selectors.list_amenities(), many=True).data})
