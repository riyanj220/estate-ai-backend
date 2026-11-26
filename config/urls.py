from django.urls import path

from apps.presentation.adminapi.accounts import (
    AccountLookupView,
    AccountReactivateView,
    AccountSuspendView,
)
from apps.presentation.adminapi.me import AdminMeView
from apps.presentation.adminapi.quota import (
    QuotaPolicyDetailView,
    QuotaPolicyView,
    QuotaUsageView,
)
from apps.presentation.api.handlers import bad_request, forbidden, not_found, server_error
from apps.presentation.api.health import health
from apps.presentation.api.listing_images import (
    ListingImageDetailView,
    ListingImageFileView,
    ListingImagesView,
)
from apps.presentation.api.listings import (
    DraftCreateView,
    ListingCorrectionView,
    ListingDetailView,
    ListingPreviewView,
    ListingPublishView,
    ListingRepublishView,
    ListingStatusView,
    MyListingCountsView,
    MyListingsView,
    PublicationAllowanceView,
)
from apps.presentation.api.notifications import (
    MarkAllReadView,
    MarkReadView,
    NotificationListView,
    UnreadCountView,
)
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
from apps.presentation.api.profile_images import (
    AgencyLogoView,
    AgencyProfileImageFileView,
    AgencyProfileImageView,
    ProfileImageFileView,
    ProfileImageView,
)
from apps.presentation.api.reference import AmenityView, CityView, LocalityView
from apps.presentation.api.registrations import AgencyRegistrationView, IndividualRegistrationView
from apps.presentation.api.registry import RegistryView
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
        "api/v1/reference/cities/<str:city_id>/localities",
        LocalityView.as_view(),
        name="reference-city-localities",
    ),
    path("api/v1/reference/amenities", AmenityView.as_view(), name="reference-amenities"),
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
    path("api/v1/admin/quota-policy", QuotaPolicyView.as_view(), name="admin-quota-policy"),
    path(
        "api/v1/admin/quota-policy/<str:account_type>",
        QuotaPolicyDetailView.as_view(),
        name="admin-quota-policy-type",
    ),
    path("api/v1/admin/quota-usage", QuotaUsageView.as_view(), name="admin-quota-usage"),
    path("api/v1/admin/accounts/lookup", AccountLookupView.as_view(), name="admin-account-lookup"),
    path(
        "api/v1/admin/accounts/<str:account_id>/suspend",
        AccountSuspendView.as_view(),
        name="admin-account-suspend",
    ),
    path(
        "api/v1/admin/accounts/<str:account_id>/reactivate",
        AccountReactivateView.as_view(),
        name="admin-account-reactivate",
    ),
    path("api/v1/me", MeView.as_view(), name="me"),
    path("api/v1/me/password", ChangePasswordView.as_view(), name="me-password"),
    path("api/v1/me/profile", ProfileDetailView.as_view(), name="me-profile"),
    path(
        "api/v1/me/agency-profile",
        AgencyProfileDetailView.as_view(),
        name="me-agency-profile",
    ),
    path("api/v1/me/profile/image", ProfileImageView.as_view(), name="me-profile-image"),
    path(
        "api/v1/me/profile/image/file",
        ProfileImageFileView.as_view(),
        name="me-profile-image-file",
    ),
    path(
        "api/v1/me/agency-profile/image",
        AgencyProfileImageView.as_view(),
        name="me-agency-profile-image",
    ),
    path(
        "api/v1/me/agency-profile/image/file",
        AgencyProfileImageFileView.as_view(),
        name="me-agency-profile-image-file",
    ),
    path("api/v1/agencies/<str:agency_id>/logo", AgencyLogoView.as_view(), name="agency-logo"),
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
    path("api/v1/notifications", NotificationListView.as_view(), name="notifications"),
    path(
        "api/v1/notifications/unread-count", UnreadCountView.as_view(), name="notifications-unread"
    ),
    path("api/v1/notifications/read-all", MarkAllReadView.as_view(), name="notifications-read-all"),
    path(
        "api/v1/notifications/<str:notification_id>/read",
        MarkReadView.as_view(),
        name="notifications-read",
    ),
    path("api/v1/registry", RegistryView.as_view(), name="registry"),
    path("api/v1/listings", DraftCreateView.as_view(), name="listings"),
    path("api/v1/listings/<str:listing_id>", ListingDetailView.as_view(), name="listing"),
    path(
        "api/v1/listings/<str:listing_id>/preview",
        ListingPreviewView.as_view(),
        name="listing-preview",
    ),
    path(
        "api/v1/listings/<str:listing_id>/publish",
        ListingPublishView.as_view(),
        name="listing-publish",
    ),
    path(
        "api/v1/listings/<str:listing_id>/republish",
        ListingRepublishView.as_view(),
        name="listing-republish",
    ),
    path(
        "api/v1/listings/<str:listing_id>/correction",
        ListingCorrectionView.as_view(),
        name="listing-correction",
    ),
    path(
        "api/v1/listings/<str:listing_id>/status",
        ListingStatusView.as_view(),
        name="listing-status",
    ),
    path(
        "api/v1/listings/<str:listing_id>/images",
        ListingImagesView.as_view(),
        name="listing-images",
    ),
    path(
        "api/v1/listings/<str:listing_id>/images/<str:image_id>",
        ListingImageDetailView.as_view(),
        name="listing-image",
    ),
    path(
        "api/v1/listings/<str:listing_id>/images/<str:image_id>/file",
        ListingImageFileView.as_view(),
        name="listing-image-file",
    ),
    path("api/v1/me/listings", MyListingsView.as_view(), name="me-listings"),
    path("api/v1/me/listings/counts", MyListingCountsView.as_view(), name="me-listings-counts"),
    path(
        "api/v1/me/publication-allowance",
        PublicationAllowanceView.as_view(),
        name="me-publication-allowance",
    ),
    path("api/v1/schema", PublicSchemaView.as_view(), name="schema"),
]
