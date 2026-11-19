from django.urls import path

from apps.presentation.api.handlers import bad_request, forbidden, not_found, server_error
from apps.presentation.api.health import health
from apps.presentation.api.reference import CityView
from apps.presentation.api.schema import PublicSchemaView

handler400 = bad_request
handler403 = forbidden
handler404 = not_found
handler500 = server_error

urlpatterns = [
    path("api/v1/health", health, name="health"),
    path("api/v1/reference/cities", CityView.as_view(), name="reference-cities"),
    path("api/v1/schema", PublicSchemaView.as_view(), name="schema"),
]
