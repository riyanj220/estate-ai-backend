"""Registration endpoints. Validation rules live in `identity`; this layer only shapes them."""

from collections.abc import Mapping

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import ErrorDetail
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.identity import services
from apps.identity.validation import validate_individual
from apps.kernel.errors import DomainError
from apps.presentation.api.actors import get_actor
from apps.presentation.api.permissions import AllowPublic
from apps.presentation.api.throttling import PolicyThrottle


def _invalid(fields: Mapping[str, str]) -> serializers.ValidationError:
    """Field errors keyed by field name, carrying only stable codes (never submitted values)."""
    return serializers.ValidationError(
        {field: [ErrorDetail(code, code=code)] for field, code in fields.items()}
    )


class IndividualRegistrationSerializer(serializers.Serializer):
    """Strict allowlist of the four fields. Rules come from `identity.validation`; every failing
    field is reported at once, and an unknown field (such as `role`) is refused."""

    full_name = serializers.CharField(
        help_text="2 to 80 letters, spaces, hyphens, apostrophes, full stops."
    )
    email = serializers.CharField(help_text="At most 254 characters.")
    password = serializers.CharField(
        help_text="8 to 1024 characters, not a common password, not the email address."
    )
    phone = serializers.CharField(
        help_text="Pakistani mobile (03xx xxxxxxx) or international number (+, 8 to 15 digits)."
    )

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError(
                {"non_field_errors": [ErrorDetail("not_a_dict", code="not_a_dict")]}
            )
        if set(data) - set(self.fields):
            raise serializers.ValidationError(
                {"non_field_errors": [ErrorDetail("unknown_field", code="unknown_field")]}
            )
        details, errors = validate_individual(
            full_name=data.get("full_name"),
            email=data.get("email"),
            password=data.get("password"),
            phone=data.get("phone"),
        )
        if details is None:
            raise _invalid(errors)
        return {
            "full_name": details.full_name,
            "email": details.email,
            "password": details.password,
            "phone": details.phone,
        }


class RegistrationAcceptedSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["check_your_email"])


class IndividualRegistrationView(APIView):
    permission_classes = [AllowPublic]
    authentication_classes: list = []
    throttle_classes = [PolicyThrottle]
    rate_limit_scope = "registration"

    @extend_schema(
        operation_id="register_individual",
        summary="Register an Individual account",
        description=(
            "The answer is the same whether or not the email is already registered; an existing "
            "address receives an 'already registered' notice by email instead."
        ),
        request=IndividualRegistrationSerializer,
        responses={
            202: OpenApiResponse(
                RegistrationAcceptedSerializer, description="Check your email to continue"
            )
        },
    )
    def post(self, request):
        actor = get_actor(request)
        if not actor.is_visitor:  # refuse before reading the body
            raise DomainError("forbidden")
        serializer = IndividualRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.register_individual(actor, **serializer.validated_data)
        except services.RegistrationInvalid as exc:
            raise _invalid(exc.fields) from None
        return Response({"status": "check_your_email"}, status=status.HTTP_202_ACCEPTED)
