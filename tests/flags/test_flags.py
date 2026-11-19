import uuid
from datetime import UTC, datetime

import pytest

from apps.kernel import flags
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import FeatureFlag

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
ADMIN_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")


@pytest.fixture(autouse=True)
def isolated_flags(monkeypatch):
    monkeypatch.setattr(flags, "_declared", dict(flags._declared))
    flags.register_flag("probe_on", True, "test flag on")
    flags.register_flag("probe_off", False, "test flag off")


def test_kernel_declares_ai_gateway_default_on(db):
    assert flags.is_enabled("ai_gateway") is True


def test_declared_flag_without_row_returns_default(db):
    assert flags.is_enabled("probe_on") is True
    assert flags.is_enabled("probe_off") is False


def test_admin_change_records_state_who_and_when(db):
    flags.set_flag(Actor.admin(ADMIN_ID), "probe_on", False, T0)
    assert flags.is_enabled("probe_on") is False
    row = FeatureFlag.objects.get(name="probe_on")
    assert (row.enabled, row.changed_by, row.changed_at) == (False, ADMIN_ID, T0)
    flags.set_flag(Actor.admin(ADMIN_ID), "probe_on", True)
    assert flags.is_enabled("probe_on") is True
    assert FeatureFlag.objects.filter(name="probe_on").count() == 1


def test_system_change_records_null_account(db):
    flags.set_flag(Actor.system(), "probe_off", True, T0)
    assert flags.is_enabled("probe_off") is True
    assert FeatureFlag.objects.get(name="probe_off").changed_by is None


def test_visitor_is_unauthenticated_and_user_is_not_found_and_nothing_changes(db):
    with pytest.raises(DomainError) as visitor:
        flags.set_flag(Actor.visitor(), "probe_on", False)
    assert visitor.value.code == "unauthenticated"
    with pytest.raises(DomainError) as user:
        flags.set_flag(Actor.user(uuid.uuid4()), "probe_on", False)
    assert user.value.code == "not_found"
    assert not FeatureFlag.objects.exists()
    assert flags.is_enabled("probe_on") is True


def test_undeclared_flag_raises_on_read_and_set(db):
    with pytest.raises(flags.UndeclaredFlag):
        flags.is_enabled("ai_gatewya")
    with pytest.raises(flags.UndeclaredFlag):
        flags.set_flag(Actor.system(), "ai_gatewya", True)
    assert not FeatureFlag.objects.exists()


def test_declaration_rules():
    flags.register_flag("probe_on", True, "test flag on")  # identical re-declaration is fine
    with pytest.raises(ValueError):
        flags.register_flag("probe_on", False, "different")
    for bad in ("Bad", "has space", "", "1x"):
        with pytest.raises(ValueError):
            flags.register_flag(bad, True, "x")
    with pytest.raises(ValueError):
        flags.register_flag("probe_x", "yes", "x")


def test_set_flag_rejects_non_bool(db):
    with pytest.raises(ValueError):
        flags.set_flag(Actor.system(), "probe_on", "no")


def test_database_error_returns_the_default_and_logs_class_only(db, monkeypatch, caplog):
    from django.db import DatabaseError

    def broken(*args, **kwargs):
        raise DatabaseError("secret detail")

    monkeypatch.setattr(FeatureFlag.objects, "filter", broken)
    assert flags.is_enabled("probe_on") is True
    assert flags.is_enabled("probe_off") is False
    assert any(r.exception_class == "DatabaseError" for r in caplog.records)
    assert "secret detail" not in caplog.text


def test_non_string_name_is_undeclared(db):
    for bad in (None, 5, ["x"]):
        with pytest.raises(flags.UndeclaredFlag):
            flags.is_enabled(bad)


def test_naive_now_is_rejected(db):
    with pytest.raises(ValueError, match="timezone-aware"):
        flags.set_flag(Actor.system(), "probe_on", False, datetime(2026, 1, 1))
    assert not FeatureFlag.objects.exists()
