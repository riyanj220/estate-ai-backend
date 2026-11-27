"""The signed-in administrator's second factor (Story 10.10). Rules live in `identity.services`."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.identity import services
from apps.presentation.api.actors import get_actor
from apps.presentation.api.authentication import SessionAuthentication
from apps.presentation.api.bodies import field_errors, single_field
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import RequireAdmin


class SecondFactorSerializer(serializers.Serializer):
    enrolled = serializers.BooleanField()


class SecondFactorStartSerializer(serializers.Serializer):
    password = serializers.CharField(help_text="The administrator's current password.")


class SecondFactorSecretSerializer(serializers.Serializer):
    secret = serializers.CharField(help_text="Base32. Shown only now.")
    otpauth_uri = serializers.CharField(help_text="The same secret as an `otpauth://` URI.")


class SecondFactorConfirmSerializer(serializers.Serializer):
    code = serializers.CharField(help_text="Six digits from the authenticator.")


class _AdminView(NoStoreMixin, APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [RequireAdmin]


_REFUSALS = (
    "Visitors get `401 unauthenticated`; signed-in non-administrators get `404 not_found`. "
    "Never cached."
)
_ENROLLING = (
    "Needs `X-CSRFToken`. After 5 wrong attempts in 15 minutes the answer is `429 throttled`. "
)


class SecondFactorView(_AdminView):
    @extend_schema(
        operation_id="get_admin_second_factor",
        summary="Whether the caller has a second factor",
        description=_REFUSALS,
        responses={200: OpenApiResponse(SecondFactorSerializer, description="Status")},
    )
    def get(self, request):
        enrolled = services.second_factor_enrolled(get_actor(request))
        return Response(SecondFactorSerializer({"enrolled": enrolled}).data)


class SecondFactorStartView(_AdminView):
    @extend_schema(
        operation_id="start_admin_second_factor",
        summary="Get a new second-factor secret (not active until confirmed)",
        description=_ENROLLING + "`409 conflict` when already enrolled. " + _REFUSALS,
        request=SecondFactorStartSerializer,
        responses={200: OpenApiResponse(SecondFactorSecretSerializer, description="The secret")},
    )
    def post(self, request):
        password = single_field(request.data, "password")
        try:
            enrolment = services.start_second_factor(get_actor(request), password)
        except services.FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response(SecondFactorSecretSerializer(enrolment).data)


class SecondFactorConfirmView(_AdminView):
    @extend_schema(
        operation_id="confirm_admin_second_factor",
        summary="Turn the second factor on with a first code",
        description=(
            _ENROLLING + "From now on sign-in needs a code; other sessions end. "
            "`409 conflict` when no secret is pending. " + _REFUSALS
        ),
        request=SecondFactorConfirmSerializer,
        responses={200: OpenApiResponse(SecondFactorSerializer, description="Enrolled")},
    )
    def post(self, request):
        code = single_field(request.data, "code")
        try:
            services.confirm_second_factor(
                get_actor(request), getattr(request.auth, "id", None), code
            )
        except services.FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response(SecondFactorSerializer({"enrolled": True}).data)
