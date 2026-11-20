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
            grant_runtime_privileges(
                connection, settings.RUNTIME_DB_USER, settings.RUNTIME_APPEND_ONLY_TABLES
            )
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
            grant_runtime_privileges(
                connection, settings.RUNTIME_DB_USER, settings.RUNTIME_APPEND_ONLY_TABLES
            )
            runtime_conn.execute("DELETE FROM django_content_type WHERE app_label = 'hook'")


@pytest.mark.django_db
def test_vector_extension_present():
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        assert cursor.fetchone() is not None


def test_runtime_can_upsert_counters_and_read_policy_but_not_ddl(runtime_conn):
    runtime_conn.execute("SELECT count(*) FROM kernel_ratelimitpolicy").fetchone()
    sql = (
        "INSERT INTO kernel_ratelimitcounter (id, scope, key_hash, window_start, count) "
        "VALUES (gen_random_uuid(), 'role_probe', 'k', now(), 1) "
        "ON CONFLICT (scope, key_hash, window_start) "
        "DO UPDATE SET count = kernel_ratelimitcounter.count + 1 RETURNING count"
    )
    assert runtime_conn.execute(sql).fetchone() == (1,)
    runtime_conn.execute("DELETE FROM kernel_ratelimitcounter WHERE scope = 'role_probe'")
    with pytest.raises(errors.InsufficientPrivilege):
        runtime_conn.execute("ALTER TABLE kernel_ratelimitpolicy ADD COLUMN nope int")


def test_runtime_can_claim_and_purge_idempotency_records_but_not_ddl(runtime_conn):
    sql = (
        "INSERT INTO kernel_idempotencyrecord "
        "(id, scope, account_id, key, request_hash, created_at) "
        "VALUES (gen_random_uuid(), 'role_probe', gen_random_uuid(), 'k', 'h', now()) "
        "ON CONFLICT (scope, account_id, key) DO NOTHING RETURNING id"
    )
    assert runtime_conn.execute(sql).fetchone() is not None
    runtime_conn.execute(
        "UPDATE kernel_idempotencyrecord SET result_ref = 'x:1' WHERE scope = 'role_probe'"
    )
    runtime_conn.execute("DELETE FROM kernel_idempotencyrecord WHERE scope = 'role_probe'")
    with pytest.raises(errors.InsufficientPrivilege):
        runtime_conn.execute("ALTER TABLE kernel_idempotencyrecord ADD COLUMN nope int")


def test_runtime_can_use_flags_ai_log_and_purges_but_not_ddl(runtime_conn):
    runtime_conn.execute(
        "INSERT INTO kernel_featureflag (id, name, enabled, changed_by, changed_at) "
        "VALUES (gen_random_uuid(), 'role_probe', true, NULL, now())"
    )
    runtime_conn.execute("UPDATE kernel_featureflag SET enabled = false WHERE name = 'role_probe'")
    assert runtime_conn.execute(
        "SELECT enabled FROM kernel_featureflag WHERE name = 'role_probe'"
    ).fetchone() == (False,)
    runtime_conn.execute("DELETE FROM kernel_featureflag WHERE name = 'role_probe'")

    runtime_conn.execute(
        "INSERT INTO kernel_airequestlog (id, feature, provider, model, latency_ms, outcome, "
        "created_at) VALUES (gen_random_uuid(), 'role_probe', 'p', 'm', 1, 'success', "
        "now() - interval '100 days')"
    )
    runtime_conn.execute(
        "DELETE FROM kernel_airequestlog WHERE created_at < now() - interval '90 days'"
    )
    assert runtime_conn.execute(
        "SELECT count(*) FROM kernel_airequestlog WHERE feature = 'role_probe'"
    ).fetchone() == (0,)
    for table in ("kernel_featureflag", "kernel_airequestlog"):
        with pytest.raises(errors.InsufficientPrivilege):
            runtime_conn.execute(f"ALTER TABLE {table} ADD COLUMN nope int")


def test_runtime_audit_table_is_append_only(runtime_conn, django_db_blocker):
    try:
        _audit_probe(runtime_conn)
    finally:
        with django_db_blocker.unblock(), connection.cursor() as cursor:
            cursor.execute("DELETE FROM kernel_auditrecord WHERE action = 'role.probed'")


def _audit_probe(runtime_conn):
    runtime_conn.execute(
        "INSERT INTO kernel_auditrecord (id, actor_kind, action, target_type, target_id, "
        "reason, request_id, metadata, created_at) VALUES (gen_random_uuid(), 'system', "
        "'role.probed', 'probe', gen_random_uuid(), '', '', '{}', now())"
    )
    assert runtime_conn.execute(
        "SELECT count(*) FROM kernel_auditrecord WHERE action = 'role.probed'"
    ).fetchone() == (1,)
    for sql in (
        "UPDATE kernel_auditrecord SET reason = 'x'",
        "DELETE FROM kernel_auditrecord",
        "TRUNCATE kernel_auditrecord",
    ):
        with pytest.raises(errors.InsufficientPrivilege):
            runtime_conn.execute(sql)


