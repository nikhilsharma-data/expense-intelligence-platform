import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_db_connection():
    required_vars = ["DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT"]
    missing_vars = [name for name in required_vars if not os.getenv(name)]

    if missing_vars:
        raise RuntimeError(
            "Missing database environment variables: "
            + ", ".join(missing_vars)
        )

    return psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        sslmode=os.getenv("DB_SSLMODE", "require"),
    )
