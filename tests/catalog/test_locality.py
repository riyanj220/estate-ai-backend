"""Localities (Story 2.7): the model, the selectors, the admin service, the seed migration and the
public endpoint. Names in tests are low-entropy fake words."""

import importlib
import uuid

import pytest
import yaml
from django.apps import apps as django_apps
from django.core.management import call_command
from django.db import IntegrityError, connections, transaction

from apps.catalog import selectors, services
from apps.catalog.models import City, Locality
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord

ADMIN = Actor.admin(uuid.UUID("33333333-3333-4333-8333-333333333333"), "req-1")
SEED = importlib.import_module("apps.catalog.migrations.0004_seed_karachi_localities")


def run_seed():
    SEED.seed_karachi_localities(django_apps, None)


@pytest.fixture
def karachi(db):
    Locality.objects.all().delete()
    City.objects.all().delete()  # a transactional test elsewhere may have flushed the seed
    return City.objects.create(name="Karachi")


@pytest.fixture
def lahore(karachi):
    return City.objects.create(name="Lahore")


# --- the seed list ----------------------------------------------------------------------------


def test_the_seed_list_is_clean_and_has_no_duplicates():
    names = [*SEED.AREAS, *SEED.SOCIETIES]
    assert len(SEED.AREAS) == 39 and len(SEED.SOCIETIES) == 32
    assert len({n.lower() for n in names}) == len(names)
    for name in names:
        assert services._clean_name(name, digits=True) == name, name  # every name passes the rule
        assert "(" not in name and ")" not in name
    assert {"DHA Phase 6", "Askari 4", "Precinct 1 Bahria", "Block 13-D Gulshan"} <= set(names)


def test_seed_creates_the_lists_for_karachi_only(karachi, lahore):
    run_seed()
    rows = list(Locality.objects.all())
    assert len(rows) == len(SEED.AREAS) + len(SEED.SOCIETIES)
    assert {r.city_id for r in rows} == {karachi.id}
    kinds = {r.name: r.kind for r in rows}
    assert kinds["Clifton"] == "area" and kinds["DHA Phase 6"] == "society"
    assert all(r.is_active for r in rows)
    assert not Locality.objects.filter(city=lahore).exists()


def test_reseed_neither_duplicates_nor_overwrites_an_edited_row(karachi):
    run_seed()
    edited = Locality.objects.get(name="Clifton")
    Locality.objects.filter(pk=edited.pk).update(name="CLIFTON", kind="society", is_active=False)
    Locality.objects.filter(name="Saddar").delete()
    run_seed()
    run_seed()
    assert Locality.objects.filter(name__iexact="clifton").count() == 1
    row = Locality.objects.get(pk=edited.pk)
    assert (row.name, row.kind, row.is_active) == ("CLIFTON", "society", False)
    assert Locality.objects.filter(name="Saddar").count() == 1  # a removed row comes back once
    assert Locality.objects.count() == len(SEED.AREAS) + len(SEED.SOCIETIES)


def test_seed_without_karachi_does_nothing(db):
    Locality.objects.all().delete()
    City.objects.all().delete()
    run_seed()
    assert not Locality.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_the_migration_path_is_idempotent():
    from django.db.migrations.executor import MigrationExecutor

    conn = connections["default"]
    try:
        MigrationExecutor(conn).migrate([("catalog", "0003_locality")])
        City.objects.all().delete()
        City.objects.create(name="Karachi")
        MigrationExecutor(conn).migrate([("catalog", "0004_seed_karachi_localities")])
        first = Locality.objects.count()
        assert first == len(SEED.AREAS) + len(SEED.SOCIETIES)
        run_seed()
        assert Locality.objects.count() == first
        MigrationExecutor(conn).migrate([("catalog", "0003_locality")])
        MigrationExecutor(conn).migrate([("catalog", "0004_seed_karachi_localities")])
        assert Locality.objects.count() == first
    finally:
        MigrationExecutor(conn).migrate(MigrationExecutor(conn).loader.graph.leaf_nodes())


# --- the model and the selectors ----------------------------------------------------------------


