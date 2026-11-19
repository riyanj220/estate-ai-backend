"""Email verification endpoints: confirm a link, request a new one."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import ErrorDetail
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.identity import services
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import single_field
from apps.presentation.api.permissions import AllowPublic
from apps.presentation.api.registrations import RegistrationAcceptedSerializer
from apps.presentation.api.throttling import PolicyThrottle


class ConfirmEmailSerializer(serializers.Serializer):
    token = serializers.CharField(help_text="The token from the verification link.")

    def to_internal_value(self, data):
        # A wrong type or size is a bad link, not a bad body.
        return {"token": single_field(data, "token")}


class VerificationRequestSerializer(serializers.Serializer):
    email = serializers.CharField(help_text="The address to send a new link to.")

    def to_internal_value(self, data):
        value = single_field(data, "email")
        if not isinstance(value, str):
            raise serializers.ValidationError({"email": [ErrorDetail("invalid", code="invalid")]})
        return {"email": value}


class VerifiedSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["verified"])


class ConfirmEmailView(APIView):
    permission_classes = [AllowPublic]
    authentication_classes: list = []

    @extend_schema(
        operation_id="confirm_email_verification",
        summary="Confirm an email verification link",
        description=(
            "POST, not GET, so link prefetchers cannot consume the single-use token. Errors: "
            "`link_invalid`, `link_expired` (410), `link_used` (409)."
        ),
        request=ConfirmEmailSerializer,
        responses={200: OpenApiResponse(VerifiedSerializer, description="Email verified")},
    )
    def post(self, request):
        serializer = ConfirmEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.confirm_email(get_actor(request), serializer.validated_data["token"])
        return Response(
            {"status": "verified"}, status=status.HTTP_200_OK, headers={"Cache-Control": "no-store"}
        )


class VerificationRequestView(APIView):
    permission_classes = [AllowPublic]
    authentication_classes: list = []
    throttle_classes = [PolicyThrottle]
    rate_limit_scope = "verification_resend"

    @extend_schema(
        operation_id="request_email_verification",
        summary="Request a new verification link",
        description=(
            "Always answers the same 202 whether or not the address is registered; the limit "
            "applies to every request alike."
        ),
        request=VerificationRequestSerializer,
        responses={
            202: OpenApiResponse(RegistrationAcceptedSerializer, description="Check your email")
        },
    )
    def post(self, request):
        serializer = VerificationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.request_verification_link(get_actor(request), serializer.validated_data["email"])
        return Response({"status": "check_your_email"}, status=status.HTTP_202_ACCEPTED)
