import logging
import threading
import uuid
from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.utils import timezone

from apps.kernel import notifications, retention
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord, Notification

A = uuid.UUID("11111111-1111-4111-8111-111111111111")
B = uuid.UUID("22222222-2222-4222-8222-222222222222")
SCHEMA = {
    "count": notifications.int_range(0, 100),
    "flag": bool,
    "state": ["open", "closed"],
    "title": notifications.SHORT_TEXT,
}


@pytest.fixture(autouse=True)
def isolated_kinds(monkeypatch):
    monkeypatch.setattr(notifications, "_kinds", dict(notifications._kinds))
    notifications.register_kind("probe.changed", SCHEMA)
    notifications.register_kind("probe.empty", {})


def good(**over):
    return {"count": 3, "flag": True, "state": "open", "title": "Garden flat", **over}


def store(account=A, age_days=0, read_age_days=None, kind="probe.empty"):
    now = timezone.now()
    return Notification.objects.create(
        account_id=account,
        kind=kind,
        payload={},
        created_at=now - timedelta(days=age_days),
        read_at=None if read_age_days is None else now - timedelta(days=read_age_days),
    )


# --- notify: commit, rollback, autocommit -----------------------------------------------------


def test_notify_stores_after_commit(db, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        notifications.notify(A, "probe.changed", good())
        assert not Notification.objects.exists()
    assert len(callbacks) == 1
    row = Notification.objects.get()
    assert (row.account_id, row.kind, row.read_at) == (A, "probe.changed", None)
    assert row.payload == {
        "count": 3,
        "flag": True,
        "state": "open",
        "title": "Garden flat",
    }


def test_notify_schedules_exactly_one_callback(db, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        notifications.notify(A, "probe.empty")
    assert len(callbacks) == 1
    assert not Notification.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_rollback_stores_nothing():
    with pytest.raises(KeyError):
        with transaction.atomic():
            notifications.notify(A, "probe.empty", {})
            raise KeyError
    assert not Notification.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_rolled_back_savepoint_stores_nothing_but_the_outer_commit_stores_the_rest():
    with transaction.atomic():
        notifications.notify(A, "probe.empty", {})
        with pytest.raises(KeyError):
            with transaction.atomic():
                notifications.notify(B, "probe.empty", {})
                raise KeyError
    assert list(Notification.objects.values_list("account_id", flat=True)) == [A]


@pytest.mark.django_db(transaction=True)
def test_commit_stores_one_row():
    with transaction.atomic():
        notifications.notify(A, "probe.empty", {})
        assert not Notification.objects.exists()
    assert Notification.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_outside_a_transaction_stores_immediately():
    assert not connection.in_atomic_block
    notifications.notify(A, "probe.empty", {})
    assert Notification.objects.count() == 1


# --- validation ------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["probe.unknown", "", None, 3])
def test_unregistered_kind_raises_and_stores_nothing(db, django_capture_on_commit_callbacks, kind):
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(ValueError):
            notifications.notify(A, kind, {})
    assert callbacks == [] and not Notification.objects.exists()


SECRET = "secretvalue"


@pytest.mark.parametrize(
    "payload",
    [
        good(extra=SECRET),
        {k: v for k, v in good().items() if k != "title"},
        good(count=SECRET),
        good(count=101),
        good(count=-1),
        good(count=True),
        good(flag=SECRET),
        good(state=SECRET),
        good(title=SECRET * 20),
        good(title=""),
        good(title="Call me on 03001234567"),
        good(title="mail jane.doe@example.com"),
        good(title="a\nb"),
        good(title=["nested", SECRET]),
        good(count={"nested": SECRET}),
        good(state=[SECRET]),
        "not a mapping",
    ],
)
def test_bad_payload_is_refused_without_echo(db, django_capture_on_commit_callbacks, payload):
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(ValueError) as excinfo:
            notifications.notify(A, "probe.changed", payload)
    assert callbacks == [] and not Notification.objects.exists()
    assert SECRET not in str(excinfo.value) and "03001234567" not in str(excinfo.value)
    assert "example.com" not in str(excinfo.value)


def test_payload_over_one_kilobyte_is_refused(db, monkeypatch):
    monkeypatch.setattr(notifications, "PAYLOAD_MAX_BYTES", 60)
    with pytest.raises(ValueError, match="too large"):
        notifications.notify(A, "probe.changed", good(title="Garden flat with a long name"))


def test_empty_kind_accepts_none_and_rejects_keys(db, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        notifications.notify(A, "probe.empty")
        notifications.notify(A, "probe.empty", {})
    assert Notification.objects.count() == 2
    with pytest.raises(ValueError):
        notifications.notify(A, "probe.empty", {"a": 1})


@pytest.mark.parametrize("account", ["x", None, str(A), 5])
def test_account_must_be_a_uuid(db, account):
    with pytest.raises(ValueError):
        notifications.notify(account, "probe.empty", {})


def test_notify_returns_nothing(db):
    assert notifications.notify(A, "probe.empty", {}) is None


# --- registration ----------------------------------------------------------------------------


def test_register_is_idempotent_for_the_same_schema():
    notifications.register_kind("probe.changed", dict(SCHEMA))
    assert "probe.changed" in notifications.registered_kinds()


def test_register_with_a_different_schema_raises():
    with pytest.raises(ValueError):
        notifications.register_kind("probe.changed", {"flag": bool})
    with pytest.raises(ValueError):
        notifications.register_kind("probe.empty", {"flag": bool})


@pytest.mark.parametrize("kind", ["Bad.kind", "nodot", "a.b.c", "1a.b", "", "a." + "b" * 70, None])
def test_invalid_kind_name_raises(kind):
    with pytest.raises(ValueError):
        notifications.register_kind(kind, {})


@pytest.mark.parametrize(
    "schema",
    [
        {"Bad": bool},
        {"x": str},
        {"x": int},
        {"x": []},
        {"x": [1, 2]},
        {"x": ["has12345678digits"]},
        {"x": ["Upper"]},
        {"x": ["jane@example.com"]},
        {"x": ["has space"]},
        {"x": ["a" * 41]},
        {"x": uuid.UUID},
        {"x": dict},
        "nope",
    ],
)
def test_invalid_schema_raises(schema):
    with pytest.raises(ValueError):
        notifications.register_kind("probe.other", schema)


@pytest.mark.parametrize("bounds", [(0, 1_000_000), (5, 1), (True, 3), (0, "9")])
def test_int_bounds_are_limited(bounds):
    with pytest.raises(ValueError):
        notifications.int_range(*bounds)


def test_shipped_kinds_are_registered_with_empty_payloads():
    kinds = notifications.registered_kinds()
    assert kinds["account.password_changed"] == {}
    assert kinds["account.email_changed"] == {}


# --- storage failure -------------------------------------------------------------------------


def test_storage_failure_is_logged_by_class_only(db, django_capture_on_commit_callbacks, caplog):
    caplog.set_level(logging.DEBUG)
    original = Notification.objects.create

    def boom(**kwargs):
        raise RuntimeError(f"cannot store {kwargs['payload']}")

    Notification.objects.create = boom
    try:
        with django_capture_on_commit_callbacks(execute=True):
            notifications.notify(A, "probe.changed", good(title="Garden flat"))
    finally:
        Notification.objects.create = original
    records = [r for r in caplog.records if r.name == "estate.notifications"]
    assert [r.exception_class for r in records] == ["RuntimeError"]
    wire = "\n".join(r.getMessage() + repr(r.__dict__) for r in caplog.records)
    assert "Garden flat" not in wire and "cannot store" not in wire and str(A) not in wire
    assert not Notification.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_storage_failure_never_changes_the_callers_committed_write(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)

    def boom(**kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(Notification.objects, "create", boom)
    with transaction.atomic():
        AuditRecord.objects.create(
            actor_kind="system",
            action="probe.changed",
            target_type="probe",
            target_id=A,
            created_at=timezone.now(),
        )
        notifications.notify(A, "probe.empty", {})
    assert AuditRecord.objects.filter(action="probe.changed").count() == 1  # the write committed
    assert not Notification.objects.exists()
    (record,) = [r for r in caplog.records if r.name == "estate.notifications"]
    assert record.levelname == "ERROR" and record.event == "notification_store_failed"
    assert (record.notification_kind, record.exception_class) == ("probe.empty", "RuntimeError")


# --- payload size and schema rules -----------------------------------------------------------

BIG = {f"k{i}": notifications.SHORT_TEXT for i in range(8)}


def big_payload(last: str, filler="a"):
    return {**{f"k{i}": filler * 120 for i in range(7)}, "k7": last}


def encoded_size(payload):
    import json

    return len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())


def test_the_real_one_kilobyte_limit(db, django_capture_on_commit_callbacks):
    notifications.register_kind("probe.big", BIG)
    base = encoded_size(big_payload("a"))
    just_under = "a" * (1 + 1024 - base)  # exactly 1024 bytes
    over = just_under + "a"
    assert encoded_size(big_payload(just_under)) == 1024 and len(over) <= 120
    with django_capture_on_commit_callbacks(execute=True):
        notifications.notify(A, "probe.big", big_payload(just_under))
    assert Notification.objects.count() == 1
    with pytest.raises(ValueError, match="too large"):
        notifications.notify(A, "probe.big", big_payload(over))


def test_non_ascii_text_is_counted_in_utf8_not_escaped(db, django_capture_on_commit_callbacks):
    notifications.register_kind("probe.wide", {f"k{i}": notifications.SHORT_TEXT for i in range(4)})
    wide = {f"k{i}": "\u00e9" * 120 for i in range(4)}
    assert encoded_size(wide) < 1024  # 6 bytes each if escaped: it would be refused
    with django_capture_on_commit_callbacks(execute=True):
        notifications.notify(A, "probe.wide", wide)
    assert Notification.objects.count() == 1
    notifications.register_kind(
        "probe.wider", {f"k{i}": notifications.SHORT_TEXT for i in range(5)}
    )
    with pytest.raises(ValueError, match="too large"):
        notifications.notify(A, "probe.wider", {f"k{i}": "\u00e9" * 120 for i in range(5)})


def test_uuid_is_not_a_payload_type(db):
    with pytest.raises(ValueError):
        notifications.register_kind("probe.ref", {"ref": uuid.UUID})


@pytest.mark.parametrize(
    "value",
    [
        "mail me at someone@example.com",
        "see http://example.org",
        "https://example.org",
        "WWW.example.org",
        "x@y",
        "1 2 3 4 5 6 7",
        "Block 1-2-3-4-5-6-7",
        "12 34 56 78",
    ],
)
def test_short_text_refuses_contact_shapes(db, value):
    with pytest.raises(ValueError):
        notifications.notify(A, "probe.changed", good(title=value))


@pytest.mark.parametrize("value", ["Sector 12", "Lahore", "Prime Estates 123456", "Gulberg III"])
def test_short_text_accepts_public_values(db, django_capture_on_commit_callbacks, value):
    with django_capture_on_commit_callbacks(execute=True):
        notifications.notify(A, "probe.changed", good(title=value))
    assert Notification.objects.get().payload["title"] == value


def test_enum_values_follow_the_registration_pattern():
    notifications.register_kind("probe.enum", {"s": ["a_b", "c.d", "e-f", "x" * 40]})
    for bad in (["Caps"], ["with space"], ["a" * 41], ["jane@example.com"], [""], ["né"]):
        with pytest.raises(ValueError):
            notifications.register_kind("probe.enum2", {"s": bad})


# --- reading ---------------------------------------------------------------------------------


def test_list_is_newest_first_and_own_only(db):
    first, second, other = store(A), store(A), store(B)
    rows = list(notifications.list_for(Actor.user(A)))
    assert [r.id for r in rows] == [second.id, first.id]
    assert [r.id for r in notifications.list_for(Actor.admin(B))] == [other.id]


def test_list_state_filter(db):
    unread, read = store(A), store(A, read_age_days=0)
    assert [r.id for r in notifications.list_for(Actor.user(A), "unread")] == [unread.id]
    assert {r.id for r in notifications.list_for(Actor.user(A), "all")} == {unread.id, read.id}
    with pytest.raises(DomainError) as excinfo:
        notifications.list_for(Actor.user(A), "bogus")
    assert excinfo.value.code == "validation_error"


def test_visitor_is_unauthenticated_and_system_is_refused(db):
    calls = (
        notifications.list_for,
        notifications.count_unread,
        notifications.mark_all_read,
        lambda actor: notifications.mark_read(actor, str(uuid.uuid4())),
    )
    for call in calls:
        with pytest.raises(DomainError) as excinfo:
            call(Actor.visitor())
        assert excinfo.value.code == "unauthenticated"
        with pytest.raises(DomainError) as excinfo:
            call(Actor.system())
        assert excinfo.value.code == "forbidden"


def test_count_unread_is_exact_and_own(db):
    store(A)
    store(A)
    store(A, read_age_days=0)
    store(B)
    assert notifications.count_unread(Actor.user(A)) == 2
    assert notifications.count_unread(Actor.user(B)) == 1


def test_mark_read_sets_once_and_is_idempotent(db):
    row = store(A)
    first = notifications.mark_read(Actor.user(A), str(row.id))
    assert first.read_at is not None
    again = notifications.mark_read(Actor.user(A), row.id)
    assert again.read_at == first.read_at
    assert Notification.objects.get().read_at == first.read_at


@pytest.mark.parametrize("raw", ["", "nope", None, 5, "A" * 36, "X" * 36])
def test_mark_read_malformed_or_unknown_is_not_found(db, raw):
    for candidate in (raw, str(uuid.uuid4())):
        with pytest.raises(DomainError) as excinfo:
            notifications.mark_read(Actor.user(A), candidate)
        assert excinfo.value.code == "not_found"


def test_mark_read_of_someone_elses_is_not_found_and_unchanged(db):
    row = store(B)
    with pytest.raises(DomainError) as excinfo:
        notifications.mark_read(Actor.user(A), str(row.id))
    assert excinfo.value.code == "not_found"
    assert Notification.objects.get().read_at is None


def test_mark_all_read_touches_only_the_callers_unread(db):
    mine = [store(A), store(A)]
    done = store(A, read_age_days=2)
    theirs = store(B)
    assert notifications.mark_all_read(Actor.user(A)) == 2
    assert (
        Notification.objects.filter(id__in=[m.id for m in mine], read_at__isnull=False).count() == 2
    )
    assert Notification.objects.get(id=done.id).read_at == done.read_at
    assert Notification.objects.get(id=theirs.id).read_at is None
    assert notifications.mark_all_read(Actor.user(A)) == 0


# --- cleanup ---------------------------------------------------------------------------------


def test_delete_for_account_removes_only_that_account(db):
    store(A)
    store(A, read_age_days=0)
    keep = store(B)
    assert notifications.delete_for_account(A) == 2
    assert list(Notification.objects.all()) == [keep]
    with pytest.raises(ValueError):
        notifications.delete_for_account(str(A))


def test_retention_job_is_registered():
    assert "kernel.notifications" in retention.registered()


def test_retention_runs_from_read_at_and_created_at(db):
    now = timezone.now()
    keep = [
        store(A, age_days=10),
        store(A, age_days=179),  # unread, under 180 days
        store(A, age_days=100, read_age_days=89),  # read 89 days ago
        store(A, age_days=100, read_age_days=1),
    ]
    gone = [
        store(A, age_days=100, read_age_days=91),  # read over 90 days ago
        store(A, age_days=181),  # any, over 180 days
        store(A, age_days=200, read_age_days=1),  # created over 180 days ago, read lately
    ]
    assert notifications.purge_expired(now, True) == 3
    assert Notification.objects.count() == 7  # a dry run deletes nothing
    results = retention.run_all(now)
    assert {r.name: r.count for r in results}["kernel.notifications"] == 3
    left = set(Notification.objects.values_list("id", flat=True))
    assert left == {k.id for k in keep}
    assert not left & {g.id for g in gone}


def test_retention_deletes_in_batches(db, monkeypatch):
    monkeypatch.setattr(notifications, "PURGE_BATCH", 3)
    now = timezone.now()
    for _ in range(8):
        store(A, age_days=200)
    kept = store(A)
    statements = []
    from django.db import connection as conn
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(conn) as queries:
        assert notifications.purge_expired(now, False) == 8
    statements = [q["sql"] for q in queries if q["sql"].startswith("DELETE")]
    assert len(statements) == 3  # 3 + 3 + 2
    assert list(Notification.objects.all()) == [kept]


def test_seq_comes_from_the_column_default_and_increases(db, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        notifications.notify(A, "probe.empty", {})
        notifications.notify(A, "probe.empty", {})
    first, second = Notification.objects.order_by("created_at")
    assert second.seq > first.seq > 0


# --- privacy ---------------------------------------------------------------------------------


def test_canary_values_reach_no_log_audit_or_error(db, django_capture_on_commit_callbacks, caplog):
    caplog.set_level(logging.DEBUG)
    canary = {"title": "jane.doe@example.com", "count": 999, "state": "03001234567"}
    errors = []
    with django_capture_on_commit_callbacks(execute=True):
        for key, value in canary.items():
            try:
                notifications.notify(A, "probe.changed", good(**{key: value}))
            except ValueError as exc:
                errors.append(str(exc))
        notifications.notify(A, "probe.changed", good(title="Open day"))
    assert len(errors) == 3
    wire = "\n".join(errors + [r.getMessage() + repr(r.__dict__) for r in caplog.records])
    for value in ("jane.doe", "example.com", "03001234567"):
        assert value not in wire
    assert not AuditRecord.objects.exists()
    assert Notification.objects.count() == 1


# --- concurrency -----------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_parallel_mark_read_keeps_one_read_time():
    row = store(A)
    barrier, results = threading.Barrier(8), []

    def work():
        try:
            barrier.wait()
            results.append(notifications.mark_read(Actor.user(A), str(row.id)).read_at)
        except Exception as exc:  # noqa: BLE001
            results.append(type(exc).__name__)
        finally:
            connection.close()

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stored = Notification.objects.get().read_at
    assert stored is not None
    assert results == [stored] * 8


@pytest.mark.django_db(transaction=True)
def test_parallel_notify_loses_and_merges_nothing():
    barrier, failures = threading.Barrier(10), []

    def work():
        try:
            barrier.wait()
            with transaction.atomic():
                notifications.notify(A, "probe.empty", {})
        except Exception as exc:  # noqa: BLE001
            failures.append(type(exc).__name__)
        finally:
            connection.close()

    threads = [threading.Thread(target=work) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert failures == []
    seqs = list(Notification.objects.values_list("seq", flat=True))
    assert len(seqs) == 10 and len(set(seqs)) == 10
    assert Notification.objects.values("id").distinct().count() == 10
