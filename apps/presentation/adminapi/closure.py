"""Administrator queue and decisions of account closure requests (Story 10.2)."""

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.governance import closure, services
from apps.presentation.api.actors import get_actor
from apps.presentation.api.authentication import SessionAuthentication
from apps.presentation.api.bodies import field_errors, single_field
from apps.presentation.api.listings import _link
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireAdmin

_STATUSES = list(closure.STATUSES)


class AdminClosureRequestSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=list(closure.KINDS))
    status = serializers.ChoiceField(choices=_STATUSES)
    account_id = serializers.UUIDField()
    account_type = serializers.CharField(allow_null=True)
    account_state = serializers.CharField(
        allow_null=True, help_text="`email_unverified`, `active`, `suspended` or `deactivated`."
    )
    masked_email = serializers.CharField(allow_null=True, help_text="Null once closed.")
    submitted_at = serializers.DateTimeField()
    decided_at = serializers.DateTimeField(allow_null=True)
    decision_reason = serializers.CharField(allow_blank=True)


class AdminClosureQueueSerializer(serializers.Serializer):
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = AdminClosureRequestSerializer(many=True)


class ClosureDecisionSerializer(serializers.Serializer):
    reason = serializers.CharField(
        min_length=services.REASON_MIN,
        max_length=services.REASON_MAX,
        help_text="One line, without contact details. The account holder reads it.",
    )


class _AdminView(NoStoreMixin, APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [RequireAdmin]


_REFUSALS = (
    "Visitors get `401 unauthenticated`; signed-in non-administrators get `404 not_found`. "
    "Never cached."
)


class ClosureQueueView(_AdminView):
    @extend_schema(
        operation_id="list_admin_closure_requests",
        summary="Closure requests in one status, oldest first",
        description=(
            "20 per page; `next` is a link, `previous` is always null. `status` defaults to "
            "`pending`. " + _REFUSALS
        ),
        parameters=[
            OpenApiParameter("status", str, required=False, enum=_STATUSES),
            OpenApiParameter("cursor", str, required=False),
        ],
        responses={200: OpenApiResponse(AdminClosureQueueSerializer, description="One page")},
    )
    def get(self, request):
        status = request.query_params.get("status") or "pending"
        if status not in _STATUSES:
            raise field_errors({"status": "invalid"})
        try:
            page = closure.review_queue(
                get_actor(request), status, request.query_params.get("cursor") or None
            )
        except closure.InvalidCursor:
            raise field_errors({"cursor": "invalid"}) from None
        return Response(
            AdminClosureQueueSerializer(
                {
                    "next": _link(request, page.next_cursor),
                    "previous": None,
                    "results": page.items,
                }
            ).data
        )


_DECISION = (
    "Needs `X-CSRFToken`. An unknown id is `404 not_found`; a decided request is `409 conflict`. "
)


def _decide(request, request_id, service) -> Response:
    reason = single_field(request.data, "reason")
    try:
        row = service(get_actor(request), request_id, reason)
    except services.FieldsInvalid as exc:
        raise field_errors(exc.fields) from None
    return Response(AdminClosureRequestSerializer(row).data)


class ClosureApproveView(_AdminView):
    @extend_schema(
        operation_id="approve_admin_closure_request",
        summary="Approve: deactivate and erase the account",
        description=(
            _DECISION + "Cannot be undone. In one transaction the account is deactivated and "
            "anonymised and its Listings archived; it is emailed. " + _REFUSALS
        ),
        request=ClosureDecisionSerializer,
        responses={200: OpenApiResponse(AdminClosureRequestSerializer, description="Approved")},
    )
    def post(self, request, request_id):
        return _decide(request, request_id, closure.approve_closure)


class ClosureRejectView(_AdminView):
    @extend_schema(
        operation_id="reject_admin_closure_request",
        summary="Reject: the account stays as it is",
        description=_DECISION + "The account is told in-app and by email. " + _REFUSALS,
        request=ClosureDecisionSerializer,
        responses={200: OpenApiResponse(AdminClosureRequestSerializer, description="Rejected")},
    )
    def post(self, request, request_id):
        return _decide(request, request_id, closure.reject_closure)
