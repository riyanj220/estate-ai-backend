import os

import psycopg
import pytest
from django.conf import settings
from django.db import connection
from psycopg import errors


@pytest.fixture
def runtime_conn(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        name = connection.settings_dict["NAME"]  # the migrated test database
    conn = psycopg.connect(
        dbname=name,
        user=settings.RUNTIME_DB_USER,
        password=os.environ["RUNTIME_PASSWORD"],
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        autocommit=True,
    )
    yield conn
    conn.close()


def test_runtime_cannot_create_table(runtime_conn):
    with pytest.raises(errors.InsufficientPrivilege):
        runtime_conn.execute("CREATE TABLE should_fail (id int)")


def test_runtime_cannot_alter_table(runtime_conn):
    with pytest.raises(errors.InsufficientPrivilege):
        runtime_conn.execute("ALTER TABLE django_content_type ADD COLUMN nope int")


def test_runtime_dml_allowed(runtime_conn):
    runtime_conn.execute(
        "INSERT INTO django_content_type (app_label, model) VALUES ('rt_probe', 'probe')"
    )
    rows = runtime_conn.execute(
        "SELECT model FROM django_content_type WHERE app_label = 'rt_probe'"
    ).fetchall()
    assert rows == [("probe",)]
    runtime_conn.execute(
        "UPDATE django_content_type SET model = 'probe2' WHERE app_label = 'rt_probe'"
    )
    runtime_conn.execute("DELETE FROM django_content_type WHERE app_label = 'rt_probe'")


def test_append_only_exclusion(runtime_conn, django_db_blocker):
    from config.privileges import grant_runtime_privileges

    with django_db_blocker.unblock():
        grant_runtime_privileges(
            connection, settings.RUNTIME_DB_USER, append_only=["django_content_type"]
        )
        try:
            with pytest.raises(errors.InsufficientPrivilege):
                runtime_conn.execute("DELETE FROM django_content_type WHERE app_label = 'x'")
            runtime_conn.execute(
                "INSERT INTO django_content_type (app_label, model) VALUES ('ao', 'p')"
            )
        finally:
            grant_runtime_privileges(connection, settings.RUNTIME_DB_USER)
            runtime_conn.execute("DELETE FROM django_content_type WHERE app_label = 'ao'")


def test_post_migrate_hook_honours_append_only_setting(runtime_conn, django_db_blocker):
    from django.apps import apps
    from django.test import override_settings

    from config.apps import _grant_after_migrate
    from config.privileges import grant_runtime_privileges

    sender = apps.get_app_config("contenttypes")
    with django_db_blocker.unblock():
        try:
            with override_settings(RUNTIME_APPEND_ONLY_TABLES=["django_content_type"]):
                _grant_after_migrate(sender, using="default")
            runtime_conn.execute(
                "INSERT INTO django_content_type (app_label, model) VALUES ('hook', 'p')"
            )
            with pytest.raises(errors.InsufficientPrivilege):
                runtime_conn.execute("UPDATE django_content_type SET model = 'q'")
            with pytest.raises(errors.InsufficientPrivilege):
                runtime_conn.execute("DELETE FROM django_content_type WHERE app_label = 'hook'")
        finally:
            grant_runtime_privileges(connection, settings.RUNTIME_DB_USER)
            runtime_conn.execute("DELETE FROM django_content_type WHERE app_label = 'hook'")


@pytest.mark.django_db
def test_vector_extension_present():
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        assert cursor.fetchone() is not None
