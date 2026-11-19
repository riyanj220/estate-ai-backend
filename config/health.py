import logging

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_safe

logger = logging.getLogger(__name__)


@require_safe
def health(request):
    """Liveness plus database reachability. Never reveals connection details."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 - any failure means unavailable
        logger.error("Health check failed: %s", type(exc).__name__)
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
