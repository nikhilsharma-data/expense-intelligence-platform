"""Opt-in PostgreSQL integration tests for transaction integrity.

These tests use only TEST_DATABASE_URL and require an explicit destructive-setup
opt-in. Ordinary pytest runs collect and skip them without opening a connection.
"""

import asyncio
import ipaddress
import os
import re
import uuid
from datetime import date
from io import BytesIO
from urllib.parse import unquote, urlsplit

import dotenv
import psycopg2
import pytest
from fastapi import HTTPException, UploadFile


# Importing main normally imports db.py, whose module body loads .env. Prevent
# that side effect for this suite; the integration connection uses TEST_DATABASE_URL only.
_load_dotenv = dotenv.load_dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False
try:
    import main
finally:
    dotenv.load_dotenv = _load_dotenv


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
        reason="Set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL integration tests",
    ),
]

EXPECTED_HOST = "localhost"
EXPECTED_PORT = 5432
EXPECTED_DATABASE = "expense_intelligence_test"
FAILURE_DESCRIPTION = "integration-trigger-failure"


def _validated_test_url():
    """Validate explicit target and opt-in before any database connection."""
    if os.getenv("ALLOW_TEST_DB_DESTRUCTIVE_SETUP") != "1":
        raise RuntimeError("Set ALLOW_TEST_DB_DESTRUCTIVE_SETUP=1 to create isolated test objects")

    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        raise RuntimeError("TEST_DATABASE_URL is required for PostgreSQL integration tests")

    try:
        parsed = urlsplit(dsn)
        database = unquote(parsed.path.lstrip("/"))
        port = parsed.port
    except (TypeError, ValueError):
        raise RuntimeError("TEST_DATABASE_URL is not a valid PostgreSQL URL") from None

    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname != EXPECTED_HOST
        or port != EXPECTED_PORT
        or database != EXPECTED_DATABASE
    ):
        raise RuntimeError(
            "Refusing database target; expected localhost:5432/expense_intelligence_test"
        )
    return dsn


def _connect(dsn, schema=None):
    """Open only the validated test URL; never use application DB configuration."""
    try:
        conn = psycopg2.connect(dsn)
    except Exception:
        raise RuntimeError("Could not connect to the explicitly configured local test database") from None

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_database(), inet_server_port(), inet_server_addr()"
            )
            database, port, address = cur.fetchone()
            if database != EXPECTED_DATABASE or port != EXPECTED_PORT:
                raise RuntimeError("Connected server identity did not match the approved test target")
            if address is None or not ipaddress.ip_address(address).is_loopback:
                raise RuntimeError("Connected server is not a loopback PostgreSQL server")
            if schema is not None:
                if not re.fullmatch(r"expense_it_[0-9a-f]{32}", schema):
                    raise RuntimeError("Refusing an unexpected test schema name")
                cur.execute(f' SET search_path TO "{schema}"')
        conn.commit()
        return conn
    except Exception:
        conn.close()
        raise


@pytest.fixture(scope="session")
def test_schema():
    dsn = _validated_test_url()
    schema = f"expense_it_{uuid.uuid4().hex}"
    conn = _connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
            if cur.fetchone()[0] != 0:
                raise RuntimeError("Refusing setup because public schema is not empty")

            cur.execute(f'CREATE SCHEMA "{schema}"')
            cur.execute(f'SET search_path TO "{schema}"')
            cur.execute(
                """
                CREATE TABLE users (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE transactions (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    date DATE,
                    description TEXT,
                    category TEXT,
                    amount NUMERIC
                )
                """
            )
            cur.execute("CREATE INDEX transactions_user_id_test_idx ON transactions(user_id)")
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()

    try:
        yield dsn, schema
    finally:
        cleanup = _connect(dsn)
        try:
            with cleanup.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cleanup.commit()
        finally:
            cleanup.close()


