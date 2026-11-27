"""Administrator Listing review and moderation actions (Stories 9.3 and 9.4).

Rules live in `listings` and `governance`. Content is the public projection (never the Private
address); a Draft's content is a reasoned private read.
"""

from dataclasses import asdict
from datetime import date

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog import registry
from apps.governance import moderation, reports
from apps.governance.services import FieldsInvalid
from apps.identity import selectors as identity
from apps.listings import selectors
from apps.listings.lifecycle import MODERATION_ACTIONS, ListingStatus
from apps.presentation.adminapi.reports import AccountRefSerializer
from apps.presentation.api.actors import get_actor
from apps.presentation.api.authentication import SessionAuthentication
from apps.presentation.api.bodies import field_errors, single_field
from apps.presentation.api.listings import PublicImageSerializer, _link
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireAdmin
from apps.presentation.api.private_reads import ReasonedReadMixin, ReasonedReadSerializer

_STATUSES = [s.value for s in ListingStatus]
_CATEGORIES = [c.id for c in registry.categories()]
_ACTIONS = list(MODERATION_ACTIONS)


class AdminListingRowSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    owner_account_id = serializers.UUIDField()
    owner = AccountRefSerializer(allow_null=True)
    status = serializers.ChoiceField(choices=_STATUSES)
    restricted = serializers.BooleanField()
    under_review = serializers.BooleanField()
    purpose = serializers.CharField()
    category = serializers.CharField()
    subtype = serializers.CharField()
    title = serializers.CharField(allow_null=True, help_text="Always null for a Draft.")
    created_at = serializers.DateTimeField()


class AdminListingPageSerializer(serializers.Serializer):
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = AdminListingRowSerializer(many=True)


class ModerationRecordSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=_ACTIONS)
    reason = serializers.CharField()
    status_before = serializers.ChoiceField(choices=_STATUSES)
    status_after = serializers.ChoiceField(choices=_STATUSES)
    restriction_after = serializers.BooleanField()
    at = serializers.DateTimeField()
    by_account_id = serializers.UUIDField()


class AdminListingDetailSerializer(AdminListingRowSerializer):
    description = serializers.CharField(allow_null=True)
    attributes = serializers.DictField(help_text="Public registry fields that have a value.")
    city = serializers.CharField(allow_null=True)
    locality = serializers.CharField(allow_null=True)
    media = PublicImageSerializer(many=True, help_text="Empty for a Draft.")
    image_count = serializers.IntegerField()
    moderation_reason = serializers.CharField(allow_null=True)
    actions = serializers.ListField(
        child=serializers.ChoiceField(choices=_ACTIONS), help_text="What can be done now."
    )
    first_published_at = serializers.DateTimeField(allow_null=True)
    open_reports = serializers.IntegerField()
    history = ModerationRecordSerializer(many=True)


class ModerationReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(
        min_length=moderation.REASON_MIN,
        max_length=moderation.REASON_MAX,
        help_text="One line, without contact details or links. The owner reads it.",
    )


