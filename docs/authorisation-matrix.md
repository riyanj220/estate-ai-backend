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

## Routes

Generated from the URL configuration; do not edit by hand. Regenerate with `uv run python scripts/authz_matrix.py`.
`tests/authz/test_matrix.py` fails when this table is stale, and `tests/authz/test_routes.py` fails any route
whose permission is not explicit.

<!-- BEGIN GENERATED ROUTES -->
| Route | Methods | Permission | View |
| --- | --- | --- | --- |
| `/api/v1/email-verification-requests` | POST | public | `apps.presentation.api.verifications.VerificationRequestView` |
| `/api/v1/email-verifications/confirm` | POST | public | `apps.presentation.api.verifications.ConfirmEmailView` |
| `/api/v1/health` | GET, HEAD | public | `apps.presentation.api.health.WrappedAPIView` |
| `/api/v1/reference/cities` | GET | public | `apps.presentation.api.reference.CityView` |
| `/api/v1/registrations/individuals` | POST | public | `apps.presentation.api.registrations.IndividualRegistrationView` |
| `/api/v1/schema` | GET | public | `apps.presentation.api.schema.PublicSchemaView` |
<!-- END GENERATED ROUTES -->

## Extending the matrix in later phases

- Each new route appears in the table above once it is added to a urlconf; pick its permission class deliberately.
- Story 1.12 adds the capability matrix (`identity.can(actor, capability)`); route permissions stay coarse and each
  service checks its own capability.
- Story 1.17 sets `request.authenticated_identity` from a verified session; until then every caller is a Visitor.
- For each new service or selector, add a test row per caller kind (Visitor, non-owner, owner, admin, system) that
  shows the refusal code, using `not_found` for any resource the caller may not know about.
