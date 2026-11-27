"""The Agency's own Verification request and status (Story 8.3). Rules live in `governance`."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.governance import selectors, services
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import exact_fields, field_errors
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireSignedIn

OWN_STATUSES = ["unverified", *selectors.STATUSES]
_FIELDS = ("business_name", "registration_number", "note")


class OwnVerificationRequestSerializer(serializers.Serializer):
    business_name = serializers.CharField()
    registration_number = serializers.CharField(help_text="Private.")
    note = serializers.CharField(allow_blank=True, help_text="Private.")
    submitted_at = serializers.DateTimeField()
    decided_at = serializers.DateTimeField(allow_null=True)
    decision_reason = serializers.CharField(
        allow_blank=True, help_text="Why it was rejected or revoked; else empty."
    )


class OwnVerificationSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=OWN_STATUSES)
    can_submit = serializers.BooleanField()
    can_withdraw = serializers.BooleanField()
    request = OwnVerificationRequestSerializer(allow_null=True, help_text="The latest request.")


class VerificationSubmitSerializer(serializers.Serializer):
    business_name = serializers.CharField(min_length=2, max_length=services.BUSINESS_NAME_MAX)
    registration_number = serializers.CharField(
        min_length=services.NUMBER_MIN,
        max_length=services.NUMBER_MAX,
        help_text="Registration or licence number: letters, digits, spaces, `.`, `/`, `-`.",
    )
    note = serializers.CharField(required=False, allow_blank=True, max_length=services.NOTE_MAX)


_REFUSALS = "Agency accounts only: others get `403 forbidden`, a Visitor `401`. Never cached. "


class _OwnView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]


class AgencyVerificationView(_OwnView):
    @extend_schema(
        operation_id="get_agency_verification",
        summary="The signed-in Agency's Verification status and latest request",
        description=_REFUSALS + "A Suspended Agency may read.",
        responses={200: OpenApiResponse(OwnVerificationSerializer, description="The status")},
    )
    def get(self, request):
        view = selectors.own_verification(get_actor(request))
        return Response(OwnVerificationSerializer(view).data)


class AgencyVerificationRequestView(_OwnView):
    @extend_schema(
        operation_id="submit_agency_verification",
        summary="Request Verification",
        description=(
            _REFUSALS + "Needs `X-CSRFToken`. No document is uploaded. `409 conflict` while a "
            "request is pending or the Agency is already verified; a Suspended Agency is `403`."
        ),
        request=VerificationSubmitSerializer,
        responses={201: OpenApiResponse(OwnVerificationSerializer, description="Pending")},
    )
    def post(self, request):
        values = exact_fields(request.data, _FIELDS)
        try:
            view = services.submit_request(
                get_actor(request),
                values.get("business_name"),
                values.get("registration_number"),
                values.get("note"),
            )
        except services.FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response(OwnVerificationSerializer(view).data, status=status.HTTP_201_CREATED)


class AgencyVerificationWithdrawView(_OwnView):
    @extend_schema(
        operation_id="withdraw_agency_verification",
        summary="Withdraw the pending Verification request",
        description=_REFUSALS + "Needs `X-CSRFToken`. `409 conflict` when none is pending.",
        request=None,
        responses={200: OpenApiResponse(OwnVerificationSerializer, description="Withdrawn")},
    )
    def post(self, request):
        view = services.withdraw_request(get_actor(request))
        return Response(OwnVerificationSerializer(view).data)