class _AdminView(NoStoreMixin, APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [RequireAdmin]


_REFUSALS = (
    "Visitors get `401 unauthenticated`; signed-in non-administrators get `404 not_found`. "
    "Never cached."
)


def _row_data(row, contacts, reviewing) -> dict:
    return {
        "id": row.id,
        "owner_account_id": row.owner_account_id,
        "owner": contacts.get(row.owner_account_id),
        "status": row.status,
        "restricted": row.restricted,
        "under_review": row.id in reviewing,
        "purpose": row.purpose,
        "category": row.category,
        "subtype": row.subtype,
        "title": row.title,
        "created_at": row.created_at,
    }


def _detail_response(actor, detail, *, draft: bool = False) -> Response:
    row = detail.row
    view = detail.listing
    data = _row_data(
        row,
        identity.masked_contacts([row.owner_account_id]),
        reports.under_review([row.id]),
    )
    data.update(
        {
            "description": view.description,
            "attributes": {
                name: dict(value) if hasattr(value, "items") else value
                for name, value in view.fields.items()
                if value is not None and name not in ("city", "locality")
            },
            "city": detail.city,
            "locality": detail.locality,
            "media": [] if draft else view.media,
            "image_count": len(view.media),
            "moderation_reason": detail.reason,
            "actions": detail.actions,
            "first_published_at": detail.first_published_at,
            "open_reports": reports.open_report_count(actor, row.id),
            "history": moderation.history(actor, row.id),
        }
    )
    return Response(AdminListingDetailSerializer(data).data)


def _date_param(request, name: str) -> date | None:
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        value = date.fromisoformat(raw)
    except ValueError:
        raise field_errors({name: "invalid"}) from None
    if len(raw) != 10 or not 2000 <= value.year <= 2999:
        raise field_errors({name: "invalid"})
    return value


def _choice_param(request, name: str, choices) -> str | None:
    value = request.query_params.get(name) or None
    if value is not None and value not in choices:
        raise field_errors({name: "invalid"})
    return value


class AdminListingsView(_AdminView):
    @extend_schema(
        operation_id="list_admin_listings",
        summary="Listings for review, newest first",
        description=(
            "20 per page; `next` is a link, `previous` is always null. Without `status` every "
            "non-Draft Listing; `status=draft` lists Drafts without their content. `owner` is an "
            "account id; the dates are Karachi calendar days, inclusive. " + _REFUSALS
        ),
        parameters=[
            OpenApiParameter("owner", str, required=False),
            OpenApiParameter("category", str, required=False, enum=_CATEGORIES),
            OpenApiParameter("status", str, required=False, enum=_STATUSES),
            OpenApiParameter("created_from", date, required=False),
            OpenApiParameter("created_to", date, required=False),
            OpenApiParameter("cursor", str, required=False),
        ],
        responses={200: OpenApiResponse(AdminListingPageSerializer, description="One page")},
    )
    def get(self, request):
        raw_owner = request.query_params.get("owner") or None
        owner = identity.parse_id(raw_owner) if raw_owner else None
        if raw_owner and owner is None:
            raise field_errors({"owner": "invalid"})
        try:
            page = selectors.admin_listing_page(
                get_actor(request),
                owner_id=owner,
                category=_choice_param(request, "category", _CATEGORIES),
                status=_choice_param(request, "status", _STATUSES),
                created_from=_date_param(request, "created_from"),
                created_to=_date_param(request, "created_to"),
                cursor=request.query_params.get("cursor") or None,
            )
        except selectors.InvalidCursor:
            raise field_errors({"cursor": "invalid"}) from None
        contacts = identity.masked_contacts([r.owner_account_id for r in page.items])
        reviewing = reports.under_review([r.id for r in page.items])
        return Response(
            AdminListingPageSerializer(
                {
                    "next": _link(request, page.next_cursor),
                    "previous": None,
                    "results": [_row_data(r, contacts, reviewing) for r in page.items],
                }
            ).data
        )


class AdminListingView(_AdminView):
    @extend_schema(
        operation_id="get_admin_listing",
        summary="One non-Draft Listing with its content, images, actions and history",
        description="A Draft is `404 not_found` here: use the reasoned read. " + _REFUSALS,
        responses={200: OpenApiResponse(AdminListingDetailSerializer, description="The Listing")},
    )
    def get(self, request, listing_id):
        actor = get_actor(request)
        return _detail_response(actor, selectors.admin_listing(actor, listing_id))


class AdminDraftReadView(ReasonedReadMixin, _AdminView):
    @extend_schema(
        operation_id="read_admin_draft_listing",
        summary="Read a Draft's content, with a reason",
        description=(
            "Needs `X-CSRFToken`. A reasoned private read recorded in the audit trail. Images "
            "are counted, not served. " + _REFUSALS
        ),
        request=ReasonedReadSerializer,
        responses={200: OpenApiResponse(AdminListingDetailSerializer, description="The Draft")},
    )
    def post(self, request, listing_id):
        actor = get_actor(request)
        reason = self.read_reason(request)
        key = selectors.draft_id_for_read(actor, listing_id)
        detail = selectors.admin_read_draft_listing(actor, reason, key)
        return _detail_response(actor, detail, draft=True)


class AdminListingAddressSerializer(serializers.Serializer):
    """Private. All null when the Listing has no Private address."""

    street = serializers.CharField(allow_null=True)
    house_number = serializers.CharField(allow_null=True)
    latitude = serializers.CharField(allow_null=True)
    longitude = serializers.CharField(allow_null=True)


class AdminListingAddressReadView(ReasonedReadMixin, _AdminView):
    @extend_schema(
        operation_id="read_admin_listing_address",
        summary="Read a Listing's Private address, with a reason",
        description=(
            "Needs `X-CSRFToken`. A reasoned private read recorded in the audit trail, one "
            "Listing at a time. " + _REFUSALS
        ),
        request=ReasonedReadSerializer,
        responses={200: OpenApiResponse(AdminListingAddressSerializer, description="Private")},
    )
    def post(self, request, listing_id):
        actor = get_actor(request)
        reason = self.read_reason(request)
        key = selectors.listing_id_for_address_read(actor, listing_id)
        address = selectors.admin_read_listing_address(actor, reason, key)
        return Response(AdminListingAddressSerializer(asdict(address)).data)


def _moderate(request, listing_id, action: str) -> Response:
    reason = single_field(request.data, "reason")
    try:
        record = moderation.moderate(get_actor(request), listing_id, action, reason)
    except FieldsInvalid as exc:
        raise field_errors(exc.fields) from None
    return Response(ModerationRecordSerializer(record).data)


_ACTION = (
    "Needs `X-CSRFToken`. An unknown id is `404 not_found`; an action not open for this Listing "
    "now is `409 conflict`. The owner is notified with the reason. "
)


def _action_schema(operation_id: str, summary: str, extra: str = ""):
    return extend_schema(
        operation_id=operation_id,
        summary=summary,
        description=_ACTION + extra + _REFUSALS,
        request=ModerationReasonSerializer,
        responses={200: OpenApiResponse(ModerationRecordSerializer, description="Recorded")},
    )


class RestrictListingView(_AdminView):
    @_action_schema("restrict_admin_listing", "Restrict a published or archived Listing")
    def post(self, request, listing_id):
        return _moderate(request, listing_id, "restrict")


class LiftRestrictionView(_AdminView):
    @_action_schema("lift_admin_listing_restriction", "Lift a restriction")
    def post(self, request, listing_id):
        return _moderate(request, listing_id, "lift_restriction")


class RemoveListingView(_AdminView):
    @_action_schema("take_down_admin_listing", "Remove a published Listing")
    def post(self, request, listing_id):
        return _moderate(request, listing_id, "remove")


class RestoreListingView(_AdminView):
    @_action_schema(
        "restore_admin_listing",
        "Restore a removed Listing to published",
        "Consumes no publication; a restriction stays in force. ",
    )
    def post(self, request, listing_id):
        return _moderate(request, listing_id, "restore")


class ArchiveRemovedListingView(_AdminView):
    @_action_schema("archive_admin_listing", "Move a removed Listing to archived")
    def post(self, request, listing_id):
        return _moderate(request, listing_id, "archive_removed")
