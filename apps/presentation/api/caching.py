"""The one way an answer is marked cacheable by anyone (public images and public Listings)."""

PUBLIC_CACHE_SECONDS = 60


def cache_publicly(request, response) -> None:
    """For an answer that does not depend on who asks: `public, max-age=60`, no cookie and
    nothing varying by cookie. A request that arrives with a stale or malformed CSRF cookie
    would otherwise make Django's CSRF middleware reissue it (`Set-Cookie`, `Vary: Cookie`)."""
    response["Cache-Control"] = f"public, max-age={PUBLIC_CACHE_SECONDS}"
    request._request.META["CSRF_COOKIE_NEEDS_UPDATE"] = False
    # belt and braces: nothing else may leave a cookie or a cookie variance behind
    response.cookies.clear()
    parts = [p.strip() for p in response.get("Vary", "").split(",") if p.strip()]
    kept = [p for p in parts if p.lower() != "cookie"]
    if kept:
        response["Vary"] = ", ".join(kept)
    elif response.has_header("Vary"):
        del response["Vary"]
