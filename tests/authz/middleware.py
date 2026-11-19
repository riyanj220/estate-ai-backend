"""Stand-in for Story 1.17's trusted authentication: tests set the identity through a
header that only exists in test settings. Production code never reads it."""

from dataclasses import dataclass


@dataclass
class Identity:
    kind: str
    account_id: str


class HeaderIdentityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        raw = request.headers.get("X-Test-Identity")
        if raw:
            kind, _, account_id = raw.partition(":")
            request.authenticated_identity = Identity(kind, account_id)
        return self.get_response(request)
