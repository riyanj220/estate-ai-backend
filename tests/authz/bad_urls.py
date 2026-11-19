"""Tests-only urlconf with routes that declare no (or the wrong) permission."""

from django.http import JsonResponse
from django.urls import path
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.presentation.api.handlers import bad_request, forbidden, not_found, server_error
from apps.presentation.api.permissions import AllowPublic, RequireAdmin, RequireSignedIn

handler400 = bad_request
handler403 = forbidden
handler404 = not_found
handler500 = server_error


class UndeclaredView(APIView):
    def get(self, request):
        return Response({"open": True})


class AllowAnyView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"open": True})


class ComposedView(APIView):
    permission_classes = [AllowPublic | RequireAdmin]

    def get(self, request):
        return Response({})


class OverridingView(APIView):
    permission_classes = [RequireSignedIn]

    def get_permissions(self):
        return []

    def get(self, request):
        return Response({})


class SpoofedLabel(BasePermission):
    label = "public"

    def has_permission(self, request, view):
        return True


class SpoofView(APIView):
    permission_classes = [SpoofedLabel]

    def get(self, request):
        return Response({})


def plain_django_view(request):
    return JsonResponse({"open": True})


urlpatterns = [
    path("undeclared", UndeclaredView.as_view()),
    path("allow-any", AllowAnyView.as_view()),
    path("plain", plain_django_view),
    path("composed", ComposedView.as_view()),
    path("overriding", OverridingView.as_view()),
    path("spoof", SpoofView.as_view()),
]
