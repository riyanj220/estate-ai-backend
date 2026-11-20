"""Individual profile and email change endpoints (Story 1.20). Rules live in `identity`."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import ErrorDetail
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.identity import services
from apps.identity.selectors import ProfileView
from apps.presentation.api.actors import get_actor
from apps.presentation.api.bodies import exact_fields, field_errors, single_field
from apps.presentation.api.passwords import NoStoreMixin
from apps.presentation.api.permissions import AllowPublic, RequireSignedIn
from apps.presentation.api.registrations import RegistrationAcceptedSerializer
from apps.presentation.api.throttling import PolicyThrottle


class ProfileSerializer(serializers.Serializer):
    full_name = serializers.CharField()
    phone = serializers.CharField(
        help_text="The owner's own phone number in E.164. Private: returned only here."
    )
    email = serializers.CharField(help_text="The current sign-in address.")
    pending_email = serializers.CharField(
        allow_null=True,
        help_text="The address of the newest unused, unexpired email-change request, or null.",
    )


class ProfileUpdateSerializer(serializers.Serializer):
    full_name = serializers.CharField(required=False, help_text="2 to 80 characters.")
    phone = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="Pakistani mobile or international number. Mandatory: blank or null is refused.",
    )

    def to_internal_value(self, data):
        values = exact_fields(data, ("full_name", "phone"))
        if not values:
            raise serializers.ValidationError(
                {"non_field_errors": [ErrorDetail("empty", code="empty")]}
            )
        return values  # types and rules are judged by the service, all fields at once


class EmailChangeSerializer(serializers.Serializer):
    new_email = serializers.CharField(help_text="The address to sign in with after confirming.")
    current_password = serializers.CharField(help_text="The password in use now.")

    def to_internal_value(self, data):
        values = exact_fields(data, ("new_email", "current_password"))
        errors = {}
        for name in ("new_email", "current_password"):
            if name not in values:
                errors[name] = [ErrorDetail("required", code="required")]
            elif not isinstance(values[name], str):
                errors[name] = [ErrorDetail("invalid", code="invalid")]
        if errors:
            raise serializers.ValidationError(errors)
        return values


class EmailChangeConfirmSerializer(serializers.Serializer):
    token = serializers.CharField(help_text="The token from the email-change link.")

    def to_internal_value(self, data):
        # A wrong type or size is a bad link, not a bad body.
        return {"token": single_field(data, "token")}


class EmailChangedSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["email_changed"])


def _profile_data(view: ProfileView) -> dict:
    return ProfileSerializer(
        {
            "full_name": view.full_name,
            "phone": view.phone,
            "email": view.email,
            "pending_email": view.pending_email,
        }
    ).data


class ProfileDetailView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="get_profile",
        summary="The signed-in Individual's profile",
        description=(
            "Individual accounts only (any other type is `403 forbidden`). The phone is private "
            "and appears only in this route's answers, to its owner. Never cached."
        ),
        responses={200: OpenApiResponse(ProfileSerializer, description="The profile")},
    )
    def get(self, request):
        return Response(_profile_data(services.get_profile(get_actor(request))))

    @extend_schema(
        operation_id="update_profile",
        summary="Change the signed-in Individual's name and/or phone",
        description=(
            "Needs `X-CSRFToken`. Only `full_name` and `phone` are accepted, at least one; any "
            "other field (the email included) is `validation_error` (`unknown_field`). The phone "
            "stays mandatory: blank or null is `fields.phone = required`. All failing fields are "
            "reported at once. An unchanged submission succeeds and records nothing."
        ),
        request=ProfileUpdateSerializer,
        responses={200: OpenApiResponse(ProfileSerializer, description="The updated profile")},
    )
    def patch(self, request):
        services.get_profile(get_actor(request))  # type refusal (403) comes before body checks
        serializer = ProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            view = services.update_profile(get_actor(request), serializer.validated_data)
        except services.FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response(_profile_data(view))


class EmailChangeRequestView(NoStoreMixin, APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="request_email_change",
        summary="Ask to change the sign-in email",
        description=(
            "Needs `X-CSRFToken`; not available to administrators. A wrong current password is "
            "`validation_error` with `fields.current_password = incorrect` (shared with the "
            "password change: after repeated failures the same answer returns without checking; "
            "never 429). A malformed address is `fields.new_email = invalid_email`. Otherwise "
            "ALWAYS the same `202`, whether the address is free, registered, the current one or "
            "unusable. A single-use link goes only to a free new address, after commit; the "
            "current address keeps signing in until it is used. Every accepted request also "
            "sends a heads-up to the current address. Only requests with the right password "
            "count against the per-account hourly limit (429 with `Retry-After`)."
        ),
        request=EmailChangeSerializer,
        responses={
            202: OpenApiResponse(RegistrationAcceptedSerializer, description="Check your email")
        },
    )
    def post(self, request):
        serializer = EmailChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.request_email_change(
                get_actor(request),
                serializer.validated_data["new_email"],
                serializer.validated_data["current_password"],
            )
        except services.FieldsInvalid as exc:
            raise field_errors(exc.fields) from None
        return Response({"status": "check_your_email"}, status=status.HTTP_202_ACCEPTED)


class EmailChangeConfirmView(NoStoreMixin, APIView):
    permission_classes = [AllowPublic]
    authentication_classes: list = []
    throttle_classes = [PolicyThrottle]
    rate_limit_scope = "email_change_confirm"

    @extend_schema(
        operation_id="confirm_email_change",
        summary="Confirm an email-change link",
        description=(
            "POST, not GET, so link prefetchers cannot consume the single-use token. Public: no "
            "session or CSRF token needed, so the link works in any browser. Errors: "
            "`link_invalid` (also when the address was taken meanwhile), `link_expired` (410), "
            "`link_used` (409). On success the sign-in address is replaced and verified; "
            "sessions stay open. Limited per client (429 with `Retry-After`)."
        ),
        request=EmailChangeConfirmSerializer,
        responses={200: OpenApiResponse(EmailChangedSerializer, description="Email changed")},
    )
    def post(self, request):
        serializer = EmailChangeConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.confirm_email_change(get_actor(request), serializer.validated_data["token"])
        return Response({"status": "email_changed"}, status=status.HTTP_200_OK)
