"""Tests-only urlconf: every view declares its permission."""

from uuid import UUID

from django.urls import path
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.presentation.api.handlers import bad_request, forbidden, not_found, server_error
from apps.presentation.api.permissions import AllowPublic, RequireAdmin, RequireSignedIn
from tests.authz import sample_service

handler400 = bad_request
handler403 = forbidden
handler404 = not_found
handler500 = server_error


class WhoView(APIView):
    permission_classes = [AllowPublic]

    def get(self, request):
        actor = request.actor
        return Response(
            {
                "kind": actor.kind.value,
                "account_id": str(actor.account_id) if actor.account_id else None,
                "request_id": actor.request_id,
            }
        )


class SignedInView(WhoView):
    permission_classes = [RequireSignedIn]


class AdminView(WhoView):
    permission_classes = [RequireAdmin]


class WidgetView(APIView):
    permission_classes = [RequireSignedIn]

    def get(self, request, widget_id: UUID):
        widget = sample_service.get_widget(request.actor, widget_id)
        return Response({"name": widget.name})

    def post(self, request, widget_id: UUID):
        widget = sample_service.rename_widget(request.actor, widget_id, "renamed")
        return Response({"name": widget.name})


urlpatterns = [
    path("who", WhoView.as_view()),
    path("signed-in", SignedInView.as_view()),
    path("admin-only", AdminView.as_view()),
    path("widgets/<uuid:widget_id>", WidgetView.as_view()),
]
