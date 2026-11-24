"""The public Field registry (Story 2.1): taxonomy and field definitions from `catalog`."""

from django.http import HttpResponseNotModified
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog import registry
from apps.presentation.api.permissions import AllowPublic

MAX_AGE = 300
# Bump when the response shape (serializers) changes without a registry change, so client
# caches are invalidated.
RESPONSE_SHAPE_VERSION = 4
FIELD_TYPES = registry.FIELD_TYPES


class ApplicabilitySerializer(serializers.Serializer):
    purposes = serializers.ListField(child=serializers.CharField())
    categories = serializers.ListField(child=serializers.CharField())
    subtypes = serializers.ListField(child=serializers.CharField())


class RegistryFieldSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="Stable snake_case id.")
    type = serializers.ChoiceField(choices=registry.FIELD_TYPES)
    requirement = serializers.ChoiceField(choices=registry.REQUIREMENTS)
    applicability = ApplicabilitySerializer(help_text="Empty lists mean all values.")
    allowed_values = serializers.ListField(
        child=serializers.CharField(), allow_null=True, help_text="Enums only; null otherwise."
    )
    allowed_values_by_purpose = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        allow_null=True,
        help_text="Enums narrowed by Listing purpose (purpose -> allowed values); else null.",
    )
    allowed_values_by_subtype = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        allow_null=True,
        help_text=(
            "Enums narrowed by Listing Subtype (Subtype -> allowed values, one entry per Subtype "
            "the field applies to); else null. When present it takes precedence over the "
            "choices in `allowed_values` for that Subtype."
        ),
    )
    seller_declared = serializers.BooleanField(
        help_text=(
            "A claim the seller makes (legal, approval, possession): show it as "
            '"Seller-declared", never as verified by EstateAI.'
        )
    )
    min_length = serializers.IntegerField(
        allow_null=True, help_text="Text only: minimum characters after normalisation."
    )
    max_length = serializers.IntegerField(
        allow_null=True, help_text="Text only: maximum characters after normalisation."
    )
    min = serializers.CharField(
        source="minimum",
        allow_null=True,
        help_text="Money, decimal and integer: smallest value, a decimal string.",
    )
    max = serializers.CharField(
        source="maximum",
        allow_null=True,
        help_text="Money, decimal and integer: largest value, a decimal string.",
    )
    currency = serializers.CharField(allow_null=True, help_text="Money only: `PKR`.")
    scale = serializers.IntegerField(
        allow_null=True, help_text="Decimal only: the number of decimals stored and returned."
    )
    derived = serializers.BooleanField(
        help_text="Computed by the server from other fields; read-only, never accepted as input."
    )
    description = serializers.CharField(
        allow_null=True, help_text="What the value means, for the form; null when not needed."
    )
    class_ = serializers.ChoiceField(choices=registry.CLASSES, source="field_class")
    searchable = serializers.BooleanField()
    sortable = serializers.BooleanField()
    ai_extractable = serializers.BooleanField()
    valuation_input = serializers.BooleanField()
    quota_identity = serializers.BooleanField()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["class"] = data.pop("class_")  # `class` is reserved in Python
        return data


class TaxonomyCategorySerializer(serializers.Serializer):
    id = serializers.CharField()
    subtypes = serializers.ListField(child=serializers.CharField())


class TaxonomySerializer(serializers.Serializer):
    categories = TaxonomyCategorySerializer(many=True)
    purposes = serializers.ListField(child=serializers.CharField())


class RegistrySerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)
    taxonomy = TaxonomySerializer()
    fields = RegistryFieldSerializer(many=True)


def etag() -> str:
    """Strong validator from the version and the content hash."""
    return f'"{registry.registry_version()}-{registry.content_hash()}-{RESPONSE_SHAPE_VERSION}"'


def _matches(header: str | None, tag: str) -> bool:
    if not header:
        return False
    if header.strip() == "*":
        return True
    candidates = {part.strip().removeprefix("W/") for part in header.split(",")}
    return tag in candidates


def _body() -> dict:
    return RegistrySerializer(
        {
            "version": registry.registry_version(),
            "taxonomy": {
                "categories": [
                    {"id": c.id, "subtypes": list(c.subtypes)} for c in registry.categories()
                ],
                "purposes": list(registry.purposes()),
            },
            "fields": [
                {
                    "id": f.id,
                    "type": f.type,
                    "requirement": f.requirement,
                    "applicability": {
                        "purposes": list(f.applicability.purposes),
                        "categories": list(f.applicability.categories),
                        "subtypes": list(f.applicability.subtypes),
                    },
                    "allowed_values": None if f.allowed_values is None else list(f.allowed_values),
                    "allowed_values_by_purpose": (
                        None
                        if f.allowed_values_by_purpose is None
                        else {p: list(v) for p, v in f.allowed_values_by_purpose}
                    ),
                    "allowed_values_by_subtype": (
                        None
                        if f.allowed_values_by_subtype is None
                        else {s: list(v) for s, v in f.allowed_values_by_subtype}
                    ),
                    "seller_declared": f.seller_declared,
                    "min_length": f.min_length,
                    "max_length": f.max_length,
                    "minimum": None if f.minimum is None else format(f.minimum, "f"),
                    "maximum": None if f.maximum is None else format(f.maximum, "f"),
                    "currency": f.currency,
                    "scale": f.scale,
                    "derived": f.derived,
                    "description": f.description,
                    "field_class": f.field_class,
                    "searchable": f.searchable,
                    "sortable": f.sortable,
                    "ai_extractable": f.ai_extractable,
                    "valuation_input": f.valuation_input,
                    "quota_identity": f.quota_identity,
                }
                for f in registry.fields()
            ],
        }
    ).data


class RegistryView(APIView):
    permission_classes = [AllowPublic]
    authentication_classes: list = []

    @extend_schema(
        operation_id="get_registry",
        summary="The Field registry and taxonomy",
        description=(
            "Public. The versioned definition of every Listing field, with the purposes, "
            "Categories and Subtypes. Strong `ETag` from the version and content hash; send "
            "`If-None-Match` to get `304`. `Cache-Control: public, max-age=300`."
        ),
        responses={
            200: OpenApiResponse(RegistrySerializer, description="The registry"),
            304: OpenApiResponse(description="Not modified"),
        },
    )
    def get(self, request):
        tag = etag()
        if _matches(request.headers.get("If-None-Match"), tag):
            response = HttpResponseNotModified()
            response["ETag"] = tag
            response["Cache-Control"] = f"public, max-age={MAX_AGE}"
            return response
        response = Response(_body())
        response["ETag"] = tag
        response["Cache-Control"] = f"public, max-age={MAX_AGE}"
        return response
