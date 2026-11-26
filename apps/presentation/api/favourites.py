"""Favourites (Story 3.7): the signed-in account's saved Listings. Rules live in `listings`."""

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.listings import selectors, services
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import field_errors
from apps.presentation.api.listings import _link, _page_size
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireSignedIn
from apps.presentation.api.public_listings import PublicListingCardSerializer, _card
from apps.presentation.api.serializers import ErrorBodySerializer


class FavouriteSerializer(serializers.Serializer):
    listing_id = serializers.UUIDField()
    saved_at = serializers.DateTimeField()
    listing = PublicListingCardSerializer(
        allow_null=True,
        help_text="The public card; null when the Listing is no longer available.",
    )


class FavouritePageSerializer(serializers.Serializer):
    results = FavouriteSerializer(many=True)
    next = serializers.URLField(allow_null=True, help_text="The next page; null on the last.")


class FavouriteIdsSerializer(serializers.Serializer):
    listing_ids = serializers.ListField(child=serializers.UUIDField())


def _refusals(*statuses: int) -> dict:
    meaning = {
        401: "`unauthenticated`: a Visitor",
        403: "`forbidden`: not allowed for this account, or a missing CSRF token",
        404: "`not_found`",
        429: "`throttled`: 500 favourites are saved; no `Retry-After`",
    }
    return {code: OpenApiResponse(ErrorBodySerializer, meaning[code]) for code in statuses}


class FavouritesView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="list_favourites",
        summary="The signed-in account's favourites, latest saved first",
        description=(
            "Own favourites only (Individual and Agency accounts; `forbidden` for others). A "
            "saved Listing that is no longer publicly visible has `listing: null` and nothing "
            "else about it. 20 per page, `page_size` up to 50; a bad `cursor` is "
            "`validation_error`. Never cached."
        ),
        parameters=[
            OpenApiParameter("page_size", int, required=False),
            OpenApiParameter("cursor", str, required=False),
        ],
        responses={
            200: OpenApiResponse(FavouritePageSerializer, description="One page"),
            **_refusals(401, 403),
        },
    )
    def get(self, request):
        try:
            page = selectors.list_favourites(
                get_actor(request),
                request.query_params.get("cursor") or None,
                _page_size(request),
            )
        except selectors.InvalidCursor:  # a stable code, never the value
            raise field_errors({"cursor": "invalid"}) from None
        results = [
            {
                "listing_id": item.listing_id,
                "saved_at": item.saved_at,
                "listing": None if item.entry is None else _card(item.entry),
            }
            for item in page.items
        ]
        return Response(
            FavouritePageSerializer(
                {"results": results, "next": _link(request, page.next_cursor)}
            ).data
        )


class FavouriteIdsView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="list_favourite_ids",
        summary="The ids of the Listings the account has saved",
        description="All of them (at most 500), for the Save buttons. Never cached.",
        responses={
            200: OpenApiResponse(FavouriteIdsSerializer, description="The ids"),
            **_refusals(401, 403),
        },
    )
    def get(self, request):
        ids = selectors.favourite_ids(get_actor(request))
        return Response(FavouriteIdsSerializer({"listing_ids": ids}).data)


class FavouriteDetailView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="save_favourite",
        summary="Save a Listing to favourites",
        description=(
            "Needs `X-CSRFToken`. No body. Idempotent: saving a saved Listing is `204` again. "
            "Only a publicly visible Listing can be newly saved; anything else is `not_found`."
        ),
        request=None,
        responses={
            204: OpenApiResponse(description="Saved"),
            **_refusals(401, 403, 404, 429),
        },
    )
    def put(self, request, listing_id):
        services.save_favourite(get_actor(request), listing_id)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        operation_id="remove_favourite",
        summary="Remove a Listing from favourites",
        description=(
            "Needs `X-CSRFToken`. The Listing itself is not touched. A Listing the account has "
            "not saved is `not_found`."
        ),
        request=None,
        responses={
            204: OpenApiResponse(description="Removed"),
            **_refusals(401, 403, 404),
        },
    )
    def delete(self, request, listing_id):
        services.remove_favourite(get_actor(request), listing_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
