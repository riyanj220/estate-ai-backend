import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection, transaction

from apps.kernel import idempotency
from apps.kernel.errors import DomainError
from apps.kernel.models import IdempotencyRecord

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
A = uuid.UUID("11111111-1111-4111-8111-111111111111")
B = uuid.UUID("22222222-2222-4222-8222-222222222222")
H1 = idempotency.request_fingerprint({"a": 1})
H2 = idempotency.request_fingerprint({"a": 2})


def test_first_claim_is_new_and_stores_hash_only(db):
    result = idempotency.claim("probe", A, "k1", H1, T0)
    assert result.is_new and result.result_ref is None
    record = IdempotencyRecord.objects.get()
    assert (record.scope, record.account_id, record.key, record.request_hash) == (
        "probe",
        A,
        "k1",
        H1,
    )
    assert record.result_ref is None


def test_replay_returns_the_original_result_reference(db):
    idempotency.claim("probe", A, "k1", H1, T0).complete("listing:abc-123")
    replay = idempotency.claim("probe", A, "k1", H1, T0 + timedelta(hours=23))
    assert not replay.is_new and replay.result_ref == "listing:abc-123"
    assert IdempotencyRecord.objects.count() == 1


def test_replay_of_an_uncompleted_claim_has_no_reference(db):
    idempotency.claim("probe", A, "k1", H1, T0)
    replay = idempotency.claim("probe", A, "k1", H1, T0)
    assert not replay.is_new and replay.result_ref is None
    with pytest.raises(RuntimeError):
        replay.complete("listing:x")


def test_different_request_hash_is_refused(db):
    idempotency.claim("probe", A, "k1", H1, T0)
    with pytest.raises(DomainError) as caught:
        idempotency.claim("probe", A, "k1", H2, T0 + timedelta(hours=1))
    assert caught.value.code == "idempotency_key_reused"


def test_other_account_or_scope_is_independent(db):
    idempotency.claim("probe", A, "k1", H1, T0)
    assert idempotency.claim("probe", B, "k1", H2, T0).is_new
    assert idempotency.claim("other", A, "k1", H2, T0).is_new


def test_expired_record_is_reclaimed(db):
    idempotency.claim("probe", A, "k1", H1, T0).complete("listing:old")
    later = T0 + timedelta(hours=24, seconds=1)
    again = idempotency.claim("probe", A, "k1", H2, later)
    assert again.is_new and again.result_ref is None
    record = IdempotencyRecord.objects.get()
    assert record.request_hash == H2 and record.result_ref is None and record.created_at == later
    assert not idempotency.claim("probe", A, "k1", H2, later).is_new


def test_purge_deletes_only_records_older_than_seven_days(db):
    idempotency.claim("probe", A, "old", H1, T0)
    idempotency.claim("probe", A, "new", H1, T0 + timedelta(days=3))
    assert idempotency.purge_expired(T0 + timedelta(days=7, seconds=1)) == 1
    assert list(IdempotencyRecord.objects.values_list("key", flat=True)) == ["new"]


def test_purge_rejects_naive_now(db):
    with pytest.raises(ValueError):
        idempotency.purge_expired(datetime(2026, 1, 1))


def test_claim_rejects_naive_now(db):
    with pytest.raises(ValueError):
        idempotency.claim("probe", A, "k", H1, datetime(2026, 1, 1))


@pytest.mark.parametrize("key", ["", "x" * 129, "has space", "semi;colon", "é", "a/b", "a\nb"])
def test_invalid_keys_are_rejected(db, key):
    with pytest.raises(ValueError):
        idempotency.claim("probe", A, key, H1, T0)


def test_key_alphabet_and_length_limits_are_accepted(db):
    assert idempotency.claim("probe", A, "x" * 128, H1, T0).is_new
    assert idempotency.claim("probe", A, "Az09._:-", H1, T0).is_new


@pytest.mark.parametrize(
    "ref", ["", "listing", "listing:", ":id", "List:1", "a b:1", "t:" + "x" * 65]
)
def test_invalid_result_refs_are_rejected(db, ref):
    result = idempotency.claim("probe", A, "k", H1, T0)
    with pytest.raises(ValueError):
        result.complete(ref)


