"""Reporting a Listing or a Conversation, and reading one's own Report (Stories 9.1 and 9.5).
Rules live in `governance`."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.governance import reports
from apps.governance.services import FieldsInvalid
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import exact_fields, field_errors
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireSignedIn

REASONS = list(reports.REASONS)
STATUSES = list(reports.STATUSES)
TARGETS = list(reports.TARGETS)


class ReportCreateSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(choices=REASONS)
    description = serializers.CharField(
        required=False, allow_blank=True, max_length=reports.DESCRIPTION_MAX
    )


class ConversationReportCreateSerializer(ReportCreateSerializer):
    message_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=reports.SELECTED_MAX,
        help_text="Messages of the other participant in this Conversation.",
    )


class OwnReportSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    target_type = serializers.ChoiceField(choices=TARGETS)
    listing_id = serializers.UUIDField()
    conversation_id = serializers.UUIDField(allow_null=True)
    title = serializers.CharField(allow_null=True, help_text="As it was when reported.")
    reason = serializers.ChoiceField(choices=REASONS)
    description = serializers.CharField(allow_blank=True)
    status = serializers.ChoiceField(choices=STATUSES)
    created_at = serializers.DateTimeField()
    closed_at = serializers.DateTimeField(allow_null=True)


class _View(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]


class ListingReportsView(_View):
    @extend_schema(
        operation_id="report_listing",
        summary="Report a publicly visible Listing",
        description=(
            "Needs `X-CSRFToken`. A Visitor is `401`; a Suspended account or an administrator "
            "`403 forbidden`; a Listing that is not public `404 not_found`; your own `403 "
            "forbidden`; one of yours already open `409 conflict`; at most 10 Reports a day "
            "(`429 throttled`). The Listing is not changed. Never cached."
        ),
        request=ReportCreateSerializer,
        responses={201: OpenApiResponse(OwnReportSerializer, description="Reported")},
    )
    def post(self, request, listing_id):
        values = exact_fields(request.data, ("reason", "description"))
        try:
            view = reports.report_listing(
                get_actor(request), listing_id, values.get("reason"), values.get("description")
            )
        except FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response(OwnReportSerializer(view).data, status=status.HTTP_201_CREATED)


class ConversationReportsView(_View):
    @extend_schema(
        operation_id="report_conversation",
        summary="Report Messages of a Conversation the caller takes part in",
        description=(
            "Needs `X-CSRFToken`. The Report keeps the selected Messages and up to 5 sent just "
            "before them. A Visitor is `401`; a Suspended account or an administrator `403 "
            "forbidden`; a Conversation the caller is not in `404 not_found`; an id that is not "
            "a Message of the other participant is a `message_ids` error; one of yours already "
            "open `409 conflict`; at most 10 Reports a day (`429 throttled`). Never cached."
        ),
        request=ConversationReportCreateSerializer,
        responses={201: OpenApiResponse(OwnReportSerializer, description="Reported")},
    )
    def post(self, request, conversation_id):
        values = exact_fields(request.data, ("message_ids", "reason", "description"))
        try:
            view = reports.report_conversation(
                get_actor(request),
                conversation_id,
                values.get("message_ids"),
                values.get("reason"),
                values.get("description"),
            )
        except FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response(OwnReportSerializer(view).data, status=status.HTTP_201_CREATED)


class OwnReportView(_View):
    @extend_schema(
        operation_id="get_own_report",
        summary="One of the caller's own Reports",
        description=(
            "Another account's, an unknown and a malformed id are `404 not_found`. The review "
            "notes are never shown. Never cached."
        ),
        responses={200: OpenApiResponse(OwnReportSerializer, description="The Report")},
    )
    def get(self, request, report_id):
        view = reports.own_report(get_actor(request), report_id)
        return Response(OwnReportSerializer(view).data)
