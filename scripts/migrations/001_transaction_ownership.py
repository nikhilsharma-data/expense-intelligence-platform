"""Explicit PostgreSQL migration for transaction ownership integrity.

Run with an explicitly selected schema and MIGRATION_DATABASE_URL, for example:
    python -m scripts.migrations.001_transaction_ownership --schema public

This module never loads .env or imports the application's database helper.
"""

import argparse
import os

import psycopg2
from psycopg2 import sql


CONSTRAINT_NAME = "fk_transactions_user_id_users"


class MigrationError(RuntimeError):
    """Raised when existing schema or data is unsafe to migrate."""


def _column_metadata(cursor, schema, table, column):
    cursor.execute(
        """
        SELECT relation.oid, attribute.attnum, format_type(attribute.atttypid, attribute.atttypmod)
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        JOIN pg_attribute AS attribute ON attribute.attrelid = relation.oid
        WHERE namespace.nspname = %s
          AND relation.relname = %s
          AND relation.relkind = 'r'
          AND attribute.attname = %s
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
        """,
        (schema, table, column),
    )
    return cursor.fetchone()


def apply_migration(connection, schema):
    """Validate ownership data and add NOT NULL plus the cascading FK.

    The caller supplies an already-open connection and an explicit schema. All
    DDL and validation run inside one transaction; any failure rolls it back.
    """
    if not isinstance(schema, str) or not schema or "\x00" in schema:
        raise MigrationError("An explicit, non-empty PostgreSQL schema is required")
    if connection.closed:
        raise MigrationError("An open, idle database connection is required")
    if (
        connection.get_transaction_status()
        != psycopg2.extensions.TRANSACTION_STATUS_IDLE
    ):
        raise MigrationError(
            "The migration requires an idle connection to avoid committing unrelated work"
        )

    try:
        with connection:
            with connection.cursor() as cursor:
                users_id = _column_metadata(cursor, schema, "users", "id")
                transactions_user_id = _column_metadata(
                    cursor, schema, "transactions", "user_id"
                )
                if users_id is None or transactions_user_id is None:
                    raise MigrationError(
                        f"Schema {schema!r} must contain users.id and transactions.user_id"
                    )
                users_oid, users_id_attnum, users_type = users_id
                (
                    transactions_oid,
                    transactions_user_attnum,
                    transactions_type,
                ) = transactions_user_id
                if users_type != "integer" or transactions_type != "integer":
                    raise MigrationError(
                        "Expected users.id and transactions.user_id to both be INTEGER"
                    )

                # Prevent ownership changes while validation and DDL are in flight.
                cursor.execute(
                    sql.SQL("LOCK TABLE {}.users, {}.transactions IN ACCESS EXCLUSIVE MODE").format(
                        sql.Identifier(schema), sql.Identifier(schema)
                    )
                )
                cursor.execute(
                    sql.SQL("SELECT count(*) FROM {}.transactions WHERE user_id IS NULL").format(
                        sql.Identifier(schema)
                    )
                )
                null_count = cursor.fetchone()[0]
                cursor.execute(
                    sql.SQL(
                        """SELECT count(*)
                           FROM {schema}.transactions AS t
                           WHERE t.user_id IS NOT NULL
                             AND NOT EXISTS (
                                 SELECT 1 FROM {schema}.users AS app_user
                                 WHERE app_user.id = t.user_id
                             )"""
                    ).format(schema=sql.Identifier(schema))
                )
                orphan_count = cursor.fetchone()[0]
                if null_count or orphan_count:
                    raise MigrationError(
                        "Transaction ownership validation failed: "
                        f"{null_count} NULL user_id row(s), "
                        f"{orphan_count} orphaned user_id row(s). "
                        "No rows were changed; resolve ownership manually and rerun."
                    )

                cursor.execute(
                    """SELECT conname, conkey::smallint[], confrelid,
                              confkey::smallint[], confdeltype, convalidated
                       FROM pg_constraint
                       WHERE conrelid = %s AND contype = 'f'
                         AND %s = ANY(conkey::smallint[])""",
                    (transactions_oid, transactions_user_attnum),
                )
                user_id_foreign_keys = cursor.fetchall()
                equivalent = None
                for name, source_columns, target_table, target_columns, delete_action, validated in user_id_foreign_keys:
                    is_equivalent = (
                        list(source_columns) == [transactions_user_attnum]
                        and target_table == users_oid
                        and list(target_columns) == [users_id_attnum]
                        and delete_action == "c"
                    )
                    if not is_equivalent:
                        raise MigrationError(
                            f"Conflicting foreign key {name!r} uses transactions.user_id; "
                            "no existing constraint was changed."
                        )
                    equivalent = (name, validated)

                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {}.transactions ALTER COLUMN user_id SET NOT NULL"
                    ).format(
                        sql.Identifier(schema)
                    )
                )

                if equivalent:
                    name, validated = equivalent
                    if not validated:
                        cursor.execute(
                            sql.SQL(
                                "ALTER TABLE {}.transactions VALIDATE CONSTRAINT {}"
                            ).format(
                                sql.Identifier(schema), sql.Identifier(name)
                            )
                        )
                else:
                    cursor.execute(
                        sql.SQL(
                            "ALTER TABLE {}.transactions ADD CONSTRAINT {} "
                            "FOREIGN KEY (user_id) REFERENCES {}.users(id) ON DELETE CASCADE"
                        ).format(
                            sql.Identifier(schema),
                            sql.Identifier(CONSTRAINT_NAME),
                            sql.Identifier(schema),
                        )
                    )
    except MigrationError:
        raise
    except Exception as exc:
        raise MigrationError(
            f"Transaction ownership migration failed and was rolled back: {exc}"
        ) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema", required=True, help="Exact PostgreSQL schema to migrate"
    )
    args = parser.parse_args(argv)
    dsn = os.environ.get("MIGRATION_DATABASE_URL")
    if not dsn:
        parser.error("MIGRATION_DATABASE_URL must be explicitly configured")

    connection = psycopg2.connect(dsn)
    try:
        apply_migration(connection, args.schema)
    finally:
        connection.close()
    print(f"Transaction ownership migration applied to schema {args.schema!r}.")


if __name__ == "__main__":
    main()
