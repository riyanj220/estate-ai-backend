"""Administrator quota policy and usage (Story 2.20). Rules live in `listings.quota_admin`."""

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.listings import quota_admin, selectors, services
from apps.presentation.api.actors import get_actor
from apps.presentation.api.authentication import SessionAuthentication
from apps.presentation.api.bodies import field_errors, single_field
from apps.presentation.api.listings import _link, _OffsetDateTimeField, _page_size
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireAdmin


class QuotaPolicySerializer(serializers.Serializer):
    account_type = serializers.ChoiceField(choices=list(quota_admin.ACCOUNT_TYPES))
    monthly_limit = serializers.IntegerField()
    effective_from = _OffsetDateTimeField(help_text="When this limit started, +05:00.")


class QuotaPolicyListSerializer(serializers.Serializer):
    items = QuotaPolicySerializer(many=True)


class QuotaLimitBodySerializer(serializers.Serializer):
    monthly_limit = serializers.IntegerField(min_value=0, max_value=quota_admin.MAX_MONTHLY_LIMIT)


class QuotaUsageSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    email = serializers.CharField(help_text="Masked, such as `j***@example.com`.")
    account_type = serializers.ChoiceField(choices=list(quota_admin.ACCOUNT_TYPES))
    used = serializers.IntegerField(help_text="Counted publications this Karachi month.")
    limit = serializers.IntegerField(help_text="The limit in force now for the account type.")


class QuotaUsagePageSerializer(serializers.Serializer):
    period_start = _OffsetDateTimeField(help_text="Start of the month, +05:00.")
    resets_at = _OffsetDateTimeField(help_text="Start of the next month, +05:00.")
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = QuotaUsageSerializer(many=True)


class _AdminQuotaView(NoStoreMixin, APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [RequireAdmin]


_REFUSALS = (
    "Visitors get `401 unauthenticated`; signed-in non-administrators get `404 not_found`. "
    "Never cached."
)


class QuotaPolicyView(_AdminQuotaView):
    @extend_schema(
        operation_id="get_admin_quota_policy",
        summary="The monthly publication limits in force now",
        description=_REFUSALS,
        responses={200: OpenApiResponse(QuotaPolicyListSerializer, description="The limits")},
    )
    def get(self, request):
        policies = quota_admin.current_policies(get_actor(request))
        return Response(QuotaPolicyListSerializer({"items": policies}).data)


class QuotaPolicyDetailView(_AdminQuotaView):
    @extend_schema(
        operation_id="set_admin_quota_limit",
        summary="Change one account type's monthly publication limit",
        description=(
            "Needs `X-CSRFToken`. Applies to later publication attempts only. `monthly_limit` is "
            "a whole number from 0 to 1000, else `400 validation_error`; the current limit again "
            "changes nothing (`200`, the unchanged row). An account type other than `individual` "
            "or `agency` is `404 not_found`. " + _REFUSALS
        ),
        request=QuotaLimitBodySerializer,
        responses={200: OpenApiResponse(QuotaPolicySerializer, description="The current limit")},
    )
    def put(self, request, account_type):
        value = single_field(request.data, "monthly_limit")
        try:
            policy = quota_admin.set_quota_limit(get_actor(request), account_type, value)
        except services.FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response(QuotaPolicySerializer(policy).data)


class QuotaUsageView(_AdminQuotaView):
    @extend_schema(
        operation_id="list_admin_quota_usage",
        summary="This month's publication usage by account",
        description=(
            "Accounts with at least one counted publication in the Asia/Karachi calendar month, "
            "newest usage first, with cursor pagination (`next` link, `previous` is always null; "
            "20 per page, `page_size` up to 50, an invalid `page_size` falls back to the "
            "default). A bad cursor, or one from an earlier month, is `validation_error` "
            "(`fields.cursor`). " + _REFUSALS
        ),
        parameters=[
            OpenApiParameter("page_size", int, required=False),
            OpenApiParameter("cursor", str, required=False),
        ],
        responses={200: OpenApiResponse(QuotaUsagePageSerializer, description="One page")},
    )
    def get(self, request):
        try:
            page = quota_admin.usage_by_account(
                get_actor(request),
                request.query_params.get("cursor") or None,  # an empty cursor is page one
                _page_size(request),
            )
        except selectors.InvalidCursor:  # a stable code, never the value
            raise field_errors({"cursor": "invalid"}) from None
        return Response(
            QuotaUsagePageSerializer(
                {
                    "period_start": page.period_start,
                    "resets_at": page.resets_at,
                    "next": _link(request, page.next_cursor),
                    "previous": None,
                    "results": page.items,
                }
            ).data
        )
