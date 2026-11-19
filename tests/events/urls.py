"""Tests-only urlconf: a view that emits inside a transaction with a failing subscriber."""

import uuid

from django.db import transaction
from django.urls import path
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.kernel import events
from apps.presentation.api.handlers import bad_request, forbidden, not_found, server_error
from apps.presentation.api.permissions import AllowPublic
from tests.events.sample_events import ThingHappened

handler400 = bad_request
handler403 = forbidden
handler404 = not_found
handler500 = server_error


class EmitView(APIView):
    permission_classes = [AllowPublic]

    def post(self, request):
        with transaction.atomic():
            events.emit(ThingHappened(thing_id=uuid.uuid4()))
        return Response({"ok": True})


urlpatterns = [path("emit", EmitView.as_view())]
