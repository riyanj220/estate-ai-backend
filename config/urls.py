from django.urls import path
from drf_spectacular.views import SpectacularAPIView

from apps.presentation.api.handlers import bad_request, forbidden, not_found, server_error
from config.health import health

handler400 = bad_request
handler403 = forbidden
handler404 = not_found
handler500 = server_error

urlpatterns = [
    path("api/v1/health", health, name="health"),
    path("api/v1/schema", SpectacularAPIView.as_view(), name="schema"),
]
