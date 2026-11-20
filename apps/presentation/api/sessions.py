"""Sign-in, sign-out, the signed-in actor summary and the CSRF token (Story 1.17)."""

from collections.abc import Mapping

from django.middleware.csrf import get_token, rotate_token
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import ErrorDetail
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.identity import selectors, services
from apps.identity.capabilities import Capability
from apps.identity.selectors import ACCOUNT_TYPES
from apps.identity.sessions import COOKIE_NAME, absolute_lifetime
from apps.identity.state import AccountState
from apps.kernel import ratelimit
from apps.kernel.errors import DomainError
from apps.presentation.api.actors import get_actor
from apps.presentation.api.authentication import cookie_kwargs, enforce_csrf
from apps.presentation.api.exceptions import error_response
from apps.presentation.api.permissions import AllowPublic, RequireSignedIn

NO_STORE = {"Cache-Control": "no-store"}
_EPOCH = "Thu, 01 Jan 1970 00:00:00 GMT"


class SignInSerializer(serializers.Serializer):
    """Exactly `email` and `password`, both strings. Nothing else is accepted."""

    email = serializers.CharField(help_text="The account email; matched case-insensitively.")
    password = serializers.CharField(help_text="The account password.")

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError(
                {"non_field_errors": [ErrorDetail("not_a_dict", code="not_a_dict")]}
            )
        if set(data) - set(self.fields):
            raise serializers.ValidationError(
                {"non_field_errors": [ErrorDetail("unknown_field", code="unknown_field")]}
            )
        errors = {}
        for name in self.fields:
            if name not in data:
                errors[name] = [ErrorDetail("required", code="required")]
            elif not isinstance(data[name], str):
                errors[name] = [ErrorDetail("invalid", code="invalid")]
        if errors:
            raise serializers.ValidationError(errors)
        return {name: data[name] for name in self.fields}


class ActorSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    account_type = serializers.ChoiceField(choices=ACCOUNT_TYPES)
    state = serializers.ChoiceField(choices=[s.value for s in AccountState])
    email = serializers.CharField(help_text="The account's own email. Private: owner only.")
    full_name = serializers.CharField(help_text="Individual profile name; empty when none.")
    agency_name = serializers.CharField(
        help_text="Agency name (public); empty for non-agency accounts."
    )
    capabilities = serializers.ListField(child=serializers.ChoiceField(choices=list(Capability)))


class CsrfTokenSerializer(serializers.Serializer):
    csrf_token = serializers.CharField()


def _summary_data(summary: selectors.ActorSummary) -> dict:
    return ActorSummarySerializer(
        {
            "id": summary.id,
            "account_type": summary.account_type,
            "state": summary.state.value,
            "email": summary.email,
            "full_name": summary.full_name,
            "agency_name": summary.agency_name,
            "capabilities": list(summary.capabilities),
        }
    ).data


def _clear_cookie(response) -> None:
    response.set_cookie(COOKIE_NAME, "", max_age=0, expires=_EPOCH, **cookie_kwargs())


class SessionView(APIView):
    """One path, two methods. The route permission is public because sign-in is; `DELETE` is
    gated by `services.sign_out` (signed-in) and by the authentication class's CSRF check."""

    permission_classes = [AllowPublic]

    @extend_schema(
        operation_id="sign_in",
        summary="Sign in",
        description=(
            "Needs an `X-CSRFToken` header (fetch it from `GET /api/v1/csrf`). On success sets the "
            "HttpOnly `estate_session` cookie. Wrong password, unknown email, a Deactivated "
            "account and a throttled attempt all answer the same `401 invalid_credentials`."
        ),
        request=SignInSerializer,
        responses={200: OpenApiResponse(ActorSummarySerializer, description="Signed in")},
    )
    def post(self, request):
        enforce_csrf(request)
        serializer = SignInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.sign_in(
            get_actor(request),
            serializer.validated_data["email"],
            serializer.validated_data["password"],
            ratelimit.client_network(request),
            request.COOKIES.get(COOKIE_NAME),
        )
        rotate_token(request._request)  # the pre-login token must not survive the login
        response = Response(_summary_data(result.summary), headers=NO_STORE)
        response.set_cookie(
            COOKIE_NAME,
            result.token,
            max_age=int(absolute_lifetime().total_seconds()),
            **cookie_kwargs(),
        )
        return response

    @extend_schema(
        operation_id="sign_out",
        summary="Sign out",
        description=(
            "Ends only the current session and clears the cookie. Needs `X-CSRFToken`. "
            "Without a valid session the answer is `401 unauthenticated`."
        ),
        request=None,
        responses={204: OpenApiResponse(description="Signed out")},
    )
    def delete(self, request):
        try:
            services.sign_out(get_actor(request), getattr(request.auth, "id", None))
        except DomainError as exc:
            if exc.code != "unauthenticated":
                raise
            # A dead or missing session: answer 401 and still drop the stale cookie.
            response = error_response("unauthenticated", request, headers=NO_STORE)
            _clear_cookie(response)
            return response
        rotate_token(request._request)
        response = Response(status=status.HTTP_204_NO_CONTENT, headers=NO_STORE)
        _clear_cookie(response)
        return response


class MeView(APIView):
    permission_classes = [RequireSignedIn]

    @extend_schema(
        operation_id="get_me",
        summary="The signed-in actor",
        responses={200: OpenApiResponse(ActorSummarySerializer, description="The caller")},
    )
    def get(self, request):
        summary = selectors.get_actor_summary(get_actor(request).account_id)
        if summary is None:
            raise DomainError("unauthenticated")
        return Response(_summary_data(summary), headers=NO_STORE)


class CsrfView(APIView):
    permission_classes = [AllowPublic]
    authentication_classes: list = []

    @extend_schema(
        operation_id="get_csrf_token",
        summary="Fetch a CSRF token",
        description=(
            "Sets the (HttpOnly) CSRF cookie and returns the matching token, which the client "
            "sends back as `X-CSRFToken` on sign-in and sign-out. Never cached."
        ),
        responses={200: OpenApiResponse(CsrfTokenSerializer, description="The token")},
    )
    def get(self, request):
        return Response({"csrf_token": get_token(request._request)}, headers=NO_STORE)
