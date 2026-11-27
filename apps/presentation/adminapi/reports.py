"""Administrator review of Reports (Stories 9.2 and 9.5). Rules live in `governance`.

A Listing Report's snapshot holds public content only, so it is a plain read. A Conversation
Report's Messages and description come only from the reasoned POST `.../read`.
"""

from dataclasses import replace

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.governance import reports, selectors
from apps.governance.services import FieldsInvalid
from apps.presentation.api.actors import get_actor
from apps.presentation.api.authentication import SessionAuthentication
from apps.presentation.api.bodies import exact_fields, field_errors
from apps.presentation.api.listings import _link
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireAdmin
from apps.presentation.api.private_reads import ReasonedReadMixin, ReasonedReadSerializer
from apps.presentation.api.reports import REASONS, STATUSES, TARGETS


class _RowSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    target_type = serializers.ChoiceField(choices=TARGETS)
    listing_id = serializers.UUIDField()
    title = serializers.CharField(allow_null=True, help_text="As it was when reported.")
    reason = serializers.ChoiceField(choices=REASONS)
    status = serializers.ChoiceField(choices=STATUSES)
    created_at = serializers.DateTimeField()


class _UnresolvedSerializer(serializers.Serializer):
    pending = serializers.IntegerField()
    reviewing = serializers.IntegerField()


class ReportQueueSerializer(serializers.Serializer):
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = _RowSerializer(many=True)
    unresolved = _UnresolvedSerializer()


class AccountRefSerializer(serializers.Serializer):
    type = serializers.CharField()
    masked_email = serializers.CharField()


class ReportSnapshotSerializer(serializers.Serializer):
    """The public view of the Listing when it was reported."""

    purpose = serializers.CharField()
    category = serializers.CharField()
    subtype = serializers.CharField()
    title = serializers.CharField(allow_null=True)
    description = serializers.CharField(allow_null=True)
    city = serializers.CharField(allow_null=True)
    locality = serializers.CharField(allow_null=True)
    image_count = serializers.IntegerField()


# `fields` is the serializer's own property, so this key is declared after the class body.
ReportSnapshotSerializer._declared_fields["fields"] = serializers.DictField(
    help_text="Public registry fields that had a value."
)


class ReportMoveSerializer(serializers.Serializer):
    to_status = serializers.ChoiceField(choices=["reviewing", "resolved", "dismissed"])
    notes = serializers.CharField(allow_blank=True)
    at = serializers.DateTimeField()
    by_account_id = serializers.UUIDField()


class ReportedMessageSerializer(serializers.Serializer):
    seq = serializers.IntegerField()
    sender = serializers.ChoiceField(choices=["reporter", "reported", reports.DELETED_SENDER])
    selected = serializers.BooleanField(help_text="Reported; the others are context.")
    body = serializers.CharField()
    sent_at = serializers.DateTimeField()


class ReportDetailSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    target_type = serializers.ChoiceField(choices=TARGETS)
    listing_id = serializers.UUIDField()
    conversation_id = serializers.UUIDField(allow_null=True)
    reason = serializers.ChoiceField(choices=REASONS)
    status = serializers.ChoiceField(choices=STATUSES)
    description = serializers.CharField(
        allow_blank=True, help_text="Empty for a Conversation Report until it is read."
    )
    snapshot = ReportSnapshotSerializer(allow_null=True, help_text="Listing Reports only.")
    messages = ReportedMessageSerializer(
        many=True,
        allow_null=True,
        help_text="Conversation Reports, and only from the reasoned read.",
    )
    created_at = serializers.DateTimeField()
    closed_at = serializers.DateTimeField(allow_null=True)
    reporter = AccountRefSerializer(allow_null=True)
    owner = AccountRefSerializer(allow_null=True, help_text="The reported account.")
    owner_account_id = serializers.UUIDField()
    history = ReportMoveSerializer(many=True)
    open_for_listing = serializers.IntegerField(help_text="Open Reports on this target.")


class ReportNotesSerializer(serializers.Serializer):
    notes = serializers.CharField(
        min_length=reports.NOTES_MIN, max_length=reports.NOTES_MAX, help_text="Administrators only."
    )


class ReportStartSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, max_length=reports.NOTES_MAX)


