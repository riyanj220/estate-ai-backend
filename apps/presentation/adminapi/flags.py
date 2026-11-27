"""The advisory duplicate and suspicious flags (Story 9.6). Rules live in `governance.flags`."""

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response

from apps.governance import flags
from apps.presentation.adminapi.reports import _REFUSALS, _AdminView
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import field_errors
from apps.presentation.api.listings import _link

KINDS = list(flags.KINDS)


class FlaggedListingSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField(allow_null=True, help_text="Null for a Draft.")
    status = serializers.CharField()
    owner_account_id = serializers.UUIDField()


class ListingFlagSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=KINDS)
    flagged_at = serializers.DateTimeField()
    listing = FlaggedListingSerializer()
    other_listing = FlaggedListingSerializer(
        allow_null=True, help_text="The older Listing of a duplicate pair."
    )
    matched = serializers.ListField(
        child=serializers.ChoiceField(choices=["price", "title"]),
        help_text="Duplicate: what is near-identical.",
    )
    reporter_count = serializers.IntegerField(help_text="Suspicious: different reporters.")


class ListingFlagPageSerializer(serializers.Serializer):
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = ListingFlagSerializer(many=True)


class AdminFlagsView(_AdminView):
    @extend_schema(
        operation_id="list_admin_flags",
        summary="Advisory duplicate and suspicious Listing flags, newest first",
        description=(
            "20 per page; `next` is a link, `previous` is always null. Flags change nothing. "
            + _REFUSALS
        ),
        parameters=[
            OpenApiParameter("kind", str, required=False, enum=KINDS),
            OpenApiParameter("cursor", str, required=False),
        ],
        responses={200: OpenApiResponse(ListingFlagPageSerializer, description="One page")},
    )
    def get(self, request):
        kind = request.query_params.get("kind") or None
        if kind is not None and kind not in KINDS:
            raise field_errors({"kind": "invalid"})
        try:
            page = flags.flag_page(
                get_actor(request), kind, request.query_params.get("cursor") or None
            )
        except flags.InvalidCursor:
            raise field_errors({"cursor": "invalid"}) from None
        return Response(
            ListingFlagPageSerializer(
                {"next": _link(request, page.next_cursor), "previous": None, "results": page.items}
            ).data
        )
