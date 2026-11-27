import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.kernel import ratelimit
from apps.kernel.models import RateLimitPolicy

H, D, M = 3600, 86400, 60

EXPECTED = {
    "registration": (10, H, "client"),
    "verification_email": (5, H, "account"),
    "password_reset_email": (5, H, "account"),
    "messages": (60, M, "account"),
    "conversations_new": (20, D, "account"),
    "favourites": (500, None, "account"),
    "reports": (10, D, "account"),
    "support_requests": (5, D, "account"),
    "open_drafts": (20, None, "account"),
    "assistant_messages": (30, H, "account"),
    "ai_search_visitor": (10, M, "client"),
    "ai_search_account": (30, H, "account"),
    "ai_voice_visitor": (10, M, "client"),
    "ai_voice_account": (30, H, "account"),
    "message_send": (60, 60, "account"),
    "conversation_new": (20, 24 * H, "account"),
    "listing_view": (300, H, "client"),
    "estimates": (20, H, "account"),
    "sign_in": (5, 900, "client"),
    "verification_resend": (5, H, "client"),
    "password_reset_request": (5, H, "client"),
    "password_reset_confirm": (20, H, "client"),
    "password_change_failed": (5, 900, "account"),
    "email_change_request": (5, H, "account"),
    "email_change_confirm": (20, H, "client"),
    "admin_sign_in": (5, 900, "client"),
    "admin_sign_in_account": (20, H, "account"),
    "storage_cap_bytes_individual": (209_715_200, None, "account"),
    "storage_cap_bytes_agency": (1_073_741_824, None, "account"),
    "image_upload": (30, H, "account"),
    "image_order": (120, H, "account"),
    "profile_image_upload": (10, H, "account"),
}


def test_seed_lists_every_nfr10_scope(seeded):
    rows = {p.scope: (p.limit, p.window_seconds, p.key_kind) for p in RateLimitPolicy.objects.all()}
    assert rows == EXPECTED
    assert all(p.note for p in RateLimitPolicy.objects.all())


def test_limit_for_returns_policy_including_caps(seeded):
    cap = ratelimit.limit_for("favourites")
    assert (cap.limit, cap.window_seconds) == (500, None)
    assert ratelimit.limit_for("estimates").window_seconds == 3600


def test_unknown_scope_is_a_configuration_error(db):
    with pytest.raises(ImproperlyConfigured):
        ratelimit.limit_for("nope")
    with pytest.raises(ImproperlyConfigured):
        ratelimit.hit("nope", "k")


def test_hit_on_a_cap_scope_raises(seeded):
    with pytest.raises(ImproperlyConfigured, match="cap"):
        ratelimit.hit("favourites", "k")


def test_policy_read_failure_fails_open_but_unknown_scope_still_raises(probe, monkeypatch, caplog):
    def broken(scope):
        raise ratelimit.DatabaseError("detail")

    monkeypatch.setattr(ratelimit, "limit_for", broken)
    with caplog.at_level("WARNING", logger="estate.ratelimit"):
        assert ratelimit.hit("probe", "k").allowed
    assert caplog.records[-1].exception_class == "DatabaseError"
    monkeypatch.undo()
    with pytest.raises(ImproperlyConfigured):
        ratelimit.hit("missing", "k")


def test_naive_now_is_rejected(probe):
    from datetime import datetime

    with pytest.raises(ValueError):
        ratelimit.hit("probe", "k", datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        ratelimit.purge_expired(datetime(2026, 1, 1))
