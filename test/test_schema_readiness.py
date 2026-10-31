from unittest.mock import MagicMock

import pytest

import main


def _connection_with_ownership_metadata(column_metadata, foreign_key_exists):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [column_metadata, (foreign_key_exists,)]
    return connection, cursor


def _configure_role_readiness(cursor, role_columns=None, invalid_rows=0, constraints=None):
    if role_columns is None:
        role_columns = [
            ("role", "NO", "text", "'user'::text"),
            ("is_active", "NO", "boolean", "true"),
        ]
    if constraints is None:
        constraints = [("CHECK ((role = ANY (ARRAY['user'::text, 'admin'::text])))", True)]
    cursor.fetchall.side_effect = [role_columns, constraints]
    existing_fetches = cursor.fetchone.side_effect
    cursor.fetchone.side_effect = (list(existing_fetches) if existing_fetches is not None else []) + [(invalid_rows,)]


def _role_only_connection(role_columns=None, invalid_rows=0, constraints=None):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    _configure_role_readiness(cursor, role_columns, invalid_rows, constraints)
    cursor.fetchone.side_effect = [(invalid_rows,)]
    return connection, cursor


def test_schema_readiness_accepts_nonnull_integer_and_cascade_foreign_key():
    connection, cursor = _connection_with_ownership_metadata(("NO", "integer"), True)

    main.validate_transaction_ownership_schema(connection)

    assert cursor.execute.call_count == 2
    assert "information_schema.columns" in cursor.execute.call_args_list[0].args[0]
    assert "confdeltype = 'c'" in cursor.execute.call_args_list[1].args[0]


@pytest.mark.parametrize(
    "column_metadata",
    [None, ("YES", "integer"), ("NO", "bigint")],
)
def test_schema_readiness_rejects_missing_nullable_or_incompatible_column(column_metadata):
    connection, cursor = _connection_with_ownership_metadata(column_metadata, True)

    with pytest.raises(RuntimeError, match="transactions.user_id"):
        main.validate_transaction_ownership_schema(connection)

    assert cursor.execute.call_count == 1


def test_schema_readiness_rejects_missing_or_non_cascading_foreign_key():
    connection, _cursor = _connection_with_ownership_metadata(("NO", "integer"), False)

    with pytest.raises(RuntimeError, match="ON DELETE CASCADE foreign key"):
        main.validate_transaction_ownership_schema(connection)


def test_startup_creates_owned_transactions_table_and_runs_readiness_check(monkeypatch):
    connection, cursor = _connection_with_ownership_metadata(("NO", "integer"), True)
    _configure_role_readiness(cursor)
    monkeypatch.setattr(main, "get_db_connection", lambda: connection)

    main.startup_db()

    transaction_ddl = next(
        call.args[0]
        for call in cursor.execute.call_args_list
        if "CREATE TABLE IF NOT EXISTS transactions" in call.args[0]
    )
    assert "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE" in transaction_ddl
    users_ddl = next(
        call.args[0]
        for call in cursor.execute.call_args_list
        if "CREATE TABLE IF NOT EXISTS users" in call.args[0]
    )
    assert "role TEXT NOT NULL DEFAULT 'user'" in users_ddl
    assert "CHECK (role IN ('user', 'admin'))" in users_ddl
    assert "is_active BOOLEAN NOT NULL DEFAULT TRUE" in users_ddl
    assert not any(
        "ALTER TABLE transactions" in call.args[0]
        for call in cursor.execute.call_args_list
    )
    connection.commit.assert_called_once()
    connection.close.assert_called_once()


def test_startup_fails_safely_when_explicit_migration_is_missing(monkeypatch):
    connection, cursor = _connection_with_ownership_metadata(("YES", "integer"), False)
    monkeypatch.setattr(main, "get_db_connection", lambda: connection)

    with pytest.raises(RuntimeError, match="Apply the transaction-ownership migration"):
        main.startup_db()

    connection.commit.assert_not_called()
    connection.rollback.assert_called_once()
    assert not any(
        "ALTER TABLE transactions" in call.args[0]
        for call in cursor.execute.call_args_list
    )
    connection.close.assert_called_once()


def test_startup_fails_safely_when_user_role_migration_is_missing(monkeypatch):
    connection, cursor = _connection_with_ownership_metadata(("NO", "integer"), True)
    cursor.fetchall.side_effect = [[], []]
    monkeypatch.setattr(main, "get_db_connection", lambda: connection)

    with pytest.raises(RuntimeError, match="users.role"):
        main.startup_db()

    connection.commit.assert_not_called()
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()


def test_user_role_readiness_accepts_only_active_schema_and_validated_role_check():
    connection, cursor = _role_only_connection()

    main.validate_user_role_schema(connection)

    assert "information_schema.columns" in cursor.execute.call_args_list[0].args[0]
    assert "role NOT IN ('user', 'admin')" in cursor.execute.call_args_list[1].args[0]
    assert "pg_constraint" in cursor.execute.call_args_list[2].args[0]


@pytest.mark.parametrize(
    "columns",
    [
        [("role", "YES", "text", "'user'::text"), ("is_active", "NO", "boolean", "true")],
        [("role", "NO", "varchar", "'user'::text"), ("is_active", "NO", "boolean", "true")],
        [("role", "NO", "text", "'admin'::text"), ("is_active", "NO", "boolean", "true")],
        [("role", "NO", "text", "'user'::text"), ("is_active", "YES", "boolean", "true")],
    ],
)
def test_user_role_readiness_rejects_missing_nullable_or_unsafe_columns(columns):
    connection, cursor = _role_only_connection(role_columns=columns)

    with pytest.raises(RuntimeError, match=r"users\.(role|is_active)"):
        main.validate_user_role_schema(connection)


def test_user_role_readiness_rejects_invalid_data_or_missing_constraint():
    connection, cursor = _role_only_connection(invalid_rows=1)
    with pytest.raises(RuntimeError, match="invalid user roles"):
        main.validate_user_role_schema(connection)

    connection, cursor = _role_only_connection(constraints=[])
    with pytest.raises(RuntimeError, match="validated users.role check"):
        main.validate_user_role_schema(connection)
