"""Security headers on every response, including errors.

The CSP is API-only: the API serves JSON, so nothing may load or frame.
HSTS is emitted here, not by Django's SecurityMiddleware, because TLS ends at the
proxy and the app must not trust X-Forwarded-Proto to learn the request was secure.
"""

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.conf import settings

CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

STATIC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Content-Security-Policy": CSP,
}


class SecurityHeadersMiddleware:
    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.is_async = iscoroutinefunction(get_response)
        if self.is_async:
            markcoroutinefunction(self)

    def __call__(self, request):
        if self.is_async:
            return self._acall(request)
        return self._apply(self.get_response(request))

    async def _acall(self, request):
        return self._apply(await self.get_response(request))

    def _apply(self, response):
        for name, value in STATIC_HEADERS.items():
            response[name] = value
        seconds = getattr(settings, "SECURE_HSTS_SECONDS", 0)
        if seconds:
            value = f"max-age={seconds}"
            if getattr(settings, "SECURE_HSTS_INCLUDE_SUBDOMAINS", False):
                value += "; includeSubDomains"
            response["Strict-Transport-Security"] = value
        return response
