# Authorisation matrix

Every request resolves to one `Actor` (visitor, user, admin or system) in `apps/presentation/api/actors.py`.
Permission classes are a first gate only; services take `actor` first and enforce policy themselves (AD-22).

| Caller | Route needs sign-in | Route needs admin | Resource they do not own |
| --- | --- | --- | --- |
| Visitor | 401 `unauthenticated` | 401 `unauthenticated` | 401 on sign-in routes; 404 `not_found` on public routes |
| Signed-in user | allowed | 404 `not_found` | 404 `not_found` |
| Administrator | allowed | allowed | 404 `not_found` from the owner-only sample service; each real service states its own rule |
| System | passes signed-in checks | refused with 404 `not_found` | refused with 404 `not_found` by the owner-only sample service |

A Visitor on an admin route gets 401: admin routes exist but are not accessible to them. Only signed-in non-admins
get 404, so the admin area is not confirmed to them.

A view with no explicit permission is denied (403). Declare one of `AllowPublic`, `RequireSignedIn` or
`RequireAdmin` from `apps/presentation/api/permissions.py` on the view. Views read the caller with
`get_actor(request)` rather than assuming `request.actor` exists.

## Reports (Epic 9)

| Caller | Report a Listing | Read a Report | Review queue and moderation |
| --- | --- | --- | --- |
| Visitor | 401 `unauthenticated` | 401 | 401 |
| Email-unverified, Active | public Listing of another account: allowed; own: 403; not public: 404 | own only; another's: 404 | 404 |
| Suspended | 403 `forbidden` | own only | 404 |
| Administrator | 403 `forbidden` | 404 on the reporter route; review through the admin routes | allowed |

The reported Listing's owner never sees who reported it.

A Conversation Report (Story 9.5) is made only by a participant (anyone else: 404; Suspended: 403) and only about
the other participant's Messages. Administrators see its Messages and description only through the reasoned,
audited `POST /api/v1/admin/reports/{id}/read`. The advisory flag list (Story 9.6) is administrator only.

## Routes

Generated from the URL configuration; do not edit by hand. Regenerate with `uv run python scripts/authz_matrix.py`.
`tests/authz/test_matrix.py` fails when this table is stale, and `tests/authz/test_routes.py` fails any route
whose permission is not explicit.

