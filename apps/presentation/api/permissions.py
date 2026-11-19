"""Route permissions: a first gate only; services enforce policy themselves (AD-22).

DRF's default is `DenyByDefault`, so a view that declares nothing is closed. Every view
declares one of the other three explicitly. `label` feeds the authorisation matrix.
"""

from rest_framework.permissions import BasePermission

from apps.kernel.actor import require_admin, require_signed_in
from apps.presentation.api.actors import get_actor


class DenyByDefault(BasePermission):
    label = "deny"

    def has_permission(self, request, view):
        return False


class AllowPublic(BasePermission):
    label = "public"

    def has_permission(self, request, view):
        get_actor(request)
        return True


class RequireSignedIn(BasePermission):
    """No authentication classes exist, so DRF would answer 403; we raise `unauthenticated`."""

    label = "signed_in"

    def has_permission(self, request, view):
        require_signed_in(get_actor(request))
        return True


class RequireAdmin(BasePermission):
    label = "admin"

    def has_permission(self, request, view):
        require_admin(get_actor(request))
        return True


EXPLICIT = (AllowPublic, RequireSignedIn, RequireAdmin)