class _AdminView(NoStoreMixin, APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [RequireAdmin]


_REFUSALS = (
    "Visitors get `401 unauthenticated`; signed-in non-administrators get `404 not_found`. "
    "Never cached."
)


def _detail(view) -> Response:
    return Response(ReportDetailSerializer(view).data)


class ReportQueueView(_AdminView):
    @extend_schema(
        operation_id="list_admin_reports",
        summary="Reports in one status, oldest first, with unresolved counts",
        description=(
            "20 per page; `next` is a link, `previous` is always null. `status` defaults to "
            "`pending`. " + _REFUSALS
        ),
        parameters=[
            OpenApiParameter("status", str, required=False, enum=STATUSES),
            OpenApiParameter("reason", str, required=False, enum=REASONS),
            OpenApiParameter("cursor", str, required=False),
        ],
        responses={200: OpenApiResponse(ReportQueueSerializer, description="One page")},
    )
    def get(self, request):
        status = request.query_params.get("status") or "pending"
        reason = request.query_params.get("reason") or None
        if status not in STATUSES:
            raise field_errors({"status": "invalid"})
        if reason is not None and reason not in REASONS:
            raise field_errors({"reason": "invalid"})
        try:
            page = reports.report_queue(
                get_actor(request), status, reason, request.query_params.get("cursor") or None
            )
        except reports.InvalidCursor:
            raise field_errors({"cursor": "invalid"}) from None
        return Response(
            ReportQueueSerializer(
                {
                    "next": _link(request, page.next_cursor),
                    "previous": None,
                    "results": page.items,
                    "unresolved": {"pending": page.pending, "reviewing": page.reviewing},
                }
            ).data
        )


class ReportDetailView(_AdminView):
    @extend_schema(
        operation_id="get_admin_report",
        summary="One Report with its snapshot and review history",
        description=_REFUSALS,
        responses={200: OpenApiResponse(ReportDetailSerializer, description="The Report")},
    )
    def get(self, request, report_id):
        return _detail(reports.report_detail(get_actor(request), report_id))


class ReportReadView(ReasonedReadMixin, _AdminView):
    @extend_schema(
        operation_id="read_admin_report_messages",
        summary="Read the reported Messages of a Conversation Report, with a recorded reason",
        description=(
            "Needs `X-CSRFToken`. Every read is audited with its reason. Returns the reported "
            "Messages and the few before them, never the rest of the Conversation. A Listing "
            "Report or an unknown id is `404 not_found`. " + _REFUSALS
        ),
        request=ReasonedReadSerializer,
        responses={200: OpenApiResponse(ReportDetailSerializer, description="With Messages")},
    )
    def post(self, request, report_id):
        actor = get_actor(request)
        key = reports.conversation_report_id(actor, report_id)
        content = selectors.admin_read_conversation_report(actor, self.read_reason(request), key)
        return _detail(
            replace(
                reports.report_detail(actor, key),
                description=content.description,
                messages=content.messages,
            )
        )


def _move(request, report_id, service) -> Response:
    values = exact_fields(request.data, ("notes",))
    try:
        return _detail(service(get_actor(request), report_id, values.get("notes")))
    except FieldsInvalid as exc:
        raise field_errors(exc.fields) from None


_MOVE = (
    "Needs `X-CSRFToken`. An unknown id is `404 not_found`; a Report in another status is `409 "
    "conflict`. The Listing is not changed. "
)


class ReportStartView(_AdminView):
    @extend_schema(
        operation_id="start_admin_report_review",
        summary="Start reviewing a pending Report",
        description=_MOVE + "The Listing shows as under review. " + _REFUSALS,
        request=ReportStartSerializer,
        responses={200: OpenApiResponse(ReportDetailSerializer, description="Reviewing")},
    )
    def post(self, request, report_id):
        return _move(request, report_id, reports.start_review)


class ReportResolveView(_AdminView):
    @extend_schema(
        operation_id="resolve_admin_report",
        summary="Resolve a Report being reviewed, with notes",
        description=_MOVE + "The reporter is notified. " + _REFUSALS,
        request=ReportNotesSerializer,
        responses={200: OpenApiResponse(ReportDetailSerializer, description="Resolved")},
    )
    def post(self, request, report_id):
        return _move(request, report_id, reports.resolve_report)


class ReportDismissView(_AdminView):
    @extend_schema(
        operation_id="dismiss_admin_report",
        summary="Dismiss a Report being reviewed, with notes",
        description=_MOVE + "The reporter is notified. " + _REFUSALS,
        request=ReportNotesSerializer,
        responses={200: OpenApiResponse(ReportDetailSerializer, description="Dismissed")},
    )
    def post(self, request, report_id):
        return _move(request, report_id, reports.dismiss_report)
