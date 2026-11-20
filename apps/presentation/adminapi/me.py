"""The administrator's own identity, the first route of the protected admin API (Story 1.22)."""

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.identity import selectors
from apps.identity.state import AccountState
from apps.kernel.errors import DomainError
from apps.presentation.api.actors import get_actor
from apps.presentation.api.authentication import SessionAuthentication
from apps.presentation.api.permissions import RequireAdmin


class AdminMeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    account_type = serializers.ChoiceField(choices=["admin"])
    state = serializers.ChoiceField(choices=[s.value for s in AccountState])


class AdminMeView(APIView):
    """Nothing private: no email, no capabilities, no session data."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [RequireAdmin]

    @extend_schema(
        operation_id="get_admin_me",
        summary="The signed-in administrator",
        description=(
            "Visitors get `401 unauthenticated`; signed-in non-administrators get `404 not_found`, "
            "so the admin area is not confirmed to them."
        ),
        responses={200: OpenApiResponse(AdminMeSerializer, description="The administrator")},
    )
    def get(self, request):
        view = selectors.get_signed_in_account(get_actor(request).account_id)
        if view is None or view.type != "admin":
            raise DomainError("unauthenticated")
        return Response(
            AdminMeSerializer(
                {"id": view.id, "account_type": view.type, "state": view.state.value}
            ).data,
            headers={"Cache-Control": "no-store"},
        )
