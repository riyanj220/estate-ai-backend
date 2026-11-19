"""Password reset and password change endpoints (Story 1.18). Rules live in `identity`."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import ErrorDetail
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.identity import services
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import exact_fields, field_errors, single_field
from apps.presentation.api.permissions import AllowPublic, RequireSignedIn
from apps.presentation.api.registrations import RegistrationAcceptedSerializer
from apps.presentation.api.throttling import PolicyThrottle


class NoStoreMixin:
    """Every answer of the view, errors included, is `no-store`."""

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.CharField(help_text="The address to send a reset link to.")

    def to_internal_value(self, data):
        value = single_field(data, "email")
        if not isinstance(value, str):
            raise serializers.ValidationError({"email": [ErrorDetail("invalid", code="invalid")]})
        return {"email": value}


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField(help_text="The token from the reset link.")
    password = serializers.CharField(
        help_text="The new password: 8 to 1024 characters, not common, not the email address."
    )

    def to_internal_value(self, data):
        values = exact_fields(data, ("token", "password"))
        if "token" not in values:
            raise serializers.ValidationError({"token": [ErrorDetail("required", code="required")]})
        # The password is judged only after the link is, so a missing or odd value is left to
        # the service (a field error appears for a valid link only).
        return {"token": values["token"], "password": values.get("password")}


class PasswordResetDoneSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["password_reset"])


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(help_text="The password in use now.")
    new_password = serializers.CharField(
        help_text="8 to 1024 characters, not common, not the email address, not the current one."
    )

    def to_internal_value(self, data):
        values = exact_fields(data, ("current_password", "new_password"))
        errors = {}
        for name in ("current_password", "new_password"):
            if name not in values:
                errors[name] = [ErrorDetail("required", code="required")]
            elif not isinstance(values[name], str):
                errors[name] = [ErrorDetail("invalid", code="invalid")]
        if errors:
            raise serializers.ValidationError(errors)
        return values


class PasswordChangedSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["password_changed"])


class PasswordResetRequestView(APIView):
    permission_classes = [AllowPublic]
    authentication_classes: list = []
    throttle_classes = [PolicyThrottle]
    rate_limit_scope = "password_reset_request"

    @extend_schema(
        operation_id="request_password_reset",
        summary="Request a password reset link",
        description=(
            "Always answers the same 202 whether or not the address is registered. The limit is "
            "per client and applies to every request alike (429 with `Retry-After`); a per-account "
            "cap is silent."
        ),
        request=PasswordResetRequestSerializer,
        responses={
            202: OpenApiResponse(RegistrationAcceptedSerializer, description="Check your email")
        },
    )
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.request_password_reset(get_actor(request), serializer.validated_data["email"])
        return Response({"status": "check_your_email"}, status=status.HTTP_202_ACCEPTED)


class PasswordResetConfirmView(NoStoreMixin, APIView):
    permission_classes = [AllowPublic]
    authentication_classes: list = []
    throttle_classes = [PolicyThrottle]
    rate_limit_scope = "password_reset_confirm"

    @extend_schema(
        operation_id="confirm_password_reset",
        summary="Set a new password with a reset link",
        description=(
            "POST, not GET, so link prefetchers cannot consume the single-use token. Link errors: "
            "`link_invalid`, `link_expired` (410), `link_used` (409). A password the policy "
            "refuses is `validation_error` with `fields.password` set, only for a valid link, and "
            "does not use up the link. On success every session of the account ends. Limited per "
            "client (429 with `Retry-After`)."
        ),
        request=PasswordResetConfirmSerializer,
        responses={200: OpenApiResponse(PasswordResetDoneSerializer, description="Password reset")},
    )
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.confirm_password_reset(
                get_actor(request),
                serializer.validated_data["token"],
                serializer.validated_data["password"],
            )
        except services.PasswordInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response({"status": "password_reset"}, status=status.HTTP_200_OK)


class ChangePasswordView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="change_password",
        summary="Change the signed-in account's password",
        description=(
            "Needs `X-CSRFToken`. A wrong current password is `validation_error` with "
            "`fields.current_password = incorrect` (after repeated failures the same answer is "
            "returned without checking; never 429). Ends every other session; the current one "
            "stays signed in."
        ),
        request=ChangePasswordSerializer,
        responses={200: OpenApiResponse(PasswordChangedSerializer, description="Password changed")},
    )
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.change_password(
                get_actor(request),
                getattr(request.auth, "id", None),
                serializer.validated_data["current_password"],
                serializer.validated_data["new_password"],
            )
        except services.PasswordInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response({"status": "password_changed"}, status=status.HTTP_200_OK)
