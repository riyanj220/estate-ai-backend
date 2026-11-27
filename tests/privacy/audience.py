"""Who can see each route's response (Story 3.2). Both privacy gates start here.

The permission comes from the authorisation matrix source (`route_table`): `public` and `admin`
need nothing more. A permission class cannot say whether a signed-in route shows only the
caller's own data, so every signed-in route outside `/api/v1/me` is declared below. A route that
cannot be classified fails the gates.
"""

from apps.presentation.api.authz_matrix import route_table

PUBLIC, OTHER_ACCOUNT, OWNER, ADMIN = "public", "other-account", "owner", "admin"

ME = "/api/v1/me"  # the caller's own data by construction

# OWNER: the service refuses everyone but the owning account. A route that shows one account
# anything about another is OTHER_ACCOUNT, and its response schema is then walked like a public one.
SIGNED_IN = {
    "/api/v1/assistant/sessions": OWNER,
    "/api/v1/assistant/sessions/<str:session_id>": OWNER,
    "/api/v1/assistant/sessions/<str:session_id>/turns": OWNER,
    "/api/v1/estimates": OWNER,
    "/api/v1/listings": OWNER,
    "/api/v1/listings/<str:listing_id>/estimate": OWNER,
    "/api/v1/listings/<str:listing_id>": OWNER,
    "/api/v1/listings/<str:listing_id>/correction": OWNER,
    "/api/v1/listings/<str:listing_id>/images": OWNER,
    "/api/v1/listings/<str:listing_id>/images/<str:image_id>": OWNER,
    "/api/v1/listings/<str:listing_id>/preview": OWNER,
    "/api/v1/listings/<str:listing_id>/publish": OWNER,
    "/api/v1/listings/<str:listing_id>/republish": OWNER,
    "/api/v1/listings/<str:listing_id>/status": OWNER,
    # the caller's own favourites show other accounts' Listings (the public card)
    "/api/v1/me/favourites": OTHER_ACCOUNT,
    "/api/v1/notifications": OWNER,
    "/api/v1/notifications/<str:notification_id>/read": OWNER,
    "/api/v1/notifications/read-all": OWNER,
    "/api/v1/notifications/unread-count": OWNER,
}


def audience(route) -> str:
    if route.is_explicit and route.permissions == ("public",):
        return PUBLIC
    if route.is_explicit and route.permissions == ("admin",):
        return ADMIN
    if route.is_explicit and route.permissions == ("signed_in",):
        if route.path in SIGNED_IN:  # first: it can narrow a `/me` route to OTHER_ACCOUNT
            return SIGNED_IN[route.path]
        if route.path == ME or route.path.startswith(ME + "/"):
            return OWNER
    raise AssertionError(
        f"cannot tell who sees {route.path} {route.permissions}: "
        "add it to SIGNED_IN in tests/privacy/audience.py"
    )


def routes():
    found = route_table("config.urls")
    assert found
    return found