<!-- BEGIN GENERATED ROUTES -->
| Route | Methods | Permission | View |
| --- | --- | --- | --- |
| `/api/v1/admin-session` | POST | public | `apps.presentation.api.sessions.AdminSessionView` |
| `/api/v1/admin/accounts` | GET | admin | `apps.presentation.adminapi.directory.AccountDirectoryView` |
| `/api/v1/admin/accounts/<str:account_id>` | GET | admin | `apps.presentation.adminapi.directory.AccountReviewView` |
| `/api/v1/admin/accounts/<str:account_id>/private` | POST | admin | `apps.presentation.adminapi.directory.AccountContactReadView` |
| `/api/v1/admin/accounts/<str:account_id>/reactivate` | POST | admin | `apps.presentation.adminapi.accounts.AccountReactivateView` |
| `/api/v1/admin/accounts/<str:account_id>/suspend` | POST | admin | `apps.presentation.adminapi.accounts.AccountSuspendView` |
| `/api/v1/admin/accounts/lookup` | POST | admin | `apps.presentation.adminapi.accounts.AccountLookupView` |
| `/api/v1/admin/ai-oversight` | GET | admin | `apps.presentation.adminapi.oversight.AiOversightView` |
| `/api/v1/admin/ai-oversight/switches/<str:name>` | POST | admin | `apps.presentation.adminapi.oversight.AiSwitchView` |
| `/api/v1/admin/audit-records` | GET | admin | `apps.presentation.adminapi.audit.AuditTrailView` |
| `/api/v1/admin/closure-requests` | GET | admin | `apps.presentation.adminapi.closure.ClosureQueueView` |
| `/api/v1/admin/closure-requests/<str:request_id>/approve` | POST | admin | `apps.presentation.adminapi.closure.ClosureApproveView` |
| `/api/v1/admin/closure-requests/<str:request_id>/reject` | POST | admin | `apps.presentation.adminapi.closure.ClosureRejectView` |
| `/api/v1/admin/dashboard` | GET | admin | `apps.presentation.adminapi.dashboard.AdminDashboardView` |
| `/api/v1/admin/flags` | GET | admin | `apps.presentation.adminapi.flags.AdminFlagsView` |
| `/api/v1/admin/listings` | GET | admin | `apps.presentation.adminapi.listings.AdminListingsView` |
| `/api/v1/admin/listings/<str:listing_id>` | GET | admin | `apps.presentation.adminapi.listings.AdminListingView` |
| `/api/v1/admin/listings/<str:listing_id>/archive` | POST | admin | `apps.presentation.adminapi.listings.ArchiveRemovedListingView` |
| `/api/v1/admin/listings/<str:listing_id>/lift-restriction` | POST | admin | `apps.presentation.adminapi.listings.LiftRestrictionView` |
| `/api/v1/admin/listings/<str:listing_id>/private-address` | POST | admin | `apps.presentation.adminapi.listings.AdminListingAddressReadView` |
| `/api/v1/admin/listings/<str:listing_id>/read` | POST | admin | `apps.presentation.adminapi.listings.AdminDraftReadView` |
| `/api/v1/admin/listings/<str:listing_id>/remove` | POST | admin | `apps.presentation.adminapi.listings.RemoveListingView` |
| `/api/v1/admin/listings/<str:listing_id>/restore` | POST | admin | `apps.presentation.adminapi.listings.RestoreListingView` |
| `/api/v1/admin/listings/<str:listing_id>/restrict` | POST | admin | `apps.presentation.adminapi.listings.RestrictListingView` |
| `/api/v1/admin/me` | GET | admin | `apps.presentation.adminapi.me.AdminMeView` |
| `/api/v1/admin/quota-policy` | GET | admin | `apps.presentation.adminapi.quota.QuotaPolicyView` |
| `/api/v1/admin/quota-policy/<str:account_type>` | PUT | admin | `apps.presentation.adminapi.quota.QuotaPolicyDetailView` |
| `/api/v1/admin/quota-usage` | GET | admin | `apps.presentation.adminapi.quota.QuotaUsageView` |
| `/api/v1/admin/reference/amenities` | GET, POST | admin | `apps.presentation.adminapi.reference.AdminAmenitiesView` |
| `/api/v1/admin/reference/amenities/<str:code>` | PATCH | admin | `apps.presentation.adminapi.reference.AdminAmenityView` |
| `/api/v1/admin/reference/cities` | GET, POST | admin | `apps.presentation.adminapi.reference.AdminCitiesView` |
| `/api/v1/admin/reference/cities/<str:city_id>` | PATCH | admin | `apps.presentation.adminapi.reference.AdminCityView` |
| `/api/v1/admin/reference/cities/<str:city_id>/localities` | GET, POST | admin | `apps.presentation.adminapi.reference.AdminCityLocalitiesView` |
| `/api/v1/admin/reference/fixed` | GET | admin | `apps.presentation.adminapi.reference.AdminFixedCatalogueView` |
| `/api/v1/admin/reference/localities/<str:locality_id>` | PATCH | admin | `apps.presentation.adminapi.reference.AdminLocalityView` |
| `/api/v1/admin/reports` | GET | admin | `apps.presentation.adminapi.reports.ReportQueueView` |
| `/api/v1/admin/reports/<str:report_id>` | GET | admin | `apps.presentation.adminapi.reports.ReportDetailView` |
| `/api/v1/admin/reports/<str:report_id>/dismiss` | POST | admin | `apps.presentation.adminapi.reports.ReportDismissView` |
| `/api/v1/admin/reports/<str:report_id>/read` | POST | admin | `apps.presentation.adminapi.reports.ReportReadView` |
| `/api/v1/admin/reports/<str:report_id>/resolve` | POST | admin | `apps.presentation.adminapi.reports.ReportResolveView` |
| `/api/v1/admin/reports/<str:report_id>/start` | POST | admin | `apps.presentation.adminapi.reports.ReportStartView` |
| `/api/v1/admin/second-factor` | GET | admin | `apps.presentation.adminapi.second_factor.SecondFactorView` |
| `/api/v1/admin/second-factor/confirm` | POST | admin | `apps.presentation.adminapi.second_factor.SecondFactorConfirmView` |
| `/api/v1/admin/second-factor/enrolment` | POST | admin | `apps.presentation.adminapi.second_factor.SecondFactorStartView` |
| `/api/v1/admin/support-requests` | GET | admin | `apps.presentation.adminapi.support.SupportQueueView` |
| `/api/v1/admin/support-requests/<str:request_id>/close` | POST | admin | `apps.presentation.adminapi.support.SupportCloseView` |
| `/api/v1/admin/support-requests/<str:request_id>/read` | POST | admin | `apps.presentation.adminapi.support.SupportReadView` |
| `/api/v1/admin/verification-requests` | GET | admin | `apps.presentation.adminapi.verification.VerificationQueueView` |
| `/api/v1/admin/verification-requests/<str:request_id>/approve` | POST | admin | `apps.presentation.adminapi.verification.VerificationApproveView` |
| `/api/v1/admin/verification-requests/<str:request_id>/read` | POST | admin | `apps.presentation.adminapi.verification.VerificationReadView` |
| `/api/v1/admin/verification-requests/<str:request_id>/reject` | POST | admin | `apps.presentation.adminapi.verification.VerificationRejectView` |
| `/api/v1/admin/verification-requests/<str:request_id>/revoke` | POST | admin | `apps.presentation.adminapi.verification.VerificationRevokeView` |
| `/api/v1/agencies/<str:agency_id>/logo` | GET, HEAD | public | `apps.presentation.api.profile_images.AgencyLogoView` |
| `/api/v1/assistant/sessions` | POST | signed_in | `apps.presentation.api.assistant.AssistantSessionsView` |
| `/api/v1/assistant/sessions/<str:session_id>` | GET | signed_in | `apps.presentation.api.assistant.AssistantSessionView` |
| `/api/v1/assistant/sessions/<str:session_id>/description` | POST | signed_in | `apps.presentation.api.assistant.AssistantDescriptionView` |
| `/api/v1/assistant/sessions/<str:session_id>/turns` | POST | signed_in | `apps.presentation.api.assistant.AssistantTurnsView` |
| `/api/v1/conversations` | GET, POST | signed_in | `apps.presentation.api.conversations.ConversationsView` |
| `/api/v1/conversations/<str:conversation_id>` | GET | signed_in | `apps.presentation.api.conversations.ConversationDetailView` |
| `/api/v1/conversations/<str:conversation_id>/messages` | GET, POST | signed_in | `apps.presentation.api.conversations.ConversationMessagesView` |
| `/api/v1/conversations/<str:conversation_id>/read` | POST | signed_in | `apps.presentation.api.conversations.ConversationReadView` |
| `/api/v1/conversations/<str:conversation_id>/reports` | POST | signed_in | `apps.presentation.api.reports.ConversationReportsView` |
| `/api/v1/conversations/unread-count` | GET | signed_in | `apps.presentation.api.conversations.UnreadConversationsView` |
| `/api/v1/csrf` | GET | public | `apps.presentation.api.sessions.CsrfView` |
| `/api/v1/email-changes/confirm` | POST | public | `apps.presentation.api.profile.EmailChangeConfirmView` |
| `/api/v1/email-verification-requests` | POST | public | `apps.presentation.api.verifications.VerificationRequestView` |
| `/api/v1/email-verifications/confirm` | POST | public | `apps.presentation.api.verifications.ConfirmEmailView` |
| `/api/v1/estimates` | POST | signed_in | `apps.presentation.api.estimates.EstimateView` |
| `/api/v1/health` | GET, HEAD | public | `apps.presentation.api.health.WrappedAPIView` |
| `/api/v1/listings` | POST | signed_in | `apps.presentation.api.listings.DraftCreateView` |
| `/api/v1/listings/<str:listing_id>` | DELETE, GET, PATCH | signed_in | `apps.presentation.api.listings.ListingDetailView` |
| `/api/v1/listings/<str:listing_id>/confirm` | POST | signed_in | `apps.presentation.api.listings.ListingConfirmView` |
| `/api/v1/listings/<str:listing_id>/correction` | GET, POST | signed_in | `apps.presentation.api.listings.ListingCorrectionView` |
| `/api/v1/listings/<str:listing_id>/estimate` | POST | signed_in | `apps.presentation.api.estimates.ListingEstimateView` |
| `/api/v1/listings/<str:listing_id>/images` | PATCH, POST | signed_in | `apps.presentation.api.listing_images.ListingImagesView` |
| `/api/v1/listings/<str:listing_id>/images/<str:image_id>` | DELETE | signed_in | `apps.presentation.api.listing_images.ListingImageDetailView` |
| `/api/v1/listings/<str:listing_id>/images/<str:image_id>/file` | GET, HEAD | public | `apps.presentation.api.listing_images.ListingImageFileView` |
| `/api/v1/listings/<str:listing_id>/preview` | GET | signed_in | `apps.presentation.api.listings.ListingPreviewView` |
| `/api/v1/listings/<str:listing_id>/publish` | POST | signed_in | `apps.presentation.api.listings.ListingPublishView` |
| `/api/v1/listings/<str:listing_id>/reports` | POST | signed_in | `apps.presentation.api.reports.ListingReportsView` |
| `/api/v1/listings/<str:listing_id>/republish` | POST | signed_in | `apps.presentation.api.listings.ListingRepublishView` |
| `/api/v1/listings/<str:listing_id>/status` | POST | signed_in | `apps.presentation.api.listings.ListingStatusView` |
| `/api/v1/me` | GET | signed_in | `apps.presentation.api.sessions.MeView` |
| `/api/v1/me/agency-dashboard` | GET | signed_in | `apps.presentation.api.agency_dashboard.AgencyDashboardView` |
| `/api/v1/me/agency-dashboard/listings` | GET | signed_in | `apps.presentation.api.agency_dashboard.AgencyDashboardListingsView` |
| `/api/v1/me/agency-profile` | GET, PATCH | signed_in | `apps.presentation.api.profile.AgencyProfileDetailView` |
| `/api/v1/me/agency-profile/image` | DELETE, PUT | signed_in | `apps.presentation.api.profile_images.AgencyProfileImageView` |
| `/api/v1/me/agency-profile/image/file` | GET, HEAD | public | `apps.presentation.api.profile_images.AgencyProfileImageFileView` |
| `/api/v1/me/agency-verification` | GET | signed_in | `apps.presentation.api.agency_verification.AgencyVerificationView` |
| `/api/v1/me/agency-verification/requests` | POST | signed_in | `apps.presentation.api.agency_verification.AgencyVerificationRequestView` |
| `/api/v1/me/agency-verification/withdraw` | POST | signed_in | `apps.presentation.api.agency_verification.AgencyVerificationWithdrawView` |
| `/api/v1/me/closure-requests` | GET, POST | signed_in | `apps.presentation.api.closure.ClosureRequestsView` |
| `/api/v1/me/closure-requests/<str:request_id>` | GET | signed_in | `apps.presentation.api.closure.ClosureRequestDetailView` |
| `/api/v1/me/email-change` | POST | signed_in | `apps.presentation.api.profile.EmailChangeRequestView` |
| `/api/v1/me/favourites` | GET | signed_in | `apps.presentation.api.favourites.FavouritesView` |
| `/api/v1/me/favourites/<str:listing_id>` | DELETE, PUT | signed_in | `apps.presentation.api.favourites.FavouriteDetailView` |
| `/api/v1/me/favourites/ids` | GET | signed_in | `apps.presentation.api.favourites.FavouriteIdsView` |
| `/api/v1/me/listings` | GET | signed_in | `apps.presentation.api.listings.MyListingsView` |
| `/api/v1/me/listings/counts` | GET | signed_in | `apps.presentation.api.listings.MyListingCountsView` |
| `/api/v1/me/password` | POST | signed_in | `apps.presentation.api.passwords.ChangePasswordView` |
| `/api/v1/me/profile` | GET, PATCH | signed_in | `apps.presentation.api.profile.ProfileDetailView` |
| `/api/v1/me/profile/image` | DELETE, PUT | signed_in | `apps.presentation.api.profile_images.ProfileImageView` |
| `/api/v1/me/profile/image/file` | GET, HEAD | public | `apps.presentation.api.profile_images.ProfileImageFileView` |
| `/api/v1/me/publication-allowance` | GET | signed_in | `apps.presentation.api.listings.PublicationAllowanceView` |
| `/api/v1/me/reports/<str:report_id>` | GET | signed_in | `apps.presentation.api.reports.OwnReportView` |
| `/api/v1/me/support-requests` | GET, POST | signed_in | `apps.presentation.api.support.SupportRequestsView` |
| `/api/v1/me/support-requests/<str:request_id>` | GET | signed_in | `apps.presentation.api.support.SupportRequestDetailView` |
| `/api/v1/notifications` | GET | signed_in | `apps.presentation.api.notifications.NotificationListView` |
| `/api/v1/notifications/<str:notification_id>/read` | POST | signed_in | `apps.presentation.api.notifications.MarkReadView` |
| `/api/v1/notifications/read-all` | POST | signed_in | `apps.presentation.api.notifications.MarkAllReadView` |
| `/api/v1/notifications/unread-count` | GET | signed_in | `apps.presentation.api.notifications.UnreadCountView` |
| `/api/v1/password-reset-requests` | POST | public | `apps.presentation.api.passwords.PasswordResetRequestView` |
| `/api/v1/password-resets/confirm` | POST | public | `apps.presentation.api.passwords.PasswordResetConfirmView` |
| `/api/v1/public/agencies/<str:agency_id>` | GET | public | `apps.presentation.api.public_listings.PublicAgencyView` |
| `/api/v1/public/agencies/<str:agency_id>/listings` | GET | public | `apps.presentation.api.public_listings.PublicAgencyListingsView` |
| `/api/v1/public/filters` | GET | public | `apps.presentation.api.public_listings.PublicFiltersView` |
| `/api/v1/public/listings` | GET | public | `apps.presentation.api.public_listings.PublicListingsView` |
| `/api/v1/public/listings/<str:listing_id>` | GET | public | `apps.presentation.api.public_listings.PublicListingDetailView` |
| `/api/v1/public/listings/<str:listing_id>/estimate` | GET | public | `apps.presentation.api.estimates.PublicListingEstimateView` |
| `/api/v1/public/listings/<str:listing_id>/views` | POST | public | `apps.presentation.api.agency_dashboard.ListingViewBeaconView` |
| `/api/v1/public/search` | POST | public | `apps.presentation.api.search.PublicSearchView` |
| `/api/v1/public/search/transcription` | POST | public | `apps.presentation.api.search.PublicSearchTranscriptionView` |
| `/api/v1/reference/amenities` | GET | public | `apps.presentation.api.reference.AmenityView` |
| `/api/v1/reference/cities` | GET | public | `apps.presentation.api.reference.CityView` |
| `/api/v1/reference/cities/<str:city_id>/localities` | GET | public | `apps.presentation.api.reference.LocalityView` |
| `/api/v1/registrations/agencies` | POST | public | `apps.presentation.api.registrations.AgencyRegistrationView` |
| `/api/v1/registrations/individuals` | POST | public | `apps.presentation.api.registrations.IndividualRegistrationView` |
| `/api/v1/registry` | GET | public | `apps.presentation.api.registry.RegistryView` |
| `/api/v1/schema` | GET | public | `apps.presentation.api.schema.PublicSchemaView` |
| `/api/v1/session` | DELETE, POST | public | `apps.presentation.api.sessions.SessionView` |
<!-- END GENERATED ROUTES -->