def test_the_database_enforces_the_rules(karachi, lahore):
    Locality.objects.create(city=karachi, name="Testville", kind="area")
    with pytest.raises(IntegrityError), transaction.atomic():
        Locality.objects.create(city=karachi, name="TESTVILLE", kind="society")
    Locality.objects.create(city=lahore, name="Testville", kind="area")  # another city is fine
    with pytest.raises(IntegrityError), transaction.atomic():
        Locality.objects.create(city=karachi, name="Otherville", kind="park")
    with pytest.raises(IntegrityError), transaction.atomic():
        Locality.objects.create(city=karachi, name="x", kind="area")


def test_list_is_active_only_ordered_plain_and_unknown_city_is_none(karachi, lahore):
    Locality.objects.create(city=karachi, name="zebra Block", kind="area")
    Locality.objects.create(city=karachi, name="Alpha Phase 1", kind="society")
    Locality.objects.create(city=karachi, name="Hidden Place", kind="area", is_active=False)
    Locality.objects.create(city=lahore, name="Lahore Only", kind="area")
    result = selectors.list_localities(karachi.id)
    assert [r.name for r in result] == ["Alpha Phase 1", "zebra Block"]
    assert [r.kind for r in result] == ["society", "area"]
    assert not any(isinstance(r, Locality) for r in result)
    with pytest.raises(AttributeError):
        result[0].name = "x"
    assert selectors.list_localities(str(lahore.id))[0].name == "Lahore Only"
    for bad in (uuid.uuid4(), "nope", None, 5, ""):
        assert selectors.list_localities(bad) is None
    lahore.is_active = False
    lahore.save()
    assert selectors.list_localities(lahore.id) is None


def test_get_locality_returns_its_city_and_hides_inactive(karachi, lahore):
    row = Locality.objects.create(city=karachi, name="Testville", kind="area")
    found = selectors.get_locality(row.id)
    assert found == selectors.LocalityRef(row.id, "Testville", "area", karachi.id)
    assert selectors.get_locality(str(row.id)).city_id == karachi.id
    row.is_active = False
    row.save()
    assert selectors.get_locality(row.id) is None
    other = Locality.objects.create(city=lahore, name="Otherville", kind="area")
    lahore.is_active = False
    lahore.save()
    assert selectors.get_locality(other.id) is None  # a locality of an inactive city
    for bad in (uuid.uuid4(), "nope", None, 5, ""):
        assert selectors.get_locality(bad) is None


# --- the admin service ----------------------------------------------------------------------------


def test_admin_creates_a_locality_with_an_audit_record(karachi):
    ref = services.create_locality(ADMIN, karachi.id, "  Test   Phase 7 ", "society")
    assert (ref.name, ref.kind, ref.city_id) == ("Test Phase 7", "society", karachi.id)
    record = AuditRecord.objects.get(action="locality.created")
    assert (record.target_type, record.target_id, record.metadata) == ("locality", ref.id, {})


@pytest.mark.parametrize(
    ("actor", "code"),
    [
        (Actor.visitor(), "unauthenticated"),
        (Actor.user(uuid.uuid4()), "not_found"),
        (Actor.system(), "not_found"),
    ],
)
def test_non_admins_cannot_create(karachi, actor, code):
    with pytest.raises(DomainError) as exc:
        services.create_locality(actor, karachi.id, "Testville", "area")
    assert exc.value.code == code
    assert not Locality.objects.exists() and not AuditRecord.objects.exists()


@pytest.mark.parametrize(
    "name",
    ["", " ", "A", "a" * 81, "7 Testville", "Test (Phase)", "Test!", "Test-", "Test'", None, 5],
)
def test_bad_names_are_refused(karachi, name):
    with pytest.raises(DomainError) as exc:
        services.create_locality(ADMIN, karachi.id, name, "area")
    assert exc.value.code == "validation_error"
    assert not Locality.objects.exists() and not AuditRecord.objects.exists()


@pytest.mark.parametrize("name", ["DHA Phase 6", "Askari 4", "Block 13-D", "Ab", "a" * 80])
def test_digits_are_allowed_in_a_locality_name(karachi, name):
    assert services.create_locality(ADMIN, karachi.id, name, "area").name == name


