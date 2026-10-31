"""Unit tests for the opt-in PostgreSQL integration connection guards."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_test_module(module_name, filename):
    module_path = PROJECT_ROOT / "test" / filename
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("module_name", "filename"),
    [
        ("_db_integration_guard", "test_db_integration.py"),
        (
            "_ownership_migration_guard",
            "test_transaction_ownership_migration.py",
        ),
    ],
)
@pytest.mark.parametrize("override", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"])
def test_libpq_target_override_rejected_before_driver_connect(
    monkeypatch, module_name, filename, override
):
    module = _load_test_module(module_name, filename)
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql://synthetic@localhost:5432/expense_intelligence_test",
    )
    monkeypatch.setenv("ALLOW_TEST_DB_DESTRUCTIVE_SETUP", "1")
    monkeypatch.setenv(override, "unsafe-test-override")
    for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        if name != override:
            monkeypatch.delenv(name, raising=False)
    driver_connect = Mock()
    monkeypatch.setattr(module.psycopg2, "connect", driver_connect)

    with pytest.raises(RuntimeError, match="libpq host or service overrides"):
        module._connect()

    driver_connect.assert_not_called()
