"""Listing endpoints (Story 2.1): start a Draft, read an owned Listing, list own Listings.

Rules live in `listings`; enums come from the Field registry, never hand-copied.
"""

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.utils.urls import replace_query_param
from rest_framework.views import APIView

from apps.catalog import registry
from apps.listings import selectors, services
from apps.listings.lifecycle import ListingStatus, Visibility
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import exact_fields, field_errors
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireSignedIn

CREATE_FIELDS = ("purpose", "category", "subtype")
PAGE_SIZE = selectors.DEFAULT_LIMIT
MAX_PAGE_SIZE = selectors.MAX_LIMIT


def _choices(values):
    return [(v, v) for v in values]


PURPOSES = _choices(registry.purposes())
CATEGORIES = _choices(c.id for c in registry.categories())
SUBTYPES = _choices(registry.all_subtypes())


class ListingFieldsSerializer(serializers.Serializer):
    """Flat registry ids. Null means unknown."""

    purpose = serializers.ChoiceField(choices=PURPOSES, allow_null=True)
    category = serializers.ChoiceField(choices=CATEGORIES, allow_null=True)
    subtype = serializers.ChoiceField(choices=SUBTYPES, allow_null=True)


class ListingSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=_choices(s.value for s in ListingStatus))
    visibility = serializers.ChoiceField(choices=_choices(v.value for v in Visibility))
    revision = serializers.IntegerField(min_value=1)
    registry_version = serializers.IntegerField(min_value=1)
    fields = ListingFieldsSerializer()
    media = serializers.ListField(
        child=serializers.DictField(), help_text="Empty until Story 2.10."
    )
    amenities = serializers.ListField(
        child=serializers.CharField(), help_text="Empty until Story 2.8."
    )


class DraftCreateSerializer(serializers.Serializer):
    purpose = serializers.ChoiceField(choices=PURPOSES, help_text="From the registry.")
    category = serializers.ChoiceField(choices=CATEGORIES, help_text="From the registry.")
    subtype = serializers.ChoiceField(
        choices=SUBTYPES, help_text="A Subtype of the chosen Category."
    )

    def to_internal_value(self, data):
        # Unknown fields are refused here; values and rules are judged by the service, all at once.
        return exact_fields(data, CREATE_FIELDS)


class ListingPageSerializer(serializers.Serializer):
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = ListingSerializer(many=True)


def _data(view: selectors.ListingView) -> dict:
    return ListingSerializer(
        {
            "id": view.id,
            "status": view.status,
            "visibility": view.visibility,
            "revision": view.revision,
            "registry_version": view.registry_version,
            "fields": dict(view.fields),
            "media": list(view.media),
            "amenities": list(view.amenities),
        }
    ).data


class DraftCreateView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="create_listing_draft",
        summary="Start a Draft",
        description=(
            "Needs `X-CSRFToken`. Body: `purpose`, `category` and `subtype`; any other field "
            "(`owner_account_id`, `status`, `revision`) is `validation_error` (`unknown_field`). "
            "Failing fields are reported at once (`required`, `invalid`, `invalid_choice`, "
            "`subtype_not_in_category`). Only Active and Email-unverified Individual and Agency "
            "accounts may start one (`forbidden` otherwise, administrators included). The Draft "
            "belongs to the account. At 20 open Drafts the answer is `429 throttled` without "
            "`Retry-After` (a cap clears when a Draft is removed). Never cached."
        ),
        request=DraftCreateSerializer,
        responses={201: OpenApiResponse(ListingSerializer, description="The new Draft")},
    )
    def post(self, request):
        serializer = DraftCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        try:
            view = services.create_draft(
                get_actor(request),
                values.get("purpose"),
                values.get("category"),
                values.get("subtype"),
            )
        except services.FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response(_data(view), status=status.HTTP_201_CREATED)


class ListingDetailView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="get_listing",
        summary="One of the signed-in account's own Listings",
        description=(
            "Owner only. Another account's, an unknown and a malformed id are all "
            "`404 not_found`, so existence is never confirmed. Never cached."
        ),
        responses={200: OpenApiResponse(ListingSerializer, description="The Listing")},
    )
    def get(self, request, listing_id):
        return Response(_data(selectors.get_owned_listing(get_actor(request), listing_id)))


def _page_size(request) -> int:
    raw = request.query_params.get("page_size")
    if raw and len(raw) <= 3 and raw.isascii() and raw.isdigit() and 1 <= int(raw) <= MAX_PAGE_SIZE:
        return int(raw)
    return PAGE_SIZE


def _link(request, cursor: str | None) -> str | None:
    if cursor is None:
        return None
    return replace_query_param(request.build_absolute_uri(), "cursor", cursor)


class MyListingsView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="list_my_listings",
        summary="The signed-in account's own Listings, newest first",
        description=(
            "Own Listings only, newest first, with cursor pagination (`next` link, `previous` is "
            "always null; 20 per page, `page_size` up to 50, an invalid `page_size` falls back to "
            "the default). `status` filters by a known status. A bad `cursor` or `status` is "
            "`validation_error` (`fields.cursor`, `fields.status`). Never cached."
        ),
        parameters=[
            OpenApiParameter("status", str, enum=[s.value for s in ListingStatus], required=False),
            OpenApiParameter("page_size", int, required=False),
            OpenApiParameter("cursor", str, required=False),
        ],
        responses={200: OpenApiResponse(ListingPageSerializer, description="One page")},
    )
    def get(self, request):
        state = request.query_params.get("status")
        if state is not None and state not in {s.value for s in ListingStatus}:
            raise field_errors({"status": "invalid_choice"})
        try:
            page = selectors.list_owned(
                get_actor(request),
                state,
                request.query_params.get("cursor") or None,  # an empty cursor is page one
                _page_size(request),
            )
        except selectors.InvalidCursor:  # a stable code, never the value
            raise field_errors({"cursor": "invalid"}) from None
        return Response(
            {
                "next": _link(request, page.next_cursor),
                "previous": None,
                "results": [_data(v) for v in page.items],
            }
        )
