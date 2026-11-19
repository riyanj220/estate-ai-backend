import ast
import importlib
import uuid
from pathlib import Path

import pytest
import yaml
from django.apps import apps as django_apps
from django.core.management import call_command
from django.db import transaction

from apps.catalog import selectors, services
from apps.catalog.models import City
from apps.kernel.actor import Actor
from apps.kernel.errors import DomainError
from apps.kernel.models import AuditRecord

ROOT = Path(__file__).resolve().parents[2]
ADMIN = Actor.admin(uuid.UUID("33333333-3333-4333-8333-333333333333"), "req-1")


def test_seed_creates_exactly_karachi(db):
    City.objects.all().delete()
    run_seed()
    city = City.objects.get()
    assert (city.name, city.is_active) == ("Karachi", True)


def run_seed():
    module = importlib.import_module("apps.catalog.migrations.0002_seed_karachi")
    module.seed_karachi(django_apps, None)


def test_reseed_is_a_no_op(db):
    City.objects.all().delete()
    run_seed()
    run_seed()
    assert City.objects.count() == 1


def test_reseed_keeps_an_existing_differently_cased_row(db):
    City.objects.all().delete()
    City.objects.create(name="karachi", is_active=False)
    run_seed()
    row = City.objects.get()
    assert (row.name, row.is_active) == ("karachi", False)


@pytest.fixture
def karachi(db):
    City.objects.all().delete()  # a transactional test elsewhere may have flushed the seed
    return City.objects.create(name="Karachi")


def test_list_is_active_only_ordered_and_plain(karachi):
    City.objects.create(name="lahore")
    City.objects.create(name="Abbottabad")
    City.objects.create(name="Quetta", is_active=False)
    result = selectors.list_cities()
    assert [c.name for c in result] == ["Abbottabad", "Karachi", "lahore"]
    assert not any(isinstance(c, City) for c in result)
    with pytest.raises(AttributeError):
        result[0].name = "x"


def test_endpoint_is_public_and_returns_only_id_and_name(client, karachi):
    City.objects.create(name="Quetta", is_active=False)
    response = client.get("/api/v1/reference/cities")
    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["items"]
    assert body["items"] == [{"id": str(karachi.id), "name": "Karachi"}]


def test_openapi_names_are_plain_strings(tmp_path):
    target = tmp_path / "o.yaml"
    call_command("spectacular", "--validate", "--file", str(target))
    schema = yaml.safe_load(target.read_text())
    assert "/api/v1/reference/cities" in schema["paths"]
    city = schema["components"]["schemas"]["City"]
    assert set(city["properties"]) == {"id", "name"}
    assert city["properties"]["name"] == {"type": "string"}
    assert "enum" not in city["properties"]["name"]


def test_admin_creates_city_with_audit_record(db):
    ref = services.create_city(ADMIN, "Lahore")
    assert City.objects.get(id=ref.id).name == "Lahore"
    audit = AuditRecord.objects.get(action="city.created")
    assert (audit.target_type, audit.target_id, audit.metadata) == ("city", ref.id, {})
    assert audit.actor_id == ADMIN.account_id


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
        services.create_city(actor, "Lahore")
    assert exc.value.code == code
    assert City.objects.count() == 1
    assert not AuditRecord.objects.exists()


@pytest.mark.parametrize("name", ["karachi", " KARACHI ", "KaRaChI"])
def test_duplicate_is_refused_without_writes(karachi, name):
    with pytest.raises(DomainError) as exc:
        services.create_city(ADMIN, name)
    assert exc.value.code == "validation_error"
    assert City.objects.count() == 1
    assert not AuditRecord.objects.exists()


@pytest.mark.parametrize(
    "name", ["", "   ", "A", "a" * 81, "City1", "Lah0re", "Lahore!", "-Lahore", "A@B", None, 5]
)
def test_bad_names_are_refused(karachi, name):
    with pytest.raises(DomainError) as exc:
        services.create_city(ADMIN, name)
    assert exc.value.code == "validation_error"
    assert City.objects.count() == 1


@pytest.mark.parametrize("name", ["Dera Ismail Khan", "Sher-e-Punjab", "O'Brien", "Ab", "a" * 80])
def test_good_names_are_accepted(db, name):
    assert services.create_city(ADMIN, name).name == name


