"""Operator-invoked one-time bootstrap for the first active administrator.

Set ADMIN_BOOTSTRAP_DATABASE_URL explicitly, then run:
    python -m scripts.bootstrap_admin --schema public

This script does not load .env, create a default account, or print passwords.
"""

import argparse
import getpass
import os

import bcrypt
import psycopg2
from psycopg2 import sql


class AdminBootstrapError(RuntimeError):
    """Raised when first-admin provisioning is unsafe or cannot complete."""


def provision_first_admin(connection, schema, name, email, password):
    if not isinstance(schema, str) or not schema or "\x00" in schema:
        raise AdminBootstrapError("An explicit, non-empty PostgreSQL schema is required")
    if not connection or connection.closed:
        raise AdminBootstrapError("An open database connection is required")
    if connection.get_transaction_status() != psycopg2.extensions.TRANSACTION_STATUS_IDLE:
        raise AdminBootstrapError("Bootstrap requires an idle connection")
    name = (name or "").strip()
    email = (email or "").strip().lower()
    if len(name) < 2 or "@" not in email:
        raise AdminBootstrapError("A valid name and email address are required")
    if not isinstance(password, str) or len(password) < 12:
        raise AdminBootstrapError("Administrator password must be at least 12 characters")

    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("LOCK TABLE {}.users IN ACCESS EXCLUSIVE MODE").format(sql.Identifier(schema)))
                cursor.execute(
                    sql.SQL("SELECT count(*) FROM {}.users WHERE role = 'admin' AND is_active = TRUE")
                    .format(sql.Identifier(schema))
                )
                row = cursor.fetchone()
                if not row or row[0] != 0:
                    raise AdminBootstrapError("An active administrator already exists; bootstrap refused")

                password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {}.users "
                        "(name, email, password_hash, role, is_active, token_version) "
                        "VALUES (%s, %s, %s, 'admin', TRUE, 0) RETURNING id"
                    ).format(sql.Identifier(schema)),
                    (name, email, password_hash),
                )
                created = cursor.fetchone()
                if not created:
                    raise AdminBootstrapError("Administrator account creation returned no user ID")
                return created[0]
    except AdminBootstrapError:
        raise
    except Exception:
        raise AdminBootstrapError("Administrator bootstrap failed and was rolled back.") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", required=True, help="Exact PostgreSQL schema containing users")
    args = parser.parse_args(argv)
    dsn = os.environ.get("ADMIN_BOOTSTRAP_DATABASE_URL")
    if not dsn:
        parser.error("ADMIN_BOOTSTRAP_DATABASE_URL must be explicitly configured")

    name = input("Administrator name: ").strip()
    email = input("Administrator email: ").strip()
    password = getpass.getpass("New administrator password (12+ characters): ")
    confirmation = getpass.getpass("Confirm administrator password: ")
    if password != confirmation:
        parser.error("Administrator passwords do not match")

    connection = psycopg2.connect(dsn)
    try:
        provision_first_admin(connection, args.schema, name, email, password)
    finally:
        connection.close()
    print("Initial administrator account created.")


if __name__ == "__main__":
    main()
