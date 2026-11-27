"""The signed-in account's closure request (Story 10.1). Rules live in `governance.closure`."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.governance import closure
from apps.governance.services import FieldsInvalid
from apps.presentation.api.actors import get_actor
from apps.presentation.api.authentication import SessionAuthentication
from apps.presentation.api.bodies import field_errors, single_field
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireSignedIn


class ClosureRequestSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=list(closure.KINDS))
    status = serializers.ChoiceField(choices=list(closure.STATUSES))
    submitted_at = serializers.DateTimeField()
    decided_at = serializers.DateTimeField(allow_null=True)
    decision_reason = serializers.CharField(allow_blank=True, help_text="Empty while pending.")


class OwnClosureSerializer(serializers.Serializer):
    can_submit = serializers.BooleanField()
    request = ClosureRequestSerializer(allow_null=True, help_text="The latest request.")


class ClosureSubmitSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=list(closure.KINDS))


class _OwnView(NoStoreMixin, APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [RequireSignedIn]


_REFUSALS = (
    "Individual and Agency accounts, Suspended ones included; an administrator is "
    "`403 forbidden`, a Visitor `401 unauthenticated`. Never cached."
)


class ClosureRequestsView(_OwnView):
    @extend_schema(
        operation_id="get_own_closure",
        summary="The caller's latest closure request",
        description=_REFUSALS,
        responses={200: OpenApiResponse(OwnClosureSerializer, description="Status")},
    )
    def get(self, request):
        return Response(OwnClosureSerializer(closure.own_closure(get_actor(request))).data)

    @extend_schema(
        operation_id="request_own_closure",
        summary="Ask for the account to be deactivated or deleted",
        description=(
            "Needs `X-CSRFToken`. The account stays as it is until an administrator decides; "
            "`409 conflict` while a request is pending. " + _REFUSALS
        ),
        request=ClosureSubmitSerializer,
        responses={201: OpenApiResponse(OwnClosureSerializer, description="Recorded")},
    )
    def post(self, request):
        kind = single_field(request.data, "kind")
        try:
            view = closure.request_closure(get_actor(request), kind)
        except FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response(OwnClosureSerializer(view).data, status=status.HTTP_201_CREATED)


class ClosureRequestDetailView(_OwnView):
    @extend_schema(
        operation_id="get_own_closure_request",
        summary="One of the caller's closure requests",
        description="Another account's or an unknown id is `404 not_found`. " + _REFUSALS,
        responses={200: OpenApiResponse(ClosureRequestSerializer, description="The request")},
    )
    def get(self, request, request_id):
        row = closure.own_closure_request(get_actor(request), request_id)
        return Response(ClosureRequestSerializer(row).data)
