"""Administrator queue of support requests: reasoned read of one message, and closing (10.9)."""

from dataclasses import asdict

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.governance import selectors, support
from apps.identity import selectors as identity
from apps.kernel.errors import DomainError
from apps.presentation.api.actors import get_actor
from apps.presentation.api.authentication import SessionAuthentication
from apps.presentation.api.bodies import exact_fields, field_errors
from apps.presentation.api.listings import _link
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireAdmin
from apps.presentation.api.private_reads import ReasonedReadMixin, ReasonedReadSerializer

_STATUSES = list(support.STATUSES)


class AdminSupportRequestSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    category = serializers.ChoiceField(choices=list(support.CATEGORIES))
    status = serializers.ChoiceField(choices=_STATUSES)
    account_id = serializers.UUIDField()
    account_type = serializers.CharField(allow_null=True)
    account_state = serializers.CharField(
        allow_null=True, help_text="`email_unverified`, `active`, `suspended` or `deactivated`."
    )
    masked_email = serializers.CharField(allow_null=True, help_text="Null once closed.")
    submitted_at = serializers.DateTimeField()
    closed_at = serializers.DateTimeField(allow_null=True)


class AdminSupportMessageSerializer(AdminSupportRequestSerializer):
    message = serializers.CharField(allow_blank=True, help_text="Private. Empty once erased.")


class AdminSupportQueueSerializer(serializers.Serializer):
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = AdminSupportRequestSerializer(many=True)


class _AdminView(NoStoreMixin, APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [RequireAdmin]


_REFUSALS = (
    "Visitors get `401 unauthenticated`; signed-in non-administrators get `404 not_found`. "
    "Never cached."
)


class SupportQueueView(_AdminView):
    @extend_schema(
        operation_id="list_admin_support_requests",
        summary="Support requests in one status, oldest first, without their text",
        description=(
            "20 per page; `next` is a link, `previous` is always null. `status` defaults to "
            "`open`. " + _REFUSALS
        ),
        parameters=[
            OpenApiParameter("status", str, required=False, enum=_STATUSES),
            OpenApiParameter("cursor", str, required=False),
        ],
        responses={200: OpenApiResponse(AdminSupportQueueSerializer, description="One page")},
    )
    def get(self, request):
        status = request.query_params.get("status") or "open"
        if status not in _STATUSES:
            raise field_errors({"status": "invalid"})
        try:
            page = support.review_queue(
                get_actor(request), status, request.query_params.get("cursor") or None
            )
        except support.InvalidCursor:
            raise field_errors({"cursor": "invalid"}) from None
        return Response(
            AdminSupportQueueSerializer(
                {
                    "next": _link(request, page.next_cursor),
                    "previous": None,
                    "results": page.items,
                }
            ).data
        )


class SupportReadView(ReasonedReadMixin, _AdminView):
    @extend_schema(
        operation_id="read_admin_support_request",
        summary="Read one support request's message, with a recorded reason",
        description=(
            "Needs `X-CSRFToken`. Every read is audited with its reason. An unknown id is "
            "`404 not_found`. " + _REFUSALS
        ),
        request=ReasonedReadSerializer,
        responses={200: OpenApiResponse(AdminSupportMessageSerializer, description="The text")},
    )
    def post(self, request, request_id):
        actor = get_actor(request)
        key = identity.parse_id(request_id)
        if key is None:
            raise DomainError("not_found")
        row = support.queue_row(actor, key)
        message = selectors.admin_read_support_request(actor, self.read_reason(request), key)
        return Response(AdminSupportMessageSerializer({**asdict(row), "message": message}).data)


class SupportCloseView(_AdminView):
    @extend_schema(
        operation_id="close_admin_support_request",
        summary="Close a support request and notify its author",
        description=(
            "Needs `X-CSRFToken` and an empty body. An unknown id is `404 not_found`; a closed "
            "request is `409 conflict`. " + _REFUSALS
        ),
        request=None,
        responses={200: OpenApiResponse(AdminSupportRequestSerializer, description="Closed")},
    )
    def post(self, request, request_id):
        if request.data:
            exact_fields(request.data, ())
        row = support.close_request(get_actor(request), request_id)
        return Response(AdminSupportRequestSerializer(row).data)
