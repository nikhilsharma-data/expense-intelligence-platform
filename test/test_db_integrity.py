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
from starlette.requests import Request

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


def test_valid_csv_upload_bulk_replaces_transactions_and_commits(monkeypatch):
    conn = MagicMock()
    cur = MagicMock()
    bulk_calls = []

    def fake_execute_values(cursor, sql, records, page_size):
        bulk_calls.append((cursor, sql, list(records), page_size))

    monkeypatch.setattr(main, "execute_values", fake_execute_values)
    csv_content = b"Date,Description,Amount\n2026-01-01,Cafe Latte,-5.00\n"

    result = asyncio.run(
        main.upload_file(17, _upload("valid.csv", csv_content), (conn, cur))
    )

    assert result == {"status": "success", "rows_loaded": 1, "rows_skipped": 0}
    assert cur.execute.call_count == 1
    assert cur.execute.call_args_list[0].args[0] == "DELETE FROM transactions WHERE user_id = %s"
    assert cur.execute.call_args_list[0].args[1] == (17,)
    assert len(bulk_calls) == 1
    assert bulk_calls[0][0] is cur
    assert "INSERT INTO transactions" in bulk_calls[0][1]
    assert bulk_calls[0][2] == [(17, date(2026, 1, 1), "Cafe Latte", -5.0, "Food")]
    assert bulk_calls[0][3] == 1000
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


def test_bulk_upload_failure_after_delete_rolls_back_without_commit(monkeypatch):
    conn = MagicMock()
    cur = MagicMock()
    bulk_calls = []

    def fail_bulk_insert(cursor, sql, records, page_size):
        bulk_calls.append((cursor, sql, list(records), page_size))
        raise RuntimeError("simulated database insert failure")

    monkeypatch.setattr(main, "execute_values", fail_bulk_insert)
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
    assert cur.execute.call_args_list[0].args[1] == (23,)
    assert len(bulk_calls) == 1
    assert len(bulk_calls[0][2]) == 2
    assert all(record[0] == 23 for record in bulk_calls[0][2])
    assert bulk_calls[0][3] == 1000
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()


def test_valid_pdf_upload_uses_one_bulk_insert(monkeypatch):
    conn, cur = MagicMock(), MagicMock()
    transactions = [
        {"date": "01-01-2026", "description": "Coffee Shop", "amount": -5.0},
        {"date": "02-01-2026", "description": "Grocery Store", "amount": -12.0},
    ]
    monkeypatch.setattr(main, "extract_pdf_transactions", lambda _content: ("synthetic", transactions))
    bulk_calls = []
    monkeypatch.setattr(
        main,
        "execute_values",
        lambda cursor, sql, records, page_size: bulk_calls.append((cursor, sql, list(records), page_size)),
    )

    result = asyncio.run(main.upload_file(28, _upload("synthetic.pdf", b"pdf"), (conn, cur)))

    assert result["rows_loaded"] == 2
    assert cur.execute.call_count == 1
    assert cur.execute.call_args.args[1] == (28,)
    assert len(bulk_calls) == 1
    assert [record[0] for record in bulk_calls[0][2]] == [28, 28]
    assert bulk_calls[0][3] == 1000
    conn.commit.assert_called_once()


def test_transaction_read_binds_the_supplied_authenticated_user_id():
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = [(date(2026, 1, 1), "Coffee", -5, "Food")]

    result = main.get_transactions(response=main.Response(), user_id=31, db=(conn, cur))

    sql, params = cur.execute.call_args.args
    assert "WHERE user_id = %s" in sql
    assert params == (31,)
    assert result == [{"date": "2026-01-01", "description": "Coffee", "amount": -5, "category": "Food"}]


def test_transaction_search_date_filters_and_pagination_remain_user_scoped():
    conn, cur = MagicMock(), MagicMock()
    cur.fetchall.return_value = [
        (date(2026, 1, 3), "market one", -5, "Food"),
        (date(2026, 1, 2), "market two", -7, "Food"),
        (date(2026, 1, 1), "market three", -8, "Food"),
    ]
    response = main.Response()

    result = main.get_transactions(
        response=response,
        user_id=31,
        db=(conn, cur),
        search="market",
        category="Food",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 1, 3),
        limit=2,
        offset=4,
    )

    sql, params = cur.execute.call_args.args
    assert "WHERE user_id = %s" in sql
    assert "description ILIKE %s" in sql
    assert "category = %s" in sql
    assert "date >= %s" in sql and "date <= %s" in sql
    assert params == (31, "%market%", "Food", date(2026, 1, 1), date(2026, 1, 3), 3, 4)
    assert len(result) == 2
    assert response.headers["X-Has-More"] == "true"


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


def test_request_database_dependency_rolls_back_and_closes_on_error(monkeypatch):
    conn, cursor = MagicMock(), MagicMock()
    conn.cursor.return_value = cursor
    monkeypatch.setattr(main, "get_db_connection", lambda: conn)
    request = Request({"type": "http", "headers": [], "method": "GET", "path": "/", "query_string": b""})
    dependency = main.get_db(request)

    provided_db = next(dependency)
    assert provided_db == (conn, cursor)
    with pytest.raises(RuntimeError, match="simulated endpoint failure"):
        dependency.throw(RuntimeError("simulated endpoint failure"))

    conn.rollback.assert_called_once()
    cursor.close.assert_called_once()
    conn.close.assert_called_once()
    assert request.state.db_connection is None
