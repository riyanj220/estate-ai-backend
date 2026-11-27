"""Administrator review of Agency Verification (Story 8.4). Rules live in `governance`.

The queue holds no private field. The registration number and note are a reasoned private read.
"""

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.governance import selectors, services
from apps.presentation.api.actors import get_actor
from apps.presentation.api.authentication import SessionAuthentication
from apps.presentation.api.bodies import field_errors, single_field
from apps.presentation.api.listings import _link
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireAdmin
from apps.presentation.api.private_reads import ReasonedReadMixin, ReasonedReadSerializer

_STATUSES = list(selectors.STATUSES)


class _AgencySummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=_STATUSES)
    business_name = serializers.CharField()
    agency_name = serializers.CharField(allow_null=True)
    agency_id = serializers.UUIDField(allow_null=True, help_text="The public Agency id.")
    account_state = serializers.CharField(
        allow_null=True, help_text="`email_unverified`, `active`, `suspended` or `deactivated`."
    )
    submitted_at = serializers.DateTimeField()


class VerificationQueueSerializer(serializers.Serializer):
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = _AgencySummarySerializer(many=True)


class VerificationHistorySerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["approved", "rejected", "revoked", "withdrawn"])
    at = serializers.DateTimeField()
    by_account_id = serializers.UUIDField(
        help_text="The administrator, or the Agency for `withdrawn`."
    )
    reason = serializers.CharField(allow_blank=True)


class VerificationDetailSerializer(_AgencySummarySerializer):
    registration_number = serializers.CharField(help_text="Private.")
    note = serializers.CharField(allow_blank=True, help_text="Private.")
    decided_at = serializers.DateTimeField(allow_null=True)
    decision_reason = serializers.CharField(allow_blank=True)
    history = VerificationHistorySerializer(many=True)


class VerificationDecisionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=_STATUSES)
    decided_at = serializers.DateTimeField()


class VerificationReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(
        min_length=services.REASON_MIN,
        max_length=services.REASON_MAX,
        help_text="One line, without contact details. The Agency reads it.",
    )


class _AdminView(NoStoreMixin, APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [RequireAdmin]


_REFUSALS = (
    "Visitors get `401 unauthenticated`; signed-in non-administrators get `404 not_found`. "
    "Never cached."
)


class VerificationQueueView(_AdminView):
    @extend_schema(
        operation_id="list_admin_verification_requests",
        summary="Verification requests in one status, oldest first",
        description=(
            "20 per page; `next` is a link, `previous` is always null. `status` defaults to "
            "`pending`; `verified` lists the approvals that can be revoked. " + _REFUSALS
        ),
        parameters=[
            OpenApiParameter("status", str, required=False, enum=_STATUSES),
            OpenApiParameter("cursor", str, required=False),
        ],
        responses={200: OpenApiResponse(VerificationQueueSerializer, description="One page")},
    )
    def get(self, request):
        status = request.query_params.get("status") or "pending"
        if status not in _STATUSES:
            raise field_errors({"status": "invalid"})
        try:
            page = selectors.review_queue(
                get_actor(request), status, request.query_params.get("cursor") or None
            )
        except selectors.InvalidCursor:
            raise field_errors({"cursor": "invalid"}) from None
        return Response(
            VerificationQueueSerializer(
                {
                    "next": _link(request, page.next_cursor),
                    "previous": None,
                    "results": page.items,
                }
            ).data
        )


class VerificationReadView(ReasonedReadMixin, _AdminView):
    @extend_schema(
        operation_id="read_admin_verification_request",
        summary="Read one request's submitted details and history, with a reason",
        description=(
            "Needs `X-CSRFToken`. A reasoned private read: `reason` (10 to 300 characters, one "
            "line, no contact details) is recorded in the audit trail. " + _REFUSALS
        ),
        request=ReasonedReadSerializer,
        responses={200: OpenApiResponse(VerificationDetailSerializer, description="The request")},
    )
    def post(self, request, request_id):
        actor = get_actor(request)
        reason = self.read_reason(request)
        key = selectors.request_id_for_read(actor, request_id)
        view = selectors.admin_read_verification_request(actor, reason, key)
        return Response(VerificationDetailSerializer(view).data)


def _decided(row) -> Response:
    return Response(VerificationDecisionSerializer(row).data)


_DECISION = (
    "Needs `X-CSRFToken`. An unknown id is `404 not_found`; a request in another status is "
    "`409 conflict`. The Agency is notified. "
)


class VerificationApproveView(_AdminView):
    @extend_schema(
        operation_id="approve_admin_verification_request",
        summary="Approve a pending request",
        description=_DECISION + "A Suspended or Deactivated Agency is `409`. " + _REFUSALS,
        request=None,
        responses={200: OpenApiResponse(VerificationDecisionSerializer, description="Verified")},
    )
    def post(self, request, request_id):
        return _decided(services.approve_request(get_actor(request), request_id))


def _with_reason(request, request_id, service) -> Response:
    reason = single_field(request.data, "reason")
    try:
        return _decided(service(get_actor(request), request_id, reason))
    except services.FieldsInvalid as exc:
        raise field_errors(exc.fields) from None


class VerificationRejectView(_AdminView):
    @extend_schema(
        operation_id="reject_admin_verification_request",
        summary="Reject a pending request, with a reason",
        description=_DECISION + _REFUSALS,
        request=VerificationReasonSerializer,
        responses={200: OpenApiResponse(VerificationDecisionSerializer, description="Rejected")},
    )
    def post(self, request, request_id):
        return _with_reason(request, request_id, services.reject_request)


class VerificationRevokeView(_AdminView):
    @extend_schema(
        operation_id="revoke_admin_verification",
        summary="Revoke an approval, with a reason",
        description=_DECISION + "The Agency's Listings stay published. " + _REFUSALS,
        request=VerificationReasonSerializer,
        responses={200: OpenApiResponse(VerificationDecisionSerializer, description="Revoked")},
    )
    def post(self, request, request_id):
        return _with_reason(request, request_id, services.revoke_verification)
