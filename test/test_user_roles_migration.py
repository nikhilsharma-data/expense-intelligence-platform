import importlib
from unittest.mock import MagicMock

import bcrypt
import psycopg2
import pytest


migration = importlib.import_module("scripts.migrations.002_user_roles")
bootstrap = importlib.import_module("scripts.bootstrap_admin")


ROLE_CHECK = "CHECK ((role = ANY (ARRAY['user'::text, 'admin'::text])))"


def _migration_connection(columns, constraints):
    columns = list(columns)
    if not any(row[0] == "token_version" for row in columns):
        columns.append(("token_version", "integer", "NO", "0"))
    connection = MagicMock()
    connection.closed = 0
    connection.get_transaction_status.return_value = psycopg2.extensions.TRANSACTION_STATUS_IDLE
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [(77,), (0,)]
    cursor.fetchall.side_effect = [columns, constraints]
    return connection, cursor


def test_user_roles_migration_adds_safe_defaults_and_allowed_role_constraint():
    connection, cursor = _migration_connection([], [])

    migration.apply_migration(connection, "test_schema")

    sql_text = "\n".join(str(call.args[0]) for call in cursor.execute.call_args_list)
    assert "LOCK TABLE" in sql_text
    assert "role TEXT NOT NULL DEFAULT 'user'" in sql_text
    assert "is_active BOOLEAN NOT NULL DEFAULT TRUE" in sql_text
    assert "token_version = token_version + 1" in sql_text
    assert "CHECK (role IN ('user', 'admin'))" in sql_text
    connection.__exit__.assert_called_once_with(None, None, None)


def test_user_roles_migration_is_rerunnable_without_duplicate_check():
    connection, cursor = _migration_connection(
        [
            ("role", "text", "NO", "'user'::text"),
            ("is_active", "boolean", "NO", "true"),
        ],
        [("users_role_allowed_check", ROLE_CHECK, True)],
    )

    migration.apply_migration(connection, "test_schema")

    sql_text = "\n".join(str(call.args[0]) for call in cursor.execute.call_args_list)
    assert "ADD CONSTRAINT" not in sql_text
    assert "ALTER COLUMN role SET DEFAULT 'user'" in sql_text


def test_user_roles_migration_rejects_conflicting_check_and_rolls_back():
    connection, _cursor = _migration_connection(
        [("role", "text", "NO", "'user'::text"), ("is_active", "boolean", "NO", "true")],
        [("users_role_allowed_check", "CHECK ((role <> 'blocked'))", True)],
    )

    with pytest.raises(migration.MigrationError, match="Conflicting CHECK constraint"):
        migration.apply_migration(connection, "test_schema")

    assert connection.__exit__.call_args.args[0] is migration.MigrationError


def test_user_roles_migration_aborts_unsupported_existing_roles():
    connection, cursor = _migration_connection([], [])
    cursor.fetchone.side_effect = [(77,), (2,)]

    with pytest.raises(migration.MigrationError, match="2 unsupported role value"):
        migration.apply_migration(connection, "test_schema")

    assert connection.__exit__.call_args.args[0] is migration.MigrationError


def test_first_admin_bootstrap_hashes_password_and_sets_admin_explicitly():
    connection = MagicMock()
    connection.closed = 0
    connection.get_transaction_status.return_value = psycopg2.extensions.TRANSACTION_STATUS_IDLE
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [(0,), (91,)]
    password = "a long unique test-only password"

    user_id = bootstrap.provision_first_admin(
        connection, "test_schema", "Test Admin", " ADMIN@example.com ", password
    )

    assert user_id == 91
    insert_sql, params = cursor.execute.call_args.args
    assert "role, is_active, token_version" in str(insert_sql)
    assert "'admin', TRUE, 0" in str(insert_sql)
    assert params[1] == "admin@example.com"
    assert bcrypt.checkpw(password.encode(), params[2].encode())
    assert params[2] != password
    connection.__exit__.assert_called_once_with(None, None, None)


def test_first_admin_bootstrap_refuses_existing_active_admin():
    connection = MagicMock()
    connection.closed = 0
    connection.get_transaction_status.return_value = psycopg2.extensions.TRANSACTION_STATUS_IDLE
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (1,)

    with pytest.raises(bootstrap.AdminBootstrapError, match="active administrator already exists"):
        bootstrap.provision_first_admin(
            connection, "test_schema", "Test Admin", "admin@example.com", "a long unique password"
        )

    assert not any("INSERT INTO" in str(call.args[0]) for call in cursor.execute.call_args_list)
    assert connection.__exit__.call_args.args[0] is bootstrap.AdminBootstrapError


def test_bootstrap_cli_requires_explicit_database_configuration(monkeypatch):
    monkeypatch.delenv("ADMIN_BOOTSTRAP_DATABASE_URL", raising=False)
    connect = MagicMock(side_effect=AssertionError("must not connect without explicit configuration"))
    monkeypatch.setattr(bootstrap.psycopg2, "connect", connect)

    with pytest.raises(SystemExit):
        bootstrap.main(["--schema", "public"])

    connect.assert_not_called()


def test_bootstrap_refuses_short_password_without_database_mutation():
    connection = MagicMock()
    connection.closed = 0
    connection.get_transaction_status.return_value = psycopg2.extensions.TRANSACTION_STATUS_IDLE

    with pytest.raises(bootstrap.AdminBootstrapError, match="at least 12 characters"):
        bootstrap.provision_first_admin(connection, "test_schema", "Test Admin", "a@example.com", "short")

    connection.cursor.assert_not_called()
