from django.urls import path

from apps.presentation.adminapi.me import AdminMeView
from apps.presentation.api.handlers import bad_request, forbidden, not_found, server_error
from apps.presentation.api.health import health
from apps.presentation.api.passwords import (
    ChangePasswordView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
)
from apps.presentation.api.profile import (
    AgencyProfileDetailView,
    EmailChangeConfirmView,
    EmailChangeRequestView,
    ProfileDetailView,
)
from apps.presentation.api.reference import CityView
from apps.presentation.api.registrations import AgencyRegistrationView, IndividualRegistrationView
from apps.presentation.api.schema import PublicSchemaView
from apps.presentation.api.sessions import AdminSessionView, CsrfView, MeView, SessionView
from apps.presentation.api.verifications import ConfirmEmailView, VerificationRequestView

handler400 = bad_request
handler403 = forbidden
handler404 = not_found
handler500 = server_error

urlpatterns = [
    path("api/v1/health", health, name="health"),
    path("api/v1/reference/cities", CityView.as_view(), name="reference-cities"),
    path(
        "api/v1/registrations/individuals",
        IndividualRegistrationView.as_view(),
        name="registrations-individuals",
    ),
    path(
        "api/v1/registrations/agencies",
        AgencyRegistrationView.as_view(),
        name="registrations-agencies",
    ),
    path(
        "api/v1/email-verifications/confirm",
        ConfirmEmailView.as_view(),
        name="email-verifications-confirm",
    ),
    path(
        "api/v1/email-verification-requests",
        VerificationRequestView.as_view(),
        name="email-verification-requests",
    ),
    path("api/v1/csrf", CsrfView.as_view(), name="csrf"),
    path("api/v1/session", SessionView.as_view(), name="session"),
    path("api/v1/admin-session", AdminSessionView.as_view(), name="admin-session"),
    path("api/v1/admin/me", AdminMeView.as_view(), name="admin-me"),
    path("api/v1/me", MeView.as_view(), name="me"),
    path("api/v1/me/password", ChangePasswordView.as_view(), name="me-password"),
    path("api/v1/me/profile", ProfileDetailView.as_view(), name="me-profile"),
    path(
        "api/v1/me/agency-profile",
        AgencyProfileDetailView.as_view(),
        name="me-agency-profile",
    ),
    path("api/v1/me/email-change", EmailChangeRequestView.as_view(), name="me-email-change"),
    path(
        "api/v1/email-changes/confirm",
        EmailChangeConfirmView.as_view(),
        name="email-changes-confirm",
    ),
    path(
        "api/v1/password-reset-requests",
        PasswordResetRequestView.as_view(),
        name="password-reset-requests",
    ),
    path(
        "api/v1/password-resets/confirm",
        PasswordResetConfirmView.as_view(),
        name="password-resets-confirm",
    ),
    path("api/v1/schema", PublicSchemaView.as_view(), name="schema"),
]
