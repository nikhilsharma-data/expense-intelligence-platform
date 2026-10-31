"""Explicit, transactional migration for account role and activation state.

Run only after selecting the intended database and schema explicitly:
    MIGRATION_DATABASE_URL=... python -m scripts.migrations.002_user_roles --schema public

This module never loads .env or imports the application's database connector.
"""

import argparse
import os
import re

import psycopg2
from psycopg2 import sql


ROLE_CONSTRAINT = "users_role_allowed_check"


class MigrationError(RuntimeError):
    """Raised when the target schema cannot be migrated safely."""


def _equivalent_role_check(definition):
    compact = re.sub(r"\s+|::text|\"", "", str(definition).lower())
    return compact in {
        "check((role=any(array['user','admin'])))",
        "check((role=any(array['admin','user'])))",
    }


def apply_migration(connection, schema):
    """Add role and activation columns without changing transaction ownership."""
    if not isinstance(schema, str) or not schema or "\x00" in schema:
        raise MigrationError("An explicit, non-empty PostgreSQL schema is required")
    if connection.closed:
        raise MigrationError("An open, idle database connection is required")
    if connection.get_transaction_status() != psycopg2.extensions.TRANSACTION_STATUS_IDLE:
        raise MigrationError("The migration requires an idle connection to avoid committing unrelated work")

    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT relation.oid
                    FROM pg_class AS relation
                    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = %s AND relation.relname = 'users'
                      AND relation.relkind = 'r'
                    """,
                    (schema,),
                )
                table = cursor.fetchone()
                if not table:
                    raise MigrationError(f"Schema {schema!r} must contain a users table")
                users_oid = table[0]
                cursor.execute(sql.SQL("LOCK TABLE {}.users IN ACCESS EXCLUSIVE MODE").format(sql.Identifier(schema)))

                cursor.execute(
                    """
                    SELECT column_name, data_type, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = 'users'
                      AND column_name IN ('role', 'is_active', 'token_version')
                    """,
                    (schema,),
                )
                columns = {row[0]: row[1:] for row in cursor.fetchall()}
                token_version = columns.get("token_version")
                if token_version is None or token_version[0] != "integer" or token_version[1] != "NO":
                    raise MigrationError(
                        "The users.token_version INTEGER column is required before the role migration"
                    )

                schema_changed = "role" not in columns or "is_active" not in columns
                if "role" not in columns:
                    cursor.execute(
                        sql.SQL("ALTER TABLE {}.users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
                        .format(sql.Identifier(schema))
                    )
                elif columns["role"][0] != "text":
                    raise MigrationError("users.role must have PostgreSQL TEXT type; no column was changed")

                if "is_active" not in columns:
                    cursor.execute(
                        sql.SQL("ALTER TABLE {}.users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE")
                        .format(sql.Identifier(schema))
                    )
                elif columns["is_active"][0] != "boolean":
                    raise MigrationError("users.is_active must have PostgreSQL BOOLEAN type; no column was changed")

                changed_rows = sql.SQL("TRUE") if schema_changed else sql.SQL("role IS NULL OR is_active IS NULL")
                cursor.execute(
                    sql.SQL(
                        "UPDATE {}.users SET role = COALESCE(role, 'user'), "
                        "is_active = COALESCE(is_active, TRUE), "
                        "token_version = token_version + 1 WHERE {}"
                    ).format(sql.Identifier(schema), changed_rows)
                )
                cursor.execute(
                    sql.SQL("SELECT count(*) FROM {}.users WHERE role NOT IN ('user', 'admin')")
                    .format(sql.Identifier(schema))
                )
                invalid_roles = cursor.fetchone()
                if not invalid_roles or invalid_roles[0]:
                    raise MigrationError(
                        f"Role validation found {invalid_roles[0] if invalid_roles else 'unknown'} "
                        "unsupported role value(s); no schema changes were committed."
                    )
                cursor.execute(
                    sql.SQL("ALTER TABLE {}.users ALTER COLUMN role SET DEFAULT 'user', ALTER COLUMN role SET NOT NULL")
                    .format(sql.Identifier(schema))
                )
                cursor.execute(
                    sql.SQL("ALTER TABLE {}.users ALTER COLUMN is_active SET DEFAULT TRUE, ALTER COLUMN is_active SET NOT NULL")
                    .format(sql.Identifier(schema))
                )

                cursor.execute(
                    """
                    SELECT role_constraint.conname, pg_get_constraintdef(role_constraint.oid), role_constraint.convalidated
                    FROM pg_constraint AS role_constraint
                    WHERE role_constraint.conrelid = %s
                      AND role_constraint.contype = 'c'
                      AND pg_get_constraintdef(role_constraint.oid) ILIKE %s
                    """,
                    (users_oid, "%role%"),
                )
                constraints = cursor.fetchall()
                if any(not _equivalent_role_check(row[1]) for row in constraints):
                    raise MigrationError("Conflicting CHECK constraint found for users.role; no constraint was replaced")

                if not constraints:
                    cursor.execute(
                        sql.SQL(
                            "ALTER TABLE {}.users ADD CONSTRAINT {} "
                            "CHECK (role IN ('user', 'admin'))"
                        ).format(sql.Identifier(schema), sql.Identifier(ROLE_CONSTRAINT))
                    )
                else:
                    for constraint_name, definition, validated in constraints:
                        if not validated:
                            cursor.execute(
                                sql.SQL("ALTER TABLE {}.users VALIDATE CONSTRAINT {}")
                                .format(sql.Identifier(schema), sql.Identifier(constraint_name))
                            )
    except MigrationError:
        raise
    except Exception as exc:
        raise MigrationError(f"User-role migration failed and was rolled back: {exc}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", required=True, help="Exact PostgreSQL schema to migrate")
    args = parser.parse_args(argv)
    dsn = os.environ.get("MIGRATION_DATABASE_URL")
    if not dsn:
        parser.error("MIGRATION_DATABASE_URL must be explicitly configured")

    connection = psycopg2.connect(dsn)
    try:
        apply_migration(connection, args.schema)
    finally:
        connection.close()
    print(f"User-role migration applied to schema {args.schema!r}.")


if __name__ == "__main__":
    main()