def test_database_enforces_case_insensitive_uniqueness(db):
    from django.db import IntegrityError

    with pytest.raises(IntegrityError), transaction.atomic():
        City.objects.create(name="KARACHI")


@pytest.mark.django_db(transaction=True)
def test_failure_after_audit_write_rolls_back_both(monkeypatch):
    City.objects.get_or_create(name="Karachi")
    before = City.objects.count()

    original = services.audit.record

    def record_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        assert AuditRecord.objects.filter(action="city.created").exists()
        raise RuntimeError

    monkeypatch.setattr(services.audit, "record", record_then_fail)
    with pytest.raises(RuntimeError):
        services.create_city(ADMIN, "Lahore")
    assert City.objects.count() == before
    assert not AuditRecord.objects.exists()


def imports_catalog_models(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import) and any(
            a.name == "apps.catalog.models" for a in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom):
            if node.module == "apps.catalog.models":
                return True
            if node.module == "apps.catalog" and any(a.name == "models" for a in node.names):
                return True
    return False


def test_presentation_does_not_import_catalog_models():
    files = sorted((ROOT / "apps/presentation").rglob("*.py"))
    assert files
    assert [f.name for f in files if imports_catalog_models(f.read_text())] == []


@pytest.mark.parametrize(
    "source",
    [
        "import apps.catalog.models",
        "from apps.catalog.models import City",
        "from apps.catalog import models",
    ],
)
def test_import_check_catches_catalog_models(source):
    assert imports_catalog_models(source)
    assert not imports_catalog_models("from apps.catalog import selectors")


@pytest.mark.parametrize(
    "name",
    ["Lahore-", "Lahore'", "-Lahore", "'Lahore", "Lahore\u00a0'", "A--"],
)
def test_names_may_not_start_or_end_with_a_joiner(karachi, name):
    with pytest.raises(DomainError):
        services.create_city(ADMIN, name)


def test_whitespace_is_collapsed_and_accents_allowed(karachi):
    assert services.create_city(ADMIN, "Dera   Ghazi \t Khan").name == "Dera Ghazi Khan"
    assert services.create_city(ADMIN, "Pe\u0301shawar").name == "Pe\u0301shawar".replace(
        "e\u0301", "\u00e9"
    )


def test_nfc_and_nfd_are_duplicates(karachi):
    services.create_city(ADMIN, "Caf\u00e9ville")
    with pytest.raises(DomainError):
        services.create_city(ADMIN, "Cafe\u0301ville")
    assert City.objects.filter(name__icontains="ville").count() == 1


def test_race_on_unique_constraint_is_a_validation_error(karachi, monkeypatch):
    monkeypatch.setattr(services, "_name_taken", lambda name: False)
    with transaction.atomic():
        with pytest.raises(DomainError) as exc:
            services.create_city(ADMIN, "karachi")
        assert exc.value.code == "validation_error"
        assert City.objects.count() == 1  # the outer transaction is still usable
    assert not AuditRecord.objects.filter(action="city.created").exists()


def test_get_city(karachi):
    assert selectors.get_city(karachi.id) == selectors.CityRef(karachi.id, "Karachi")
    assert selectors.get_city(str(karachi.id)).name == "Karachi"
    hidden = City.objects.create(name="Quetta", is_active=False)
    assert selectors.get_city(hidden.id) is None
    for bad in (uuid.uuid4(), "nope", None, 5, ""):
        assert selectors.get_city(bad) is None


@pytest.mark.django_db(transaction=True)
def test_migration_path_seeds_exactly_one_karachi():
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor

    conn = connections["default"]  # the migrator in test settings
    executor = MigrationExecutor(conn)
    try:
        executor.migrate([("catalog", "0001_initial")])
        City.objects.all().delete()
        executor = MigrationExecutor(conn)
        executor.migrate([("catalog", "0002_seed_karachi")])
        rows = list(City.objects.all())
        assert [(c.name, c.is_active) for c in rows] == [("Karachi", True)]
    finally:
        MigrationExecutor(conn).migrate(MigrationExecutor(conn).loader.graph.leaf_nodes())