@pytest.fixture
def seeded_users(test_schema):
    dsn, schema = test_schema
    conn = _connect(dsn, schema)
    suffix = uuid.uuid4().hex
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (name, email, password_hash) VALUES (%s, %s, %s) RETURNING id",
                ("Test User One", f"one-{suffix}@example.invalid", "synthetic-hash"),
            )
            first_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO users (name, email, password_hash) VALUES (%s, %s, %s) RETURNING id",
                ("Test User Two", f"two-{suffix}@example.invalid", "synthetic-hash"),
            )
            second_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO transactions (user_id, date, description, category, amount) "
                "VALUES (%s, %s, %s, %s, %s), (%s, %s, %s, %s, %s)",
                (
                    first_id, date(2026, 1, 2), f"old-{suffix}-one", "Other", -10,
                    first_id, date(2026, 1, 3), f"old-{suffix}-two", "Other", -20,
                ),
            )
            cur.execute(
                "INSERT INTO transactions (user_id, date, description, category, amount) "
                "VALUES (%s, %s, %s, %s, %s)",
                (second_id, date(2026, 1, 4), f"other-{suffix}", "Other", -30),
            )
        conn.commit()
        yield first_id, second_id, suffix
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM transactions WHERE user_id IN (%s, %s)", (first_id, second_id))
            cur.execute("DELETE FROM users WHERE id IN (%s, %s)", (first_id, second_id))
        conn.commit()
        conn.close()


def _upload(content):
    return UploadFile(filename="replacement.csv", file=BytesIO(content))


def _install_insert_failure(dsn, schema):
    conn = _connect(dsn, schema)
    function = f"fail_insert_{uuid.uuid4().hex}"
    trigger = f"fail_insert_{uuid.uuid4().hex}"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.description = '{FAILURE_DESCRIPTION}' THEN
                    RAISE EXCEPTION 'injected integration insert failure';
                END IF;
                RETURN NEW;
            END
            $$
            """
        )
        cur.execute(
            f"CREATE TRIGGER {trigger} BEFORE INSERT ON transactions "
            f"FOR EACH ROW EXECUTE FUNCTION {function}()"
        )
    conn.commit()
    conn.close()
    return function, trigger


def _remove_trigger_objects(dsn, schema, function, trigger):
    conn = _connect(dsn, schema)
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP TRIGGER IF EXISTS {trigger} ON transactions")
            cur.execute(f"DROP FUNCTION IF EXISTS {function}()")
        conn.commit()
    finally:
        conn.close()


def test_upload_failure_restores_committed_rows(test_schema, seeded_users):
    dsn, schema = test_schema
    user_id, _other_id, suffix = seeded_users
    function, trigger = _install_insert_failure(dsn, schema)
    upload_conn = _connect(dsn, schema)
    upload_cur = upload_conn.cursor()
    csv_data = (
        "date,description,amount\n"
        f"2026-02-01,replacement-{suffix},-40\n"
        f"2026-02-02,{FAILURE_DESCRIPTION},-50\n"
    ).encode()
    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(main.upload_file(user_id, _upload(csv_data), (upload_conn, upload_cur)))
        assert error.value.status_code == 500
        observer = _connect(dsn, schema)
        try:
            with observer.cursor() as cur:
                cur.execute(
                    "SELECT description, amount FROM transactions WHERE user_id = %s ORDER BY description",
                    (user_id,),
                )
                rows = cur.fetchall()
            assert {row[0] for row in rows} == {f"old-{suffix}-one", f"old-{suffix}-two"}
            assert {str(row[1]) for row in rows} == {"-10", "-20"}
        finally:
            observer.close()
    finally:
        upload_cur.close()
        upload_conn.close()
        _remove_trigger_objects(dsn, schema, function, trigger)


def test_transaction_reads_and_deletes_are_user_scoped(test_schema, seeded_users):
    dsn, schema = test_schema
    user_id, other_id, suffix = seeded_users
    handler_conn = _connect(dsn, schema)
    handler_cur = handler_conn.cursor()
    try:
        rows = main.get_transactions(user_id=user_id, db=(handler_conn, handler_cur))
        assert {row["description"] for row in rows} == {f"old-{suffix}-one", f"old-{suffix}-two"}
        assert all(f"other-{suffix}" not in row["description"] for row in rows)

        main.delete_transactions(user_id=user_id, db=(handler_conn, handler_cur))
        observer = _connect(dsn, schema)
        try:
            with observer.cursor() as cur:
                cur.execute("SELECT user_id, description FROM transactions ORDER BY description")
                remaining = cur.fetchall()
            assert remaining == [(other_id, f"other-{suffix}")]
        finally:
            observer.close()
    finally:
        handler_cur.close()
        handler_conn.close()


def _install_user_delete_failure(dsn, schema):
    conn = _connect(dsn, schema)
    function = f"fail_user_delete_{uuid.uuid4().hex}"
    trigger = f"fail_user_delete_{uuid.uuid4().hex}"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'injected integration account deletion failure';
            END
            $$
            """
        )
        cur.execute(
            f"CREATE TRIGGER {trigger} BEFORE DELETE ON users "
            f"FOR EACH ROW EXECUTE FUNCTION {function}()"
        )
    conn.commit()
    conn.close()
    return function, trigger


