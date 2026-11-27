"""The real purge database role (Stories 10.12 and 10.13): what it may do, and the purge commands
running through it. It exists where `docker/postgres/create_roles.sh` ran with PURGE_PASSWORD;
CI must have it, a developer database made before Story 10.13 may not yet."""

import io
import os
import uuid
from datetime import timedelta

import psycopg
import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import connection, connections, transaction
from django.utils import timezone
from psycopg import errors

from apps.kernel import audit
from apps.kernel.actor import Actor
from apps.kernel.models import AuditRecord
from apps.listings import publication
from apps.listings.models import PublicationEvent
from config.privileges import role_exists

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def password():
    value = os.environ.get("PURGE_PASSWORD")
    with connection.cursor():
        exists = role_exists(connection, settings.PURGE_DB_USER)
    if not (value and exists):
        assert not os.environ.get("CI"), "CI must provision the purge role"
        pytest.skip("no purge role here: see docs/audit.md")
    return value


@pytest.fixture
def purge_conn(password):
    conn = psycopg.connect(
        dbname=connection.settings_dict["NAME"],
        user=settings.PURGE_DB_USER,
        password=password,
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        autocommit=True,
    )
    yield conn
    conn.close()


@pytest.fixture
def purge_alias(password, monkeypatch):
    """The `purge` connection an operator environment has, pointing at the test database."""
    config = {
        **connection.settings_dict,
        "USER": settings.PURGE_DB_USER,
        "PASSWORD": password,
    }
    monkeypatch.setitem(connections.settings, "purge", config)
    monkeypatch.setitem(settings.DATABASES, "purge", config)
    connections["purge"].connect()  # opened here: the test case refuses a lazy first connection
    yield
    connections["purge"].close()
    del connections["purge"]


def test_the_purge_role_selects_and_deletes_its_tables_only(purge_conn):
    for table in settings.PURGE_ROLE_TABLES:
        purge_conn.execute(f"SELECT count(*) FROM {table}")  # noqa: S608
        purge_conn.execute(f"DELETE FROM {table} WHERE false")  # noqa: S608
        for sql in (
            f"UPDATE {table} SET id = id",  # noqa: S608
            f"INSERT INTO {table} DEFAULT VALUES",  # noqa: S608
            f"TRUNCATE {table}",
            f"ALTER TABLE {table} ADD COLUMN nope int",
        ):
            with pytest.raises(errors.InsufficientPrivilege):
                purge_conn.execute(sql)
    for sql in (
        "SELECT count(*) FROM identity_account",
        "DELETE FROM governance_supportrequest",
        "CREATE TABLE should_fail (id int)",
    ):
        with pytest.raises(errors.InsufficientPrivilege):
            purge_conn.execute(sql)


def test_the_purge_commands_work_as_the_purge_role(purge_alias):
    old = timezone.now() - timedelta(days=800)
    audit.register_action("probe.happened", {})
    with transaction.atomic():
        row = audit.record(Actor.system("probe"), "probe.happened", "probe", uuid.uuid4())
    AuditRecord.objects.filter(id=row.id).update(created_at=old)
    PublicationEvent.objects.create(
        account_id=uuid.uuid4(),
        listing_id=uuid.uuid4(),
        kind="initial",
        counted=True,
        created_at=old,
    )
    out = io.StringIO()
    call_command("purge_audit_records", stdout=out)
    assert out.getvalue().strip().endswith("deleted 1")
    assert not AuditRecord.objects.filter(id=row.id).exists()
    assert publication.purge_old_events(timezone.now(), False) == 1
    assert not PublicationEvent.objects.exists()


def test_the_purge_role_deletes_reports_and_verification_with_their_history(purge_alias):
    from apps.governance import retention as governance_retention
    from tests.retention.test_app_retention import report, verification_request

    now = timezone.now()
    closed = now - timedelta(days=800)
    report("resolved", closed)
    verification_request("rejected", closed)
    assert governance_retention.reports(now, False) == 1
    assert governance_retention.verification(now, False) == 1
    assert governance_retention.moderation_records(now, False) == 0
