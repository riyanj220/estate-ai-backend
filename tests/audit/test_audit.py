import uuid

import pytest
from django.db import connection, transaction

from apps.kernel import audit
from apps.kernel.actor import Actor
from apps.kernel.models import AuditRecord

ADMIN = uuid.UUID("33333333-3333-4333-8333-333333333333")
TARGET = uuid.UUID("44444444-4444-4444-8444-444444444444")


@pytest.fixture(autouse=True)
def isolated_actions(monkeypatch):
    monkeypatch.setattr(audit, "_actions", dict(audit._actions))
    audit.register_action(
        "probe.changed", {"flag": bool, "count": int, "ref": uuid.UUID, "state": ["active", "off"]}
    )
    audit.register_action("probe.touched", {})


def good(**over):
    base = {"flag": True, "count": 3, "ref": TARGET, "state": "active"}
    return {**base, **over}


def test_writes_row_in_callers_transaction(db):
    with transaction.atomic():
        audit.record(Actor.admin(ADMIN, "req-1"), "probe.changed", "account", TARGET, good())
    row = AuditRecord.objects.get()
    assert (row.actor_id, row.actor_kind, row.action) == (ADMIN, "admin", "probe.changed")
    assert (row.target_type, row.target_id, row.reason, row.request_id) == (
        "account",
        TARGET,
        "",
        "req-1",
    )
    assert row.metadata == {"flag": True, "count": 3, "ref": str(TARGET), "state": "active"}


def test_system_actor_has_null_actor_id(db):
    audit.record(Actor.system(), "probe.touched", "account", TARGET)
    assert AuditRecord.objects.get().actor_id is None


@pytest.mark.django_db(transaction=True)
def test_rolled_back_action_leaves_no_record():
    with pytest.raises(KeyError):
        with transaction.atomic():
            audit.record(Actor.system(), "probe.touched", "account", TARGET)
            raise KeyError
    assert not AuditRecord.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_outside_transaction_is_refused():
    assert not connection.in_atomic_block
    with pytest.raises(RuntimeError):
        audit.record(Actor.system(), "probe.touched", "account", TARGET)
    assert not AuditRecord.objects.exists()


def test_unregistered_action_refused(db):
    with pytest.raises(ValueError):
        audit.record(Actor.system(), "probe.unknown", "account", TARGET)
    assert not AuditRecord.objects.exists()


@pytest.mark.parametrize(
    "metadata",
    [
        {**good(), "extra": 1},
        {"flag": True, "count": 3, "ref": TARGET},
        good(flag="yes"),
        good(count=True),
        good(count="3"),
        good(ref="not-a-uuid"),
        good(state="other"),
        good(state=None),
        good(count=15551234567),
        good(count=-4155551234),
        good(count=1234567),
        good(state="+44 7700 900123"),
        good(state="jane@example.com"),
        good(state="12 High Street"),
        good(state="call me later please"),
        good(state="active 1234567"),
        None,
        [],
    ],
)
def test_bad_metadata_refused_and_nothing_written(db, metadata):
    with pytest.raises(ValueError) as exc:
        audit.record(Actor.system(), "probe.changed", "account", TARGET, metadata)
    assert "@" not in str(exc.value)
    assert not AuditRecord.objects.exists()


def test_free_text_key_rejected_for_empty_schema(db):
    with pytest.raises(ValueError):
        audit.record(Actor.system(), "probe.touched", "account", TARGET, {"note": "0712345678"})


def test_bad_target_arguments_refused(db):
    with pytest.raises(ValueError):
        audit.record(Actor.system(), "probe.touched", "Account", TARGET)
    with pytest.raises(ValueError):
        audit.record(Actor.system(), "probe.touched", "account", str(TARGET))


@pytest.mark.parametrize("name", ["probe", "Probe.changed", "probe.changed.again", "a.b-c", ".x"])
def test_bad_action_name_refused_at_registration(name):
    with pytest.raises(ValueError):
        audit.register_action(name, {})


@pytest.mark.parametrize(
    "schema",
    [{"Key": bool}, {"k": str}, {"k": []}, {"k": ["a1234567"]}, {"k": [1]}, "nope"],
)
def test_bad_schema_refused_at_registration(schema):
    with pytest.raises(ValueError):
        audit.register_action("probe.bad", schema)


def test_registration_idempotent_and_conflict_errors():
    audit.register_action("probe.touched", {})
    audit.register_action(
        "probe.changed", {"flag": bool, "count": int, "ref": uuid.UUID, "state": ("off", "active")}
    )
    with pytest.raises(ValueError):
        audit.register_action("probe.touched", {"flag": bool})


def test_six_digit_int_is_allowed(db):
    audit.record(Actor.system(), "probe.changed", "account", TARGET, good(count=-123456))
    assert AuditRecord.objects.get().metadata["count"] == -123456


def test_unhashable_enumeration_member_is_value_error():
    with pytest.raises(ValueError):
        audit.register_action("probe.bad", {"k": [["a"], "b"]})


def test_public_record_refuses_private_read_action(db):
    with pytest.raises(ValueError):
        audit.record(Actor.system(), audit.READ_ACTION, "account", TARGET)
    assert not AuditRecord.objects.exists()


def test_record_has_no_reason_parameter(db):
    with pytest.raises(TypeError):
        audit.record(Actor.system(), "probe.touched", "account", TARGET, None, "free text")


@pytest.mark.parametrize("reason", [None, 5, "x" * 301, "bad\x00nul", "line\nbreak", "tab\there"])
def test_reasoned_writer_validates_reason(db, reason):
    with pytest.raises(ValueError):
        audit._record_reasoned(Actor.system(), audit.READ_ACTION, "account", TARGET, reason)
    assert not AuditRecord.objects.exists()


def test_reasoned_writer_accepts_valid_reason(db):
    audit._record_reasoned(Actor.system(), audit.READ_ACTION, "account", TARGET, "x" * 300)
    assert len(AuditRecord.objects.get().reason) == 300


def test_request_id_truncated_to_64(db):
    audit.record(Actor.system("r" * 100), "probe.touched", "account", TARGET)
    assert len(AuditRecord.objects.get().request_id) == 64


def test_missing_request_id_stored_empty(db):
    audit.record(Actor.system(None), "probe.touched", "account", TARGET)
    audit.record(Actor.system(), "probe.touched", "account", TARGET)
    assert set(AuditRecord.objects.values_list("request_id", flat=True)) == {""}


@pytest.mark.parametrize(
    ("kind", "actor_id"),
    [("robot", None), ("admin", None), ("user", None), ("visitor", ADMIN), ("system", ADMIN)],
)
def test_database_refuses_inconsistent_actor(db, kind, actor_id):
    from django.db import IntegrityError
    from django.utils import timezone

    with pytest.raises(IntegrityError), transaction.atomic():
        AuditRecord.objects.create(
            actor_kind=kind,
            actor_id=actor_id,
            action="probe.touched",
            target_type="account",
            target_id=TARGET,
            created_at=timezone.now(),
        )
