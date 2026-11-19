"""Tests-only urlconf: views that raise each catalogued error, plus a paginated list."""

from django.contrib.contenttypes.models import ContentType
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.urls import path
from rest_framework import exceptions as drf
from rest_framework import generics, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.kernel.errors import DomainError
from apps.presentation.api.handlers import bad_request, forbidden, not_found, server_error
from apps.presentation.api.pagination import StandardCursorPagination

handler400 = bad_request
handler403 = forbidden
handler404 = not_found
handler500 = server_error


class ProbeSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=5)
    email = serializers.EmailField()
    address = serializers.ChoiceField(choices=["home", "office"])
    tags = serializers.ListField(child=serializers.IntegerField())


class ValidationView(APIView):
    def post(self, request):
        ProbeSerializer(data=request.data).is_valid(raise_exception=True)
        return Response()  # pragma: no cover


class RaiseView(APIView):
    exc: Exception = RuntimeError()

    def get(self, request, **kwargs):
        raise self.exc

    post = get


def raiser(name, exc):
    return path(f"raise/{name}", RaiseView.as_view(exc=exc))


class ContentTypeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    app_label = serializers.CharField()
    model = serializers.CharField()


class ContentTypePagination(StandardCursorPagination):
    ordering = "id"


class ContentTypeList(generics.ListAPIView):
    queryset = ContentType.objects.all()
    serializer_class = ContentTypeSerializer
    pagination_class = ContentTypePagination


class UnorderedPagination(StandardCursorPagination):
    pass


class UnorderedList(ContentTypeList):
    pagination_class = UnorderedPagination


class MultiPagination(StandardCursorPagination):
    ordering = ("app_label", "id")


class MultiList(ContentTypeList):
    pagination_class = MultiPagination


class HostileView(APIView):
    def post(self, request):
        class S(serializers.Serializer):
            meta = serializers.DictField(child=serializers.IntegerField())

        S(data=request.data).is_valid(raise_exception=True)


def plain_failure(request):
    raise RuntimeError("0300-1234567 secret@example.com")


def csrf_token(request):
    return JsonResponse({"token": get_token(request)})


def csrf_post(request):
    return JsonResponse({"ok": True})


urlpatterns = [
    path("csrf-token", csrf_token),
    path("csrf-post", csrf_post),
    path("validate", ValidationView.as_view()),
    path("items", ContentTypeList.as_view()),
    path("unordered", UnorderedList.as_view()),
    path("multi", MultiList.as_view()),
    path("hostile", HostileView.as_view()),
    path("empty-validation", RaiseView.as_view(exc=drf.ValidationError([]))),
    path("plain-failure", plain_failure),
    path("things/<int:pk>", RaiseView.as_view(exc=drf.NotFound("x"))),
    raiser("unauthenticated", drf.NotAuthenticated()),
    raiser("forbidden", drf.PermissionDenied()),
    raiser("stale", DomainError("stale_revision")),
    raiser("idempotency", DomainError("idempotency_key_reused")),
    raiser("throttled", drf.Throttled(wait=29.2)),
    raiser("throttled-unknown", drf.Throttled()),
    raiser("boom", RuntimeError("0300-1234567 secret@example.com")),
]
