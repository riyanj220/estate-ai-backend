"""Tests-only urlconf: a service-style view that claims inside its transaction."""

import uuid

from django.db import transaction
from django.urls import path
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.kernel import idempotency
from apps.presentation.api.handlers import bad_request, forbidden, not_found, server_error
from apps.presentation.api.permissions import AllowPublic

handler400 = bad_request
handler403 = forbidden
handler404 = not_found
handler500 = server_error

ACCOUNT = uuid.UUID("11111111-1111-4111-8111-111111111111")


class ClaimView(APIView):
    permission_classes = [AllowPublic]

    def post(self, request, digit):
        with transaction.atomic():
            idempotency.claim("probe", ACCOUNT, "same-key", str(digit) * 64)
        return Response({"ok": True})


urlpatterns = [path("claim/<int:digit>", ClaimView.as_view())]
