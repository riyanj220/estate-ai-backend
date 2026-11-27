from django.urls import path

from apps.presentation.adminapi.accounts import (
    AccountLookupView,
    AccountReactivateView,
    AccountSuspendView,
)
from apps.presentation.adminapi.closure import (
    ClosureApproveView,
    ClosureQueueView,
    ClosureRejectView,
)
from apps.presentation.adminapi.flags import AdminFlagsView
from apps.presentation.adminapi.listings import (
    AdminDraftReadView,
    AdminListingsView,
    AdminListingView,
    ArchiveRemovedListingView,
    LiftRestrictionView,
    RemoveListingView,
    RestoreListingView,
    RestrictListingView,
)
from apps.presentation.adminapi.me import AdminMeView
from apps.presentation.adminapi.quota import (
    QuotaPolicyDetailView,
    QuotaPolicyView,
    QuotaUsageView,
)
from apps.presentation.adminapi.reports import (
    ReportDetailView,
    ReportDismissView,
    ReportQueueView,
    ReportReadView,
    ReportResolveView,
    ReportStartView,
)
from apps.presentation.adminapi.verification import (
    VerificationApproveView,
    VerificationQueueView,
    VerificationReadView,
    VerificationRejectView,
    VerificationRevokeView,
)
from apps.presentation.api.agency_dashboard import (
    AgencyDashboardListingsView,
    AgencyDashboardView,
    ListingViewBeaconView,
)
from apps.presentation.api.agency_verification import (
    AgencyVerificationRequestView,
    AgencyVerificationView,
    AgencyVerificationWithdrawView,
)
from apps.presentation.api.assistant import (
    AssistantDescriptionView,
    AssistantSessionsView,
    AssistantSessionView,
    AssistantTurnsView,
)
from apps.presentation.api.closure import ClosureRequestDetailView, ClosureRequestsView
from apps.presentation.api.conversations import (
    ConversationDetailView,
    ConversationMessagesView,
    ConversationReadView,
    ConversationsView,
    UnreadConversationsView,
)
from apps.presentation.api.estimates import (
    EstimateView,
    ListingEstimateView,
    PublicListingEstimateView,
)
from apps.presentation.api.favourites import (
    FavouriteDetailView,
    FavouriteIdsView,
    FavouritesView,
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
    ListingConfirmView,
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
from apps.presentation.api.public_listings import (
    PublicAgencyListingsView,
    PublicAgencyView,
    PublicFiltersView,
    PublicListingDetailView,
    PublicListingsView,
)
from apps.presentation.api.reference import AmenityView, CityView, LocalityView
from apps.presentation.api.registrations import AgencyRegistrationView, IndividualRegistrationView
from apps.presentation.api.registry import RegistryView
from apps.presentation.api.reports import ConversationReportsView, ListingReportsView, OwnReportView
from apps.presentation.api.schema import PublicSchemaView
from apps.presentation.api.search import PublicSearchTranscriptionView, PublicSearchView
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
    path(
        "api/v1/admin/verification-requests",
        VerificationQueueView.as_view(),
        name="admin-verification-requests",
    ),
    path(
        "api/v1/admin/verification-requests/<str:request_id>/read",
        VerificationReadView.as_view(),
        name="admin-verification-read",
    ),
    path(
        "api/v1/admin/verification-requests/<str:request_id>/approve",
        VerificationApproveView.as_view(),
        name="admin-verification-approve",
    ),
    path(
        "api/v1/admin/verification-requests/<str:request_id>/reject",
        VerificationRejectView.as_view(),
        name="admin-verification-reject",
    ),
    path(
        "api/v1/admin/verification-requests/<str:request_id>/revoke",
        VerificationRevokeView.as_view(),
        name="admin-verification-revoke",
    ),
    path(
        "api/v1/me/agency-verification",
        AgencyVerificationView.as_view(),
        name="me-agency-verification",
    ),
    path(
        "api/v1/me/agency-verification/requests",
        AgencyVerificationRequestView.as_view(),
        name="me-agency-verification-requests",
    ),
    path(
        "api/v1/me/agency-verification/withdraw",
        AgencyVerificationWithdrawView.as_view(),
        name="me-agency-verification-withdraw",
    ),
    path(
        "api/v1/me/closure-requests",
        ClosureRequestsView.as_view(),
        name="me-closure-requests",
    ),
    path(
        "api/v1/me/closure-requests/<str:request_id>",
        ClosureRequestDetailView.as_view(),
        name="me-closure-request",
    ),
    path(
        "api/v1/admin/closure-requests",
        ClosureQueueView.as_view(),
        name="admin-closure-requests",
    ),
    path(
        "api/v1/admin/closure-requests/<str:request_id>/approve",
        ClosureApproveView.as_view(),
        name="admin-closure-approve",
    ),
    path(
        "api/v1/admin/closure-requests/<str:request_id>/reject",
        ClosureRejectView.as_view(),
        name="admin-closure-reject",
    ),
    path("api/v1/admin/reports", ReportQueueView.as_view(), name="admin-reports"),
    path("api/v1/admin/reports/<str:report_id>", ReportDetailView.as_view(), name="admin-report"),
    path(
        "api/v1/admin/reports/<str:report_id>/start",
        ReportStartView.as_view(),
        name="admin-report-start",
    ),
    path(
        "api/v1/admin/reports/<str:report_id>/resolve",
        ReportResolveView.as_view(),
        name="admin-report-resolve",
    ),
    path(
        "api/v1/admin/reports/<str:report_id>/dismiss",
        ReportDismissView.as_view(),
        name="admin-report-dismiss",
    ),
    path(
        "api/v1/admin/reports/<str:report_id>/read",
        ReportReadView.as_view(),
        name="admin-report-read",
    ),
    path("api/v1/admin/flags", AdminFlagsView.as_view(), name="admin-flags"),
    path("api/v1/admin/listings", AdminListingsView.as_view(), name="admin-listings"),
    path(
        "api/v1/admin/listings/<str:listing_id>", AdminListingView.as_view(), name="admin-listing"
    ),
    path(
        "api/v1/admin/listings/<str:listing_id>/read",
        AdminDraftReadView.as_view(),
        name="admin-listing-read",
    ),
    path(
        "api/v1/admin/listings/<str:listing_id>/restrict",
        RestrictListingView.as_view(),
        name="admin-listing-restrict",
    ),
    path(
        "api/v1/admin/listings/<str:listing_id>/lift-restriction",
        LiftRestrictionView.as_view(),
        name="admin-listing-lift",
    ),
    path(
        "api/v1/admin/listings/<str:listing_id>/remove",
        RemoveListingView.as_view(),
        name="admin-listing-remove",
    ),
    path(
        "api/v1/admin/listings/<str:listing_id>/restore",
        RestoreListingView.as_view(),
        name="admin-listing-restore",
    ),
    path(
        "api/v1/admin/listings/<str:listing_id>/archive",
        ArchiveRemovedListingView.as_view(),
        name="admin-listing-archive",
    ),
    path(
        "api/v1/listings/<str:listing_id>/reports",
        ListingReportsView.as_view(),
        name="listing-reports",
    ),
    path("api/v1/me/reports/<str:report_id>", OwnReportView.as_view(), name="me-report"),
    path(
        "api/v1/conversations/<str:conversation_id>/reports",
        ConversationReportsView.as_view(),
        name="conversation-reports",
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
        "api/v1/listings/<str:listing_id>/confirm",
        ListingConfirmView.as_view(),
        name="listing-confirm",
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
    path("api/v1/estimates", EstimateView.as_view(), name="estimates"),
    path(
        "api/v1/assistant/sessions",
        AssistantSessionsView.as_view(),
        name="assistant-sessions",
    ),
    path(
        "api/v1/assistant/sessions/<str:session_id>",
        AssistantSessionView.as_view(),
        name="assistant-session",
    ),
    path(
        "api/v1/assistant/sessions/<str:session_id>/turns",
        AssistantTurnsView.as_view(),
        name="assistant-turns",
    ),
    path(
        "api/v1/assistant/sessions/<str:session_id>/description",
        AssistantDescriptionView.as_view(),
        name="assistant-description",
    ),
    path(
        "api/v1/public/listings/<str:listing_id>/estimate",
        PublicListingEstimateView.as_view(),
        name="public-listing-estimate",
    ),
    path(
        "api/v1/listings/<str:listing_id>/estimate",
        ListingEstimateView.as_view(),
        name="listing-estimate",
    ),
    path("api/v1/conversations", ConversationsView.as_view(), name="conversations"),
    path(
        "api/v1/conversations/unread-count",
        UnreadConversationsView.as_view(),
        name="conversations-unread",
    ),
    path(
        "api/v1/conversations/<str:conversation_id>",
        ConversationDetailView.as_view(),
        name="conversation",
    ),
    path(
        "api/v1/conversations/<str:conversation_id>/messages",
        ConversationMessagesView.as_view(),
        name="conversation-messages",
    ),
    path(
        "api/v1/conversations/<str:conversation_id>/read",
        ConversationReadView.as_view(),
        name="conversation-read",
    ),
    path("api/v1/public/filters", PublicFiltersView.as_view(), name="public-filters"),
    path("api/v1/public/search", PublicSearchView.as_view(), name="public-search"),
    path(
        "api/v1/public/search/transcription",
        PublicSearchTranscriptionView.as_view(),
        name="public-search-transcription",
    ),
    path("api/v1/public/listings", PublicListingsView.as_view(), name="public-listings"),
    path(
        "api/v1/public/listings/<str:listing_id>",
        PublicListingDetailView.as_view(),
        name="public-listing",
    ),
    path(
        "api/v1/public/listings/<str:listing_id>/views",
        ListingViewBeaconView.as_view(),
        name="public-listing-views",
    ),
    path(
        "api/v1/public/agencies/<str:agency_id>",
        PublicAgencyView.as_view(),
        name="public-agency",
    ),
    path(
        "api/v1/public/agencies/<str:agency_id>/listings",
        PublicAgencyListingsView.as_view(),
        name="public-agency-listings",
    ),
    path("api/v1/me/favourites", FavouritesView.as_view(), name="me-favourites"),
    path("api/v1/me/favourites/ids", FavouriteIdsView.as_view(), name="me-favourites-ids"),
    path(
        "api/v1/me/favourites/<str:listing_id>",
        FavouriteDetailView.as_view(),
        name="me-favourite",
    ),
    path("api/v1/me/listings", MyListingsView.as_view(), name="me-listings"),
    path("api/v1/me/listings/counts", MyListingCountsView.as_view(), name="me-listings-counts"),
    path(
        "api/v1/me/publication-allowance",
        PublicationAllowanceView.as_view(),
        name="me-publication-allowance",
    ),
    path("api/v1/me/agency-dashboard", AgencyDashboardView.as_view(), name="me-agency-dashboard"),
    path(
        "api/v1/me/agency-dashboard/listings",
        AgencyDashboardListingsView.as_view(),
        name="me-agency-dashboard-listings",
    ),
    path("api/v1/schema", PublicSchemaView.as_view(), name="schema"),
]
