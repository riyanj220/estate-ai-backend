"""Administrator reference data (Story 10.5). Rules live in `catalog.services`.

Cities, localities and amenities are added, renamed and deactivated, never deleted. Purposes,
Categories, Subtypes and area units come from the Field registry and are read only.
"""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response

from apps.catalog import registry, selectors, services
from apps.presentation.adminapi.params import REFUSALS, AdminView
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import exact_fields, field_errors, single_field

_WRITE = (
    "Needs `X-CSRFToken`. A bad or already taken value is `400 validation_error`; an unknown "
    "item is `404 not_found`. Every change is audited. "
)


class AdminCitySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    is_active = serializers.BooleanField()


class AdminLocalitySerializer(AdminCitySerializer):
    kind = serializers.CharField(help_text="`area` or `society`.")


class AdminAmenitySerializer(serializers.Serializer):
    code = serializers.CharField()
    label = serializers.CharField()
    kind = serializers.CharField(help_text="`on_property`, `community` or `nearby`.")
    group = serializers.CharField()
    is_active = serializers.BooleanField()


class AdminCityListSerializer(serializers.Serializer):
    items = AdminCitySerializer(many=True)


class AdminLocalityListSerializer(serializers.Serializer):
    items = AdminLocalitySerializer(many=True)


class AdminAmenityListSerializer(serializers.Serializer):
    items = AdminAmenitySerializer(many=True)


class NewCitySerializer(serializers.Serializer):
    name = serializers.CharField()


class NewLocalitySerializer(serializers.Serializer):
    name = serializers.CharField()
    kind = serializers.CharField(help_text="`area` or `society`.")


class NewAmenitySerializer(serializers.Serializer):
    code = serializers.CharField(help_text="snake_case; never changes.")
    label = serializers.CharField()
    kind = serializers.CharField(help_text="`on_property`, `community` or `nearby`.")
    group = serializers.CharField(help_text="snake_case.")


class PlaceChangeSerializer(serializers.Serializer):
    name = serializers.CharField(required=False)
    is_active = serializers.BooleanField(required=False)


class AmenityChangeSerializer(serializers.Serializer):
    label = serializers.CharField(required=False)
    is_active = serializers.BooleanField(required=False)


class FixedCategorySerializer(serializers.Serializer):
    id = serializers.CharField()
    subtypes = serializers.ListField(child=serializers.CharField())


class FixedCatalogueSerializer(serializers.Serializer):
    purposes = serializers.ListField(child=serializers.CharField())
    categories = FixedCategorySerializer(many=True)
    area_units = serializers.ListField(child=serializers.CharField())


def _changes(data, name_field: str) -> dict:
    values = exact_fields(data, (name_field, "is_active"))
    if not values:
        raise field_errors({"non_field_errors": "required"})
    for key, value in values.items():
        if not isinstance(value, bool if key == "is_active" else str):
            raise field_errors({key: "invalid"})
    return values


def _new(data, names: tuple[str, ...]) -> list:
    values = exact_fields(data, names)
    missing = {name: "required" for name in names if name not in values}
    if missing:
        raise field_errors(missing)
    return [values[name] for name in names]


class AdminFixedCatalogueView(AdminView):
    @extend_schema(
        operation_id="get_admin_fixed_catalogue",
        summary="Purposes, Categories, Subtypes and area units (read only)",
        description="Fixed by the product. " + REFUSALS,
        responses={200: OpenApiResponse(FixedCatalogueSerializer, description="Fixed values")},
    )
    def get(self, request):
        return Response(
            FixedCatalogueSerializer(
                {
                    "purposes": registry.purposes(),
                    "categories": registry.categories(),
                    "area_units": registry.field("area_unit").allowed_values,
                }
            ).data
        )


class AdminCitiesView(AdminView):
    @extend_schema(
        operation_id="list_admin_cities",
        summary="Every city, deactivated ones included",
        description=REFUSALS,
        responses={200: OpenApiResponse(AdminCityListSerializer, description="Cities")},
    )
    def get(self, request):
        items = selectors.admin_cities(get_actor(request))
        return Response(AdminCityListSerializer({"items": items}).data)

    @extend_schema(
        operation_id="create_admin_city",
        summary="Add a city",
        description=_WRITE + REFUSALS,
        request=NewCitySerializer,
        responses={204: OpenApiResponse(description="Added")},
    )
    def post(self, request):
        services.create_city(get_actor(request), single_field(request.data, "name"))
        return Response(status=204)


class AdminCityView(AdminView):
    @extend_schema(
        operation_id="update_admin_city",
        summary="Rename, deactivate or reactivate a city",
        description=_WRITE + REFUSALS,
        request=PlaceChangeSerializer,
        responses={204: OpenApiResponse(description="Changed")},
    )
    def patch(self, request, city_id):
        services.update_city(get_actor(request), city_id, **_changes(request.data, "name"))
        return Response(status=204)


class AdminCityLocalitiesView(AdminView):
    @extend_schema(
        operation_id="list_admin_localities",
        summary="Every locality of a city, deactivated ones included",
        description="An unknown city is `404 not_found`. " + REFUSALS,
        responses={200: OpenApiResponse(AdminLocalityListSerializer, description="Localities")},
    )
    def get(self, request, city_id):
        items = selectors.admin_localities(get_actor(request), city_id)
        return Response(AdminLocalityListSerializer({"items": items}).data)

    @extend_schema(
        operation_id="create_admin_locality",
        summary="Add an area or society to an active city",
        description=_WRITE + REFUSALS,
        request=NewLocalitySerializer,
        responses={204: OpenApiResponse(description="Added")},
    )
    def post(self, request, city_id):
        name, kind = _new(request.data, ("name", "kind"))
        services.create_locality(get_actor(request), city_id, name, kind)
        return Response(status=204)


class AdminLocalityView(AdminView):
    @extend_schema(
        operation_id="update_admin_locality",
        summary="Rename, deactivate or reactivate a locality",
        description=_WRITE + REFUSALS,
        request=PlaceChangeSerializer,
        responses={204: OpenApiResponse(description="Changed")},
    )
    def patch(self, request, locality_id):
        services.update_locality(get_actor(request), locality_id, **_changes(request.data, "name"))
        return Response(status=204)


class AdminAmenitiesView(AdminView):
    @extend_schema(
        operation_id="list_admin_amenities",
        summary="Every amenity and nearby facility, deactivated ones included",
        description=REFUSALS,
        responses={200: OpenApiResponse(AdminAmenityListSerializer, description="Amenities")},
    )
    def get(self, request):
        items = selectors.admin_amenities(get_actor(request))
        return Response(AdminAmenityListSerializer({"items": items}).data)

    @extend_schema(
        operation_id="create_admin_amenity",
        summary="Add an amenity or nearby facility",
        description=_WRITE + REFUSALS,
        request=NewAmenitySerializer,
        responses={204: OpenApiResponse(description="Added")},
    )
    def post(self, request):
        values = _new(request.data, ("code", "label", "kind", "group"))
        services.create_amenity(get_actor(request), *values)
        return Response(status=204)


class AdminAmenityView(AdminView):
    @extend_schema(
        operation_id="update_admin_amenity",
        summary="Relabel, deactivate or reactivate an amenity",
        description=_WRITE + REFUSALS,
        request=AmenityChangeSerializer,
        responses={204: OpenApiResponse(description="Changed")},
    )
    def patch(self, request, code):
        services.update_amenity(get_actor(request), code, **_changes(request.data, "label"))
        return Response(status=204)
