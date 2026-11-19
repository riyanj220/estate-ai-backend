"""Tests-only urlconf: a limited public view."""

from datetime import UTC, datetime

from django.urls import path
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.kernel import ratelimit
from apps.presentation.api.handlers import bad_request, forbidden, not_found, server_error
from apps.presentation.api.permissions import AllowPublic
from apps.presentation.api.throttling import PolicyThrottle

handler400 = bad_request
handler403 = forbidden
handler404 = not_found
handler500 = server_error


class LimitedView(APIView):
    permission_classes = [AllowPublic]
    throttle_classes = [PolicyThrottle]
    rate_limit_scope = "probe"

    def get(self, request):
        return Response({"ok": True})


class AccountLimitedView(LimitedView):
    rate_limit_scope = "probe_account"


class ServiceView(APIView):
    """A service-path limit: `RateLimited` propagates to the API handler."""

    permission_classes = [AllowPublic]
    FIXED_NOW = datetime(2026, 1, 1, 12, 0, 10, tzinfo=UTC)  # 10 s into a 60 s window

    def get(self, request):
        ratelimit.enforce("probe", ratelimit.derive_key("service"), self.FIXED_NOW)
        return Response({"ok": True})


class UnscopedView(LimitedView):
    rate_limit_scope = None


urlpatterns = [
    path("limited", LimitedView.as_view()),
    path("limited-account", AccountLimitedView.as_view()),
    path("service", ServiceView.as_view()),
    path("unscoped", UnscopedView.as_view()),
]