def test_account_deletion_failure_preserves_user_and_transactions(test_schema, seeded_users):
    dsn, schema = test_schema
    user_id, _other_id, suffix = seeded_users
    function, trigger = _install_user_delete_failure(dsn, schema)
    handler_conn = _connect(dsn, schema)
    try:
        with pytest.raises(HTTPException) as error:
            main.delete_account(user_id=user_id, db=(handler_conn, handler_conn.cursor()))
        assert error.value.status_code == 500
        observer = _connect(dsn, schema)
        try:
            with observer.cursor() as cur:
                cur.execute("SELECT count(*) FROM users WHERE id = %s", (user_id,))
                assert cur.fetchone()[0] == 1
                cur.execute(
                    "SELECT description FROM transactions WHERE user_id = %s ORDER BY description",
                    (user_id,),
                )
                assert [row[0] for row in cur.fetchall()] == [f"old-{suffix}-one", f"old-{suffix}-two"]
        finally:
            observer.close()
    finally:
        handler_conn.close()
        _remove_trigger_objects(dsn, schema, function, trigger)


def test_test_schema_has_cascade_foreign_key_and_enforces_it(test_schema, seeded_users):
    dsn, schema = test_schema
    user_id, _other_id, suffix = seeded_users
    conn = _connect(dsn, schema)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT constraint_row.confdeltype,
                       array_agg(source_column.attname ORDER BY key_column.position),
                       array_agg(target_column.attname ORDER BY key_column.position)
                FROM pg_constraint AS constraint_row
                JOIN pg_class AS source_table ON source_table.oid = constraint_row.conrelid
                JOIN pg_namespace AS source_schema ON source_schema.oid = source_table.relnamespace
                JOIN pg_class AS target_table ON target_table.oid = constraint_row.confrelid
                JOIN unnest(constraint_row.conkey, constraint_row.confkey)
                     WITH ORDINALITY AS key_column(source_attnum, target_attnum, position) ON TRUE
                JOIN pg_attribute AS source_column
                     ON source_column.attrelid = source_table.oid
                    AND source_column.attnum = key_column.source_attnum
                JOIN pg_attribute AS target_column
                     ON target_column.attrelid = target_table.oid
                    AND target_column.attnum = key_column.target_attnum
                WHERE constraint_row.contype = 'f'
                  AND source_schema.nspname = current_schema()
                  AND source_table.relname = 'transactions'
                  AND target_table.relname = 'users'
                GROUP BY constraint_row.oid, constraint_row.confdeltype
                """
            )
            fk_rows = cur.fetchall()
            assert any(action == "c" and source == ["user_id"] and target == ["id"] for action, source, target in fk_rows)

            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            cur.execute("SELECT count(*) FROM transactions WHERE user_id = %s", (user_id,))
            assert cur.fetchone()[0] == 0
        conn.commit()

        observer = _connect(dsn, schema)
        try:
            with observer.cursor() as cur:
                cur.execute("SELECT count(*) FROM transactions WHERE user_id = %s", (user_id,))
                assert cur.fetchone()[0] == 0
        finally:
            observer.close()
    finally:
        conn.close()