def test_runtime_can_read_and_write_cities_but_not_ddl(runtime_conn, django_db_blocker):
    try:
        runtime_conn.execute("SELECT count(*) FROM catalog_city").fetchone()
        runtime_conn.execute(
            "INSERT INTO catalog_city (id, name, is_active, created_at) "
            "VALUES (gen_random_uuid(), 'Role Probe', true, now())"
        )
        runtime_conn.execute("UPDATE catalog_city SET is_active = false WHERE name = 'Role Probe'")
        runtime_conn.execute("DELETE FROM catalog_city WHERE name = 'Role Probe'")
        with pytest.raises(errors.InsufficientPrivilege):
            runtime_conn.execute("ALTER TABLE catalog_city ADD COLUMN nope int")
    finally:
        with django_db_blocker.unblock(), connection.cursor() as cursor:
            cursor.execute("DELETE FROM catalog_city WHERE name = 'Role Probe'")


def test_runtime_can_read_and_write_accounts_but_not_ddl(runtime_conn, django_db_blocker):
    try:
        runtime_conn.execute("SELECT count(*) FROM identity_account").fetchone()
        runtime_conn.execute(
            "INSERT INTO identity_account (id, email, password, account_type, suspension_reason, "
            "created_at, updated_at) VALUES (gen_random_uuid(), 'role.probe@example.com', 'x', "
            "'individual', '', now(), now())"
        )
        runtime_conn.execute(
            "UPDATE identity_account SET email_verified_at = now() "
            "WHERE email = 'role.probe@example.com'"
        )
        runtime_conn.execute("DELETE FROM identity_account WHERE email = 'role.probe@example.com'")
        with pytest.raises(errors.InsufficientPrivilege):
            runtime_conn.execute("ALTER TABLE identity_account ADD COLUMN nope int")
    finally:
        with django_db_blocker.unblock(), connection.cursor() as cursor:
            cursor.execute("DELETE FROM identity_account WHERE email = 'role.probe@example.com'")


def test_runtime_can_write_individual_profiles_but_not_ddl(runtime_conn, django_db_blocker):
    try:
        runtime_conn.execute(
            "INSERT INTO identity_account (id, email, password, account_type, suspension_reason, "
            "created_at, updated_at) VALUES (gen_random_uuid(), 'profile.probe@example.com', 'x', "
            "'individual', '', now(), now())"
        )
        runtime_conn.execute(
            "INSERT INTO identity_individualprofile (id, account_id, full_name, phone, "
            "created_at, updated_at) SELECT gen_random_uuid(), id, 'Probe', '+923001234567', "
            "now(), now() FROM identity_account WHERE email = 'profile.probe@example.com'"
        )
        runtime_conn.execute(
            "UPDATE identity_individualprofile SET full_name = 'Probe Two' WHERE account_id = "
            "(SELECT id FROM identity_account WHERE email = 'profile.probe@example.com')"
        )
        with pytest.raises(errors.InsufficientPrivilege):
            runtime_conn.execute("ALTER TABLE identity_individualprofile ADD COLUMN nope int")
    finally:
        with django_db_blocker.unblock(), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM identity_individualprofile WHERE account_id = "
                "(SELECT id FROM identity_account WHERE email = 'profile.probe@example.com')"
            )
            cursor.execute("DELETE FROM identity_account WHERE email = 'profile.probe@example.com'")


def test_runtime_can_write_agency_profiles_but_not_ddl(runtime_conn, django_db_blocker):
    try:
        runtime_conn.execute(
            "INSERT INTO identity_account (id, email, password, account_type, suspension_reason, "
            "created_at, updated_at) VALUES (gen_random_uuid(), 'agency.probe@example.com', 'x', "
            "'agency', '', now(), now())"
        )
        runtime_conn.execute(
            "INSERT INTO identity_agencyprofile (id, account_id, agency_name, business_phone, "
            "office_address, city_id, description, verification_status, created_at, updated_at) "
            "SELECT gen_random_uuid(), id, 'Probe', '+923001234567', 'Probe street 1', "
            "gen_random_uuid(), '', 'unverified', now(), now() FROM identity_account "
            "WHERE email = 'agency.probe@example.com'"
        )
        runtime_conn.execute(
            "UPDATE identity_agencyprofile SET agency_name = 'Probe Two' WHERE account_id = "
            "(SELECT id FROM identity_account WHERE email = 'agency.probe@example.com')"
        )
        with pytest.raises(errors.InsufficientPrivilege):
            runtime_conn.execute("ALTER TABLE identity_agencyprofile ADD COLUMN nope int")
    finally:
        with django_db_blocker.unblock(), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM identity_agencyprofile WHERE account_id = "
                "(SELECT id FROM identity_account WHERE email = 'agency.probe@example.com')"
            )
            cursor.execute("DELETE FROM identity_account WHERE email = 'agency.probe@example.com'")