def test_bad_kind_city_and_duplicates_are_refused(karachi, lahore):
    services.create_locality(ADMIN, karachi.id, "Testville", "area")
    for args in (
        (karachi.id, "Otherville", "park"),
        (karachi.id, "Otherville", None),
        (uuid.uuid4(), "Otherville", "area"),
        ("nope", "Otherville", "area"),
        (karachi.id, "testville", "society"),
    ):
        with pytest.raises(DomainError) as exc:
            services.create_locality(ADMIN, *args)
        assert exc.value.code == "validation_error"
    services.create_locality(ADMIN, lahore.id, "Testville", "area")  # same name, other city
    lahore.is_active = False
    lahore.save()
    with pytest.raises(DomainError):
        services.create_locality(ADMIN, lahore.id, "Newville", "area")
    assert Locality.objects.count() == 2
    assert AuditRecord.objects.filter(action="locality.created").count() == 2


def test_a_lost_race_is_a_validation_error(karachi, monkeypatch):
    Locality.objects.create(city=karachi, name="Testville", kind="area")
    monkeypatch.setattr(services, "_locality_taken", lambda city_id, name: False)
    with transaction.atomic():
        with pytest.raises(DomainError) as exc:
            services.create_locality(ADMIN, karachi.id, "testville", "area")
        assert exc.value.code == "validation_error"
        assert Locality.objects.count() == 1  # the outer transaction is still usable
    assert not AuditRecord.objects.filter(action="locality.created").exists()


@pytest.mark.django_db(transaction=True)
def test_failure_after_the_audit_write_rolls_back_both(monkeypatch):
    city, _ = City.objects.get_or_create(name="Karachi")
    before = Locality.objects.count()
    original = services.audit.record

    def record_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        assert AuditRecord.objects.filter(action="locality.created").exists()
        raise RuntimeError

    monkeypatch.setattr(services.audit, "record", record_then_fail)
    with pytest.raises(RuntimeError):
        services.create_locality(ADMIN, city.id, "Rollbackville", "area")
    assert Locality.objects.count() == before
    assert not AuditRecord.objects.filter(action="locality.created").exists()


# --- the public endpoint --------------------------------------------------------------------------


def url(city_id):
    return f"/api/v1/reference/cities/{city_id}/localities"


def test_endpoint_is_public_and_returns_only_id_name_kind(client, karachi, lahore):
    a = Locality.objects.create(city=karachi, name="Beta Block", kind="area")
    b = Locality.objects.create(city=karachi, name="Alpha Phase 2", kind="society")
    Locality.objects.create(city=karachi, name="Hidden Place", kind="area", is_active=False)
    Locality.objects.create(city=lahore, name="Lahore Only", kind="area")
    response = client.get(url(karachi.id))
    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["items"]
    assert body["items"] == [
        {"id": str(b.id), "name": "Alpha Phase 2", "kind": "society"},
        {"id": str(a.id), "name": "Beta Block", "kind": "area"},
    ]
    assert client.get(url(lahore.id)).json()["items"][0]["name"] == "Lahore Only"


def test_an_empty_known_city_is_an_empty_list(client, karachi, lahore):
    response = client.get(url(lahore.id))
    assert response.status_code == 200 and response.json() == {"items": []}


@pytest.mark.parametrize("bad", ["nope", str(uuid.uuid4()), "1234", "%20", "A" * 80])
def test_an_unknown_or_malformed_city_is_not_found(client, karachi, bad):
    response = client.get(url(bad))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_an_inactive_city_is_not_found(client, lahore):
    lahore.is_active = False
    lahore.save()
    assert client.get(url(lahore.id)).status_code == 404


def test_the_endpoint_is_get_only(client, karachi):
    assert client.post(url(karachi.id)).status_code in (403, 405)
    assert client.delete(url(karachi.id)).status_code in (403, 405)


def test_openapi_documents_names_as_plain_strings_and_kind_as_a_closed_list(tmp_path):
    target = tmp_path / "o.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    schema = yaml.safe_load(target.read_text())
    assert "/api/v1/reference/cities/{city_id}/localities" in schema["paths"]
    locality = schema["components"]["schemas"]["Locality"]
    assert set(locality["properties"]) == {"id", "name", "kind"}
    assert locality["properties"]["name"] == {"type": "string"}
    assert "enum" not in locality["properties"]["name"]
