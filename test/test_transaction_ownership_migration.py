"""Opt-in PostgreSQL tests for migration 001, using isolated synthetic schemas."""

import importlib
import ipaddress
import os
import re
import uuid
from urllib.parse import unquote, urlsplit

import psycopg2
import pytest


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
        reason="Set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL integration tests",
    ),
]

EXPECTED_DATABASE = "expense_intelligence_test"
EXPECTED_PORT = 5432

migration = importlib.import_module(
    "scripts.migrations.001_transaction_ownership"
)


def _validated_test_url():
    if os.getenv("ALLOW_TEST_DB_DESTRUCTIVE_SETUP") != "1":
        raise RuntimeError(
            "Set ALLOW_TEST_DB_DESTRUCTIVE_SETUP=1 to create isolated test objects"
        )
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        raise RuntimeError("TEST_DATABASE_URL is required for PostgreSQL integration tests")
    try:
        parsed = urlsplit(dsn)
        database = unquote(parsed.path.lstrip("/"))
        port = parsed.port
    except (TypeError, ValueError):
        raise RuntimeError("TEST_DATABASE_URL is not a valid PostgreSQL URL") from None
    if parsed.query or parsed.fragment:
        raise RuntimeError("TEST_DATABASE_URL must not contain query parameters or a fragment")
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname != "localhost"
        or port != EXPECTED_PORT
        or database != EXPECTED_DATABASE
    ):
        raise RuntimeError(
            "Refusing database target; expected localhost:5432/expense_intelligence_test"
        )
    return dsn


def _connect(schema=None):
    # Recheck the explicit URL and destructive-setup opt-in for every connection.
    dsn = _validated_test_url()
    try:
        connection = psycopg2.connect(dsn)
    except Exception:
        raise RuntimeError(
            "Could not connect to the explicitly configured local test database"
        ) from None
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), inet_server_port(), inet_server_addr()"
            )
            database, port, address = cursor.fetchone()
            if database != EXPECTED_DATABASE or port != EXPECTED_PORT:
                raise RuntimeError("Connected server identity did not match the approved test target")
            if address is None or not ipaddress.ip_address(address).is_loopback:
                raise RuntimeError("Connected server is not a loopback PostgreSQL server")
            if schema is not None:
                if not re.fullmatch(r"expense_mig_[0-9a-f]{32}", schema):
                    raise RuntimeError("Refusing an unexpected migration-test schema name")
                cursor.execute(
                    "SET search_path TO "
                    + psycopg2.extensions.quote_ident(schema, cursor)
                )
        connection.commit()
        return connection
    except Exception:
        connection.close()
        raise


@pytest.fixture
def migration_schema():
    schema = f"expense_mig_{uuid.uuid4().hex}"
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
            if cursor.fetchone()[0] != 0:
                raise RuntimeError("Refusing setup because public schema is not empty")
            cursor.execute(
                "CREATE SCHEMA " + psycopg2.extensions.quote_ident(schema, cursor)
            )
            cursor.execute(
                "SET search_path TO " + psycopg2.extensions.quote_ident(schema, cursor)
            )
            # Kept in sync with main.startup_db() base table definitions. In
            # particular, this deliberately has no FK or NOT NULL on user_id.
            cursor.execute(
                """CREATE TABLE users (
                       id SERIAL PRIMARY KEY,
                       name TEXT NOT NULL,
                       email TEXT UNIQUE NOT NULL,
                       password_hash TEXT NOT NULL
                   )"""
            )
            cursor.execute(
                """CREATE TABLE transactions (
                       id SERIAL PRIMARY KEY,
                       user_id INTEGER,
                       date DATE,
                       description TEXT,
                       category TEXT,
                       amount NUMERIC
                   )"""
            )
            cursor.execute(
                "CREATE INDEX idx_transactions_user_id ON transactions(user_id)"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        connection.close()
        raise
    connection.close()

    try:
        yield schema
    finally:
        cleanup = _connect()
        try:
            with cleanup.cursor() as cursor:
                if not re.fullmatch(r"expense_mig_[0-9a-f]{32}", schema):
                    raise RuntimeError("Refusing cleanup for unexpected migration-test schema")
                cursor.execute(
                    "DROP TABLE IF EXISTS "
                    + psycopg2.extensions.quote_ident(schema, cursor)
                    + ".transactions"
                )
                cursor.execute(
                    "DROP TABLE IF EXISTS "
                    + psycopg2.extensions.quote_ident(schema, cursor)
                    + ".users"
                )
                cursor.execute(
                    "DROP SCHEMA " + psycopg2.extensions.quote_ident(schema, cursor)
                )
            cleanup.commit()
        except Exception:
            cleanup.rollback()
            raise RuntimeError(
                "Safe migration-test cleanup aborted; no cascading cleanup was performed"
            ) from None
        finally:
            cleanup.close()


def _insert_user_and_transaction(schema, user_id=1):
    connection = _connect(schema)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, name, email, password_hash) "
                "VALUES (%s, %s, %s, %s)",
                (user_id, "Synthetic User", f"{uuid.uuid4().hex}@example.invalid", "synthetic"),
            )
            cursor.execute(
                "INSERT INTO transactions (user_id, description) VALUES (%s, %s)",
                (user_id, "synthetic transaction"),
            )
        connection.commit()
    finally:
        connection.close()


