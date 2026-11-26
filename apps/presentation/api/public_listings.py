"""The public marketplace (Story 3.1): browse Published Listings and read one.

Both answers are built only from the public projection (`selectors.PublicListingView`), are the
same for every caller (no authentication, no cookie) and hide everything else behind `404`.
"""

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.utils.urls import replace_query_param
from rest_framework.views import APIView

from apps.listings import selectors
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import field_errors
from apps.presentation.api.caching import cache_publicly
from apps.presentation.api.listings import (
    CATEGORIES,
    PURPOSES,
    SUBTYPES,
    PublicListingSerializer,
    _field_serializer,
    public_values,
)
from apps.presentation.api.permissions import AllowPublic
from apps.presentation.api.serializers import ErrorBodySerializer

# The facts a card shows. Values come from the projection's `fields`, which holds public ones only.
CARD_FIELD_IDS = (
    "asking_price",
    "price_basis",
    "rental_period",
    "area_value",
    "area_unit",
    "marla_basis",
    "area_sqft",
    "bedrooms",
    "bathrooms",
)
CardFieldsSerializer = _field_serializer(
    "PublicCardFieldsSerializer",
    CARD_FIELD_IDS,
    False,
    "Price, area and room facts by registry id. Null means unknown or not applicable.",
)


class PlaceSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class PublicListingCardSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    purpose = serializers.ChoiceField(choices=PURPOSES)
    category = serializers.ChoiceField(choices=CATEGORIES)
    subtype = serializers.ChoiceField(choices=SUBTYPES)
    title = serializers.CharField(allow_null=True)
    fields = CardFieldsSerializer()
    city = PlaceSerializer(allow_null=True)
    locality = PlaceSerializer(allow_null=True)
    cover_url = serializers.CharField(
        allow_null=True, help_text="Relative path of the cover image on the media endpoint."
    )
    published_on = serializers.DateField(
        help_text="The Asia/Karachi date of the first publication."
    )


class PublicListingPageSerializer(serializers.Serializer):
    results = PublicListingCardSerializer(many=True)
    next = serializers.URLField(allow_null=True, help_text="The next page; null on the last.")


class PublicListingDetailSerializer(PublicListingSerializer):
    """The public projection plus the publication date and the names of the city and locality."""

    city = PlaceSerializer(allow_null=True)
    locality = PlaceSerializer(allow_null=True)
    published_on = serializers.DateField(
        help_text="The Asia/Karachi date of the first publication."
    )


def _discovery(entry: selectors.PublicEntry) -> dict:
    return {"city": entry.city, "locality": entry.locality, "published_on": entry.published_on}


def _card(entry: selectors.PublicEntry) -> dict:
    view = entry.listing
    return {
        "id": view.id,
        "purpose": view.purpose,
        "category": view.category,
        "subtype": view.subtype,
        "title": view.title,
        "fields": {name: view.fields.get(name) for name in CARD_FIELD_IDS},
        "cover_url": next((image.url for image in view.media if image.is_cover), None),
        **_discovery(entry),
    }


def _limit(raw: str | None) -> int:
    if raw is None:
        return selectors.DEFAULT_LIMIT
    if len(raw) <= 3 and raw.isascii() and raw.isdigit() and 1 <= int(raw) <= selectors.MAX_LIMIT:
        return int(raw)
    raise field_errors({"limit": "invalid"})


class PublicReadView(APIView):
    """Anyone may read, nobody is identified: a success may be cached by anyone for a minute,
    every other answer is `no-store`."""

    permission_classes = [AllowPublic]
    authentication_classes: list = []

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            cache_publicly(request, response)
        else:
            response["Cache-Control"] = "no-store"
        return response


class PublicListingsView(PublicReadView):
    @extend_schema(
        operation_id="list_public_listings",
        summary="Browse the published Listings, newest first",
        description=(
            "Public; the answer is the same for every caller. Only publicly visible Listings "
            "(published, not restricted, owner Active), newest first publication first. `limit` "
            "is 1 to 50 (default 20); `next` is the link to the following page and carries an "
            "opaque `cursor`. A bad `limit`, `cursor` or `sort` is `400 validation_error` under "
            "that name in `fields`. `200` has `Cache-Control: public, max-age=60` and no cookie "
            "(so a Listing may stay in a cache for up to a minute after it stops being public); "
            "errors are `no-store`."
        ),
        parameters=[
            OpenApiParameter("sort", str, enum=list(selectors.PUBLIC_SORTS), required=False),
            OpenApiParameter("limit", int, required=False),
            OpenApiParameter("cursor", str, required=False),
        ],
        responses={
            200: OpenApiResponse(PublicListingPageSerializer, description="One page"),
            400: OpenApiResponse(ErrorBodySerializer, description="`validation_error`"),
        },
    )
    def get(self, request):
        sort = request.query_params.get("sort", selectors.SORT_NEWEST)
        if sort not in selectors.PUBLIC_SORTS:
            raise field_errors({"sort": "invalid_choice"})
        limit = _limit(request.query_params.get("limit"))
        try:
            page = selectors.search_public(
                get_actor(request),
                selectors.SearchSpec(),
                sort,
                request.query_params.get("cursor") or None,  # an empty cursor is page one
                limit,
            )
        except selectors.InvalidCursor:  # a stable code, never the value
            raise field_errors({"cursor": "invalid"}) from None
        following = (
            None
            if page.next_cursor is None
            else replace_query_param(request.build_absolute_uri(), "cursor", page.next_cursor)
        )
        return Response(
            PublicListingPageSerializer(
                {"results": [_card(entry) for entry in page.items], "next": following}
            ).data
        )


class PublicListingDetailView(PublicReadView):
    @extend_schema(
        operation_id="get_public_listing",
        summary="One published Listing",
        description=(
            "Public; the answer is the same for every caller, the owner included. The public "
            "projection only: never a private address, coordinates, a phone number, the owner, "
            "the status, the revision or the provenance. `nearby` amenities are the seller's own "
            "declaration. A Listing that is not publicly visible, an unknown and a malformed id "
            "are the same `404 not_found`. `200` has `Cache-Control: public, max-age=60` and no "
            "cookie; errors are `no-store`."
        ),
        responses={
            200: OpenApiResponse(PublicListingDetailSerializer, description="The Listing"),
            404: OpenApiResponse(ErrorBodySerializer, description="`not_found`"),
        },
    )
    def get(self, request, listing_id):
        entry = selectors.public_detail(get_actor(request), listing_id)
        return Response(
            PublicListingDetailSerializer(
                {**public_values(entry.listing), **_discovery(entry)}
            ).data
        )
