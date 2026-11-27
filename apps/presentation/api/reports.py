"""Reporting a Listing and reading one's own Report (Story 9.1). Rules live in `governance`."""

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


class ReportCreateSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(choices=REASONS)
    description = serializers.CharField(
        required=False, allow_blank=True, max_length=reports.DESCRIPTION_MAX
    )


class OwnReportSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    listing_id = serializers.UUIDField()
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