Routes shown as `public` whose owner check lives in a selector: the Listing media endpoint (`image_for_serving`, which asks `listings.policy.can_view`: an image of a public Listing goes to anyone, any other only to its owner, see `docs/visibility.md`), the owner
file routes of Story 2.11 (`own_image_for_serving`: a Visitor, another account, the other type and an administrator get 404)
and the public Agency logo (`logo_for_serving`: served only while the Agency account is Active, otherwise 404).

The estimate of a Listing (`/api/v1/public/listings/{id}/estimate`, Stories 4.6 and 4.7) is public for a publicly
visible Listing and `not_found` for everyone otherwise, the owner included (who sees it in the Draft preview). A
Visitor reads only what is stored; recomputing (`POST /api/v1/listings/{id}/estimate`) is the owner's alone: Visitor
401, another account 404, Suspended or Deactivated 403.

Favourites (Story 3.7, `/api/v1/me/favourites*`) are the caller's own by construction: a Visitor gets 401, an administrator
or Deactivated account 403, a Suspended account may view and remove but not save (403), and another account sees an empty
list and gets `not_found` when removing a Listing it has not saved (`docs/listings.md`).

## Extending the matrix in later phases

- Each new route appears in the table above once it is added to a urlconf; pick its permission class deliberately.
- Story 1.12 adds the capability matrix (`identity.can(actor, capability)`); route permissions stay coarse and each
  service checks its own capability.
- `SessionAuthentication` (Story 1.17) sets `request.authenticated_identity` from a verified session cookie; anything else is a Visitor. `/api/v1/session` serves sign-in (public, CSRF) and sign-out (signed in, CSRF) on one route, so the table shows `public`; see `docs/sessions.md`.
- For each new service or selector, add a test row per caller kind (Visitor, non-owner, owner, admin, system) that
  shows the refusal code, using `not_found` for any resource the caller may not know about.
