import pytest

from apps.kernel.models import RateLimitPolicy
from tests.listings.support import seed_cap, seed_media_policies


def set_policy(scope, limit, window=60):
    RateLimitPolicy.objects.update_or_create(
        scope=scope,
        defaults={"limit": limit, "window_seconds": window, "key_kind": "account", "note": "t"},
    )


@pytest.fixture(autouse=True)
def policies(db):
    seed_cap(200)
    seed_media_policies()
    set_policy("message_send", 60)
    set_policy("conversation_new", 20, 86400)
