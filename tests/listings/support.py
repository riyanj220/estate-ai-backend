"""Helpers shared by the listing service and API tests."""

import uuid

from django.utils import timezone

from apps.identity import services
from apps.identity.models import Account
from apps.kernel.actor import Actor
from apps.kernel.models import RateLimitPolicy

PASSWORD = "correct horse battery staple"
SYSTEM = Actor.system("r")
HOUSE = ("sale", "house", "house")


def seed_cap(limit=20):
    """The open-Draft cap policy (a transactional test may have flushed the seed rows)."""
    RateLimitPolicy.objects.update_or_create(
        scope="open_drafts",
        defaults={"limit": limit, "window_seconds": None, "key_kind": "account", "note": "t"},
    )


def make_account(email="jane.doe@example.com", account_type="individual", state="active"):
    """An account in the given state: unverified, active, suspended or deactivated."""
    view = services.create_account(SYSTEM, email, PASSWORD, account_type)
    now = timezone.now()
    fields = {
        "unverified": {},
        "active": {"email_verified_at": now},
        "suspended": {"email_verified_at": now, "suspended_at": now, "suspension_reason": "r"},
        "deactivated": {"email_verified_at": now, "deactivated_at": now},
    }[state]
    Account.objects.filter(id=view.id).update(**fields)
    return view


def user(view) -> Actor:
    return Actor.user(view.id, "req")


def random_email() -> str:
    return f"{uuid.uuid4().hex[:10]}@example.com"