def test_bad_hash_scope_and_account_are_rejected(db):
    with pytest.raises(ValueError):
        idempotency.claim("probe", A, "k", "not-a-hash", T0)
    with pytest.raises(ValueError):
        idempotency.claim("Bad Scope", A, "k", H1, T0)
    with pytest.raises(ValueError):
        idempotency.claim("probe", str(A), "k", H1, T0)


def test_claim_outside_a_transaction_is_refused():
    with pytest.raises(RuntimeError):
        idempotency.claim("probe", A, "k", H1, T0)


def test_fingerprint_is_canonical():
    assert idempotency.request_fingerprint({"a": 1, "b": [1, 2]}) == (
        idempotency.request_fingerprint({"b": [1, 2], "a": 1})
    )
    assert idempotency.request_fingerprint({"a": 1}) != idempotency.request_fingerprint({"a": "1"})
    assert idempotency.request_fingerprint({"id": A}) == idempotency.request_fingerprint(
        {"id": str(A)}
    )
    with pytest.raises(TypeError):
        idempotency.request_fingerprint({"x": object()})


@pytest.mark.django_db(transaction=True)
def test_rolled_back_claim_leaves_no_record():
    with pytest.raises(RuntimeError), transaction.atomic():
        idempotency.claim("probe", A, "k1", H1, T0)
        raise RuntimeError
    assert IdempotencyRecord.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_committed_claim_persists_and_conflicting_reuse_rolls_back():
    with transaction.atomic():
        idempotency.claim("probe", A, "k1", H1, T0).complete("listing:one")
    with pytest.raises(DomainError), transaction.atomic():
        idempotency.claim("probe", A, "k1", H2, T0)
    record = IdempotencyRecord.objects.get()
    assert (record.request_hash, record.result_ref) == (H1, "listing:one")
    assert IdempotencyRecord.objects.count() == 1


def test_exactly_24_hours_is_expired_and_just_under_is_not(db):
    idempotency.claim("probe", A, "k1", H1, T0)
    just_under = T0 + timedelta(hours=24) - timedelta(microseconds=1)
    with pytest.raises(DomainError):
        idempotency.claim("probe", A, "k1", H2, just_under)
    assert idempotency.claim("probe", A, "k1", H2, T0 + timedelta(hours=24)).is_new


def test_purge_boundary_at_exactly_seven_days(db):
    idempotency.claim("probe", A, "k1", H1, T0)
    assert idempotency.purge_expired(T0 + timedelta(days=7)) == 0
    assert idempotency.purge_expired(T0 + timedelta(days=7, microseconds=1)) == 1


def test_complete_twice_is_refused(db):
    result = idempotency.claim("probe", A, "k1", H1, T0)
    result.complete("listing:one")
    with pytest.raises(RuntimeError):
        result.complete("listing:two")
    assert IdempotencyRecord.objects.get().result_ref == "listing:one"


def test_complete_after_purge_is_refused(db):
    result = idempotency.claim("probe", A, "k1", H1, T0)
    idempotency.purge_expired(T0 + timedelta(days=8))
    with pytest.raises(RuntimeError):
        result.complete("listing:one")


@pytest.mark.django_db(transaction=True)
def test_complete_outside_a_transaction_is_refused():
    with transaction.atomic():
        result = idempotency.claim("probe", A, "k1", H1, T0)
    with pytest.raises(RuntimeError):
        result.complete("listing:one")


def test_fingerprint_rejects_odd_input():
    for bad in ({1: "a"}, {"a": {2: "b"}}, {"a": [{3: "c"}]}):
        with pytest.raises(ValueError):
            idempotency.request_fingerprint(bad)
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            idempotency.request_fingerprint({"a": bad})
    with pytest.raises(ValueError):
        idempotency.request_fingerprint({"at": datetime(2026, 1, 1)})
    assert idempotency.request_fingerprint({"at": T0, "n": None, "t": True, "f": 1.5})


@pytest.mark.django_db(transaction=True)
def test_concurrent_claims_create_exactly_one_record():
    workers = 8
    barrier = threading.Barrier(workers)
    results, errors = [], []

    def work():
        try:
            barrier.wait()
            with transaction.atomic():
                results.append(idempotency.claim("probe", A, "race", H1, T0).is_new)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=work) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert results.count(True) == 1 and results.count(False) == workers - 1
    assert IdempotencyRecord.objects.count() == 1