def test_runtime_can_write_verification_tokens_but_not_ddl(runtime_conn, django_db_blocker):
    try:
        runtime_conn.execute(
            "INSERT INTO identity_account (id, email, password, account_type, suspension_reason, "
            "created_at, updated_at) VALUES (gen_random_uuid(), 'token.probe@example.com', 'x', "
            "'individual', '', now(), now())"
        )
        runtime_conn.execute(
            "INSERT INTO identity_emailverificationtoken (id, account_id, token_hash, created_at, "
            "expires_at) SELECT gen_random_uuid(), id, 'probehash', now(), now() "
            "FROM identity_account WHERE email = 'token.probe@example.com'"
        )
        runtime_conn.execute(
            "UPDATE identity_emailverificationtoken SET used_at = now() "
            "WHERE token_hash = 'probehash'"
        )
        runtime_conn.execute(
            "DELETE FROM identity_emailverificationtoken WHERE token_hash = 'probehash'"
        )
        with pytest.raises(errors.InsufficientPrivilege):
            runtime_conn.execute("ALTER TABLE identity_emailverificationtoken ADD COLUMN nope int")
    finally:
        with django_db_blocker.unblock(), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM identity_emailverificationtoken WHERE token_hash = 'probehash'"
            )
            cursor.execute("DELETE FROM identity_account WHERE email = 'token.probe@example.com'")


def test_runtime_can_write_password_reset_tokens_but_not_ddl(runtime_conn, django_db_blocker):
    try:
        runtime_conn.execute(
            "INSERT INTO identity_account (id, email, password, account_type, suspension_reason, "
            "created_at, updated_at) VALUES (gen_random_uuid(), 'reset.probe@example.com', 'x', "
            "'individual', '', now(), now())"
        )
        runtime_conn.execute(
            "INSERT INTO identity_passwordresettoken (id, account_id, token_hash, created_at, "
            "expires_at) SELECT gen_random_uuid(), id, 'resetprobehash', now(), now() "
            "FROM identity_account WHERE email = 'reset.probe@example.com'"
        )
        runtime_conn.execute(
            "UPDATE identity_passwordresettoken SET used_at = now() "
            "WHERE token_hash = 'resetprobehash'"
        )
        runtime_conn.execute(
            "DELETE FROM identity_passwordresettoken WHERE token_hash = 'resetprobehash'"
        )
        with pytest.raises(errors.InsufficientPrivilege):
            runtime_conn.execute("ALTER TABLE identity_passwordresettoken ADD COLUMN nope int")
    finally:
        with django_db_blocker.unblock(), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM identity_passwordresettoken WHERE token_hash = 'resetprobehash'"
            )
            cursor.execute("DELETE FROM identity_account WHERE email = 'reset.probe@example.com'")


def test_runtime_can_write_email_change_tokens_but_not_ddl(runtime_conn, django_db_blocker):
    try:
        runtime_conn.execute(
            "INSERT INTO identity_account (id, email, password, account_type, suspension_reason, "
            "created_at, updated_at) VALUES (gen_random_uuid(), 'change.probe@example.com', 'x', "
            "'individual', '', now(), now())"
        )
        runtime_conn.execute(
            "INSERT INTO identity_emailchangetoken (id, account_id, new_email, token_hash, "
            "created_at, expires_at, link_issued) SELECT gen_random_uuid(), id, "
            "'new.probe@example.com', 'changeprobehash', now(), now(), false FROM identity_account "
            "WHERE email = 'change.probe@example.com'"
        )
        runtime_conn.execute(
            "UPDATE identity_emailchangetoken SET used_at = now() "
            "WHERE token_hash = 'changeprobehash'"
        )
        runtime_conn.execute(
            "DELETE FROM identity_emailchangetoken WHERE token_hash = 'changeprobehash'"
        )
        with pytest.raises(errors.InsufficientPrivilege):
            runtime_conn.execute("ALTER TABLE identity_emailchangetoken ADD COLUMN nope int")
    finally:
        with django_db_blocker.unblock(), connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM identity_emailchangetoken WHERE token_hash = 'changeprobehash'"
            )
            cursor.execute("DELETE FROM identity_account WHERE email = 'change.probe@example.com'")
