"""The signed-in account's support requests (Story 10.9). Rules live in `governance.support`."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.governance import support
from apps.governance.services import FieldsInvalid
from apps.presentation.api.actors import get_actor
from apps.presentation.api.authentication import SessionAuthentication
from apps.presentation.api.bodies import exact_fields, field_errors
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireSignedIn


class SupportRequestSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    category = serializers.ChoiceField(choices=list(support.CATEGORIES))
    message = serializers.CharField(allow_blank=True)
    status = serializers.ChoiceField(choices=list(support.STATUSES))
    submitted_at = serializers.DateTimeField()
    closed_at = serializers.DateTimeField(allow_null=True)


class OwnSupportSerializer(serializers.Serializer):
    results = SupportRequestSerializer(many=True, help_text="The latest 20, newest first.")


class SupportSubmitSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=list(support.CATEGORIES))
    message = serializers.CharField(min_length=support.MESSAGE_MIN, max_length=support.MESSAGE_MAX)


class _OwnView(NoStoreMixin, APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [RequireSignedIn]


_REFUSALS = (
    "Individual and Agency accounts, Suspended ones included; an administrator is "
    "`403 forbidden`, a Visitor `401 unauthenticated`. Never cached."
)


class SupportRequestsView(_OwnView):
    @extend_schema(
        operation_id="list_own_support_requests",
        summary="The caller's support requests",
        description=_REFUSALS,
        responses={200: OpenApiResponse(OwnSupportSerializer, description="Requests")},
    )
    def get(self, request):
        rows = support.own_requests(get_actor(request))
        return Response(OwnSupportSerializer({"results": rows}).data)

    @extend_schema(
        operation_id="submit_support_request",
        summary="Write to the platform team",
        description=(
            "Needs `X-CSRFToken`. At most 5 requests a day (`429 throttled`). " + _REFUSALS
        ),
        request=SupportSubmitSerializer,
        responses={201: OpenApiResponse(SupportRequestSerializer, description="Recorded")},
    )
    def post(self, request):
        values = exact_fields(request.data, ("category", "message"))
        try:
            view = support.submit_request(
                get_actor(request), values.get("category"), values.get("message")
            )
        except FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response(SupportRequestSerializer(view).data, status=status.HTTP_201_CREATED)


class SupportRequestDetailView(_OwnView):
    @extend_schema(
        operation_id="get_own_support_request",
        summary="One of the caller's support requests",
        description="Another account's or an unknown id is `404 not_found`. " + _REFUSALS,
        responses={200: OpenApiResponse(SupportRequestSerializer, description="The request")},
    )
    def get(self, request, request_id):
        row = support.own_request(get_actor(request), request_id)
        return Response(SupportRequestSerializer(row).data)