def _ownership_state(schema):
    connection = _connect(schema)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'transactions' "
                "AND column_name = 'user_id'"
            )
            nullable = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conrelid = 'transactions'::regclass AND contype = 'f'"
            )
            fk_count = cursor.fetchone()[0]
            cursor.execute("SELECT user_id, description FROM transactions ORDER BY id")
            rows = cursor.fetchall()
        return nullable, fk_count, rows
    finally:
        connection.close()


def test_clean_schema_migrates_and_cascades(migration_schema):
    schema = migration_schema
    _insert_user_and_transaction(schema)
    connection = _connect()
    try:
        migration.apply_migration(connection, schema)
    finally:
        connection.close()

    nullable, fk_count, rows = _ownership_state(schema)
    assert nullable == "NO"
    assert fk_count == 1
    assert rows == [(1, "synthetic transaction")]

    connection = _connect(schema)
    try:
        with connection.cursor() as cursor:
            with pytest.raises(psycopg2.IntegrityError):
                cursor.execute(
                    "INSERT INTO transactions (user_id, description) VALUES (%s, %s)",
                    (None, "synthetic null owner"),
                )
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE id = %s", (1,))
            cursor.execute("SELECT count(*) FROM transactions")
            assert cursor.fetchone()[0] == 0
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("ownership_value", "diagnostic"),
    [(None, "1 NULL user_id row(s), 0 orphaned"), (999999, "0 NULL user_id row(s), 1 orphaned")],
)
def test_invalid_ownership_aborts_without_data_or_schema_change(
    migration_schema, ownership_value, diagnostic
):
    schema = migration_schema
    connection = _connect(schema)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO transactions (user_id, description) VALUES (%s, %s)",
                (ownership_value, "synthetic invalid ownership"),
            )
        connection.commit()
    finally:
        connection.close()

    connection = _connect()
    try:
        with pytest.raises(migration.MigrationError, match=re.escape(diagnostic)):
            migration.apply_migration(connection, schema)
    finally:
        connection.close()

    nullable, fk_count, rows = _ownership_state(schema)
    assert nullable == "YES"
    assert fk_count == 0
    assert rows == [(ownership_value, "synthetic invalid ownership")]


def test_migration_rerun_does_not_duplicate_foreign_key(migration_schema):
    schema = migration_schema
    _insert_user_and_transaction(schema)
    for _ in range(2):
        connection = _connect()
        try:
            migration.apply_migration(connection, schema)
        finally:
            connection.close()
    nullable, fk_count, _rows = _ownership_state(schema)
    assert nullable == "NO"
    assert fk_count == 1


def test_conflicting_foreign_key_aborts_without_schema_change(migration_schema):
    schema = migration_schema
    _insert_user_and_transaction(schema)
    connection = _connect(schema)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE transactions ADD CONSTRAINT conflicting_user_fk "
                "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT"
            )
        connection.commit()
    finally:
        connection.close()

    connection = _connect()
    try:
        with pytest.raises(migration.MigrationError, match="Conflicting foreign key"):
            migration.apply_migration(connection, schema)
    finally:
        connection.close()

    nullable, fk_count, rows = _ownership_state(schema)
    assert nullable == "YES"
    assert fk_count == 1
    assert rows == [(1, "synthetic transaction")]
