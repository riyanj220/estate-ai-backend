"""Django-level handlers so errors outside DRF views use the same envelope."""

from django.http import JsonResponse

from apps.kernel.errors import get_error
from apps.presentation.api.exceptions import build_envelope


def _respond(request, code: str) -> JsonResponse:
    return JsonResponse(
        build_envelope(code, getattr(request, "request_id", "")), status=get_error(code).status
    )


def not_found(request, exception=None):
    return _respond(request, "not_found")


def server_error(request):
    return _respond(request, "server_error")


def bad_request(request, exception=None):
    return _respond(request, "bad_request")


def forbidden(request, exception=None):
    return _respond(request, "forbidden")
