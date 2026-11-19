"""DRF throttle backed by `kernel.ratelimit`.

A view opts in with `throttle_classes = [PolicyThrottle]` and `rate_limit_scope = "<scope>"`.
The 429 envelope and `Retry-After` header come from the standard exception handler.
"""

from django.core.exceptions import ImproperlyConfigured
from rest_framework.throttling import BaseThrottle

from apps.kernel import ratelimit
from apps.presentation.api.actors import get_actor


class PolicyThrottle(BaseThrottle):
    def allow_request(self, request, view):
        scope = getattr(view, "rate_limit_scope", None)
        if not scope:
            raise ImproperlyConfigured("PolicyThrottle needs rate_limit_scope on the view")
        decision = ratelimit.hit_client(scope, get_actor(request), request)
        self._wait = decision.retry_after
        return decision.allowed

    def wait(self):
        return self._wait
