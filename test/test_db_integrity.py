"""Mock-based checks for database integrity behavior in request handlers.

These tests verify SQL scoping and transaction-control calls. They do not
claim to prove persisted PostgreSQL rollback behavior; no database is accessed.
"""

import asyncio
from datetime import date
from io import BytesIO
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, UploadFile

import main


def _upload(filename, content):
    return UploadFile(filename=filename, file=BytesIO(content))


def test_invalid_csv_upload_does_not_delete_existing_transactions():
    conn = MagicMock()
    cur = MagicMock()
    csv_content = (
        b"date,description,amount\n"
        b"not-a-date,Coffee,5.00\n"
        b"2026-01-02,Groceries,not-an-amount\n"
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(main.upload_file(17, _upload("invalid.csv", csv_content), (conn, cur)))

    assert error.value.status_code == 422
    assert not any(
        "DELETE FROM TRANSACTIONS" in " ".join(str(arg) for arg in call.args).upper()
        for call in cur.execute.call_args_list
    )
    conn.commit.assert_not_called()


def test_csv_with_duplicate_normalized_headers_is_rejected_without_mutation():
    conn = MagicMock()
    cur = MagicMock()
    csv_content = (
        b"date,Date,description,amount\n"
        b"2026-01-01,2026-01-02,Coffee,-5.00\n"
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(main.upload_file(17, _upload("duplicate-headers.csv", csv_content), (conn, cur)))

    assert error.value.status_code == 400
    assert "duplicate column names" in error.value.detail.lower()
    assert not any(
        "DELETE FROM TRANSACTIONS" in " ".join(str(arg) for arg in call.args).upper()
        or "INSERT INTO TRANSACTIONS" in " ".join(str(arg) for arg in call.args).upper()
        for call in cur.execute.call_args_list
    )
    conn.commit.assert_not_called()


def test_valid_csv_upload_still_replaces_transactions_and_commits():
    conn = MagicMock()
    cur = MagicMock()
    csv_content = b"Date,Description,Amount\n2026-01-01,Coffee,-5.00\n"

    result = asyncio.run(
        main.upload_file(17, _upload("valid.csv", csv_content), (conn, cur))
    )

    assert result == {"status": "success", "rows_loaded": 1, "rows_skipped": 0}
    assert cur.execute.call_count == 2
    assert cur.execute.call_args_list[0].args[0] == "DELETE FROM transactions WHERE user_id = %s"
    assert cur.execute.call_args_list[0].args[1] == (17,)
    assert cur.execute.call_args_list[1].args[1][0] == 17
    conn.commit.assert_called_once()
    conn.rollback.assert_not_called()


def test_pdf_without_valid_transactions_does_not_delete_existing_transactions(monkeypatch):
    conn = MagicMock()
    cur = MagicMock()
    monkeypatch.setattr(main, "extract_pdf_transactions", lambda _content: ("", []))

    with pytest.raises(HTTPException) as error:
        asyncio.run(main.upload_file(17, _upload("empty.pdf", b"pdf bytes"), (conn, cur)))

    assert error.value.status_code == 422
    cur.execute.assert_not_called()
    conn.commit.assert_not_called()


def test_upload_insert_failure_rolls_back_without_committing_replacement():
    conn = MagicMock()
    cur = MagicMock()
    insert_count = 0

    def execute(sql, params=None):
        nonlocal insert_count
        normalized_sql = " ".join(sql.split()).upper()
        if normalized_sql.startswith("INSERT INTO TRANSACTIONS"):
            insert_count += 1
            if insert_count == 2:
                raise RuntimeError("simulated database insert failure")

    cur.execute.side_effect = execute
    csv_content = (
        b"date,description,amount\n"
        b"2026-01-01,Coffee,-5.00\n"
        b"2026-01-02,Groceries,-20.00\n"
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(main.upload_file(23, _upload("replacement.csv", csv_content), (conn, cur)))

    assert error.value.status_code == 500
    executed_sql = [" ".join(call.args[0].split()).upper() for call in cur.execute.call_args_list]
    assert executed_sql[0] == "DELETE FROM TRANSACTIONS WHERE USER_ID = %S"
    assert executed_sql[1].startswith("INSERT INTO TRANSACTIONS")
    assert executed_sql[2].startswith("INSERT INTO TRANSACTIONS")
    assert cur.execute.call_args_list[0].args[1] == (23,)
    assert cur.execute.call_args_list[1].args[1][0] == 23
    assert cur.execute.call_args_list[2].args[1][0] == 23
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()


def test_transaction_read_binds_the_supplied_authenticated_user_id():
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = [(date(2026, 1, 1), "Coffee", -5, "Food")]

    result = main.get_transactions(user_id=31, db=(conn, cur))

    sql, params = cur.execute.call_args.args
    assert "WHERE user_id = %s" in sql
    assert params == (31,)
    assert result == [{"date": "2026-01-01", "description": "Coffee", "amount": -5, "category": "Food"}]


def test_transaction_delete_is_scoped_to_the_supplied_authenticated_user_id():
    conn = MagicMock()
    cur = MagicMock()

    result = main.delete_transactions(user_id=31, db=(conn, cur))

    sql, params = cur.execute.call_args.args
    assert "DELETE FROM transactions WHERE user_id = %s" in sql
    assert params == (31,)
    conn.commit.assert_called_once()
    conn.rollback.assert_not_called()
    assert result == {"message": "Transactions deleted successfully"}


def test_account_deletion_rolls_back_if_user_delete_fails():
    conn = MagicMock()
    cur = MagicMock()
    cur.execute.side_effect = [None, RuntimeError("simulated user delete failure")]

    with pytest.raises(HTTPException) as error:
        main.delete_account(user_id=41, db=(conn, cur))

    assert error.value.status_code == 500
    assert cur.execute.call_count == 2
    assert cur.execute.call_args_list[0].args[1] == (41,)
    assert cur.execute.call_args_list[1].args[1] == (41,)
    assert "DELETE FROM transactions WHERE user_id = %s" in cur.execute.call_args_list[0].args[0]
    assert "DELETE FROM users WHERE id = %s" in cur.execute.call_args_list[1].args[0]
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()
