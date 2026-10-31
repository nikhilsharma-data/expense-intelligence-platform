from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import main


def _signed_token(user_id=23, version=4):
    return jwt.encode(
        {
            "sub": str(user_id),
            "token_version": version,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        main.JWT_SECRET_KEY,
        algorithm=main.JWT_ALGORITHM,
    )


def _connection(principal_row=None, endpoint_one=None, endpoint_rows=None):
    connection = MagicMock()
    auth_cursor = MagicMock()
    auth_cursor.fetchone.return_value = principal_row
    connection.cursor.return_value.__enter__.return_value = auth_cursor
    handler_cursor = connection.cursor.return_value
    handler_cursor.fetchone.return_value = endpoint_one
    handler_cursor.fetchall.return_value = endpoint_rows or []
    return connection, auth_cursor, handler_cursor


def _get(monkeypatch, path, principal_row=None, endpoint_one=None, endpoint_rows=None, token=None):
    monkeypatch.setattr(main.app.router, "on_startup", [])
    connection, auth_cursor, handler_cursor = _connection(principal_row, endpoint_one, endpoint_rows)
    factory = MagicMock(return_value=connection)
    monkeypatch.setattr(main, "get_db_connection", factory)
    headers = {"Authorization": f"Bearer {token or _signed_token()}"} if token is not None or principal_row else {}
    response = TestClient(main.app).get(path, headers=headers)
    return response, connection, auth_cursor, handler_cursor, factory


@pytest.mark.parametrize("path", ["/admin/overview", "/admin/users"])
def test_admin_endpoints_reject_anonymous_http_requests_without_database_access(monkeypatch, path):
    monkeypatch.setattr(main.app.router, "on_startup", [])
    factory = MagicMock(side_effect=AssertionError("anonymous request must not access the database"))
    monkeypatch.setattr(main, "get_db_connection", factory)

    response = TestClient(main.app).get(path)

    assert response.status_code == 401
    factory.assert_not_called()


@pytest.mark.parametrize("path", ["/admin/overview", "/admin/users"])
def test_normal_user_receives_forbidden_from_admin_endpoints(monkeypatch, path):
    row = (4, "user", True, "Normal User", "user@example.com")
    response, connection, _auth, handler, factory = _get(monkeypatch, path, row)

    assert response.status_code == 403
    assert response.json()["detail"] == "Administrator access is required"
    factory.assert_called_once()
    handler.execute.assert_not_called()
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()


def test_me_returns_database_role_and_safe_profile_for_authenticated_user(monkeypatch):
    row = (4, "user", True, "Normal User", "user@example.com")
    response, connection, auth_cursor, _handler, factory = _get(monkeypatch, "/me", row)

    assert response.status_code == 200
    assert response.json() == {
        "id": 23,
        "name": "Normal User",
        "email": "user@example.com",
        "role": "user",
        "is_active": True,
    }
    assert auth_cursor.execute.call_args.args[1] == (23,)
    assert factory.call_count == 1
    connection.close.assert_called_once()
    assert not {"password_hash", "access_token", "token", "token_version"}.intersection(response.json())


def test_inactive_account_is_rejected_with_http_401(monkeypatch):
    row = (4, "admin", False, "Inactive Admin", "inactive@example.com")
    response, connection, _auth, handler, _factory = _get(monkeypatch, "/admin/overview", row)

    assert response.status_code == 401
    assert response.json()["detail"] == "Account is inactive"
    handler.execute.assert_not_called()
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()


def test_admin_overview_returns_aggregate_statistics_only(monkeypatch):
    row = (4, "admin", True, "Admin User", "admin@example.com")
    aggregate = (12, 10, 2, 1, 84)
    response, connection, _auth, handler, _factory = _get(
        monkeypatch, "/admin/overview", row, endpoint_one=aggregate
    )

    assert response.status_code == 200
    assert response.json() == {
        "total_users": 12,
        "active_users": 10,
        "inactive_users": 2,
        "admin_users": 1,
        "total_transactions": 84,
    }
    call = handler.execute.call_args
    sql = call.args[0]
    params = call.args[1] if len(call.args) > 1 else ()
    assert "count(*)" in sql.lower()
    assert "description" not in sql.lower() and "amount" not in sql.lower()
    assert params == ()
    connection.close.assert_called_once()


def test_admin_user_listing_searches_and_paginates_without_sensitive_fields(monkeypatch):
    row = (4, "admin", True, "Admin User", "admin@example.com")
    response, connection, _auth, handler, _factory = _get(
        monkeypatch,
        "/admin/users?search=sample&limit=2&offset=4",
        row,
        endpoint_one=(3,),
        endpoint_rows=[(25, "Sample Person", "sample@example.com", "user", True)],
    )

    assert response.status_code == 200
    assert response.json() == {
        "users": [{
            "id": 25,
            "name": "Sample Person",
            "email": "sample@example.com",
            "role": "user",
            "is_active": True,
        }],
        "total": 3,
        "limit": 2,
        "offset": 4,
    }
    count_sql, count_params = handler.execute.call_args_list[0].args
    page_sql, page_params = handler.execute.call_args_list[1].args
    assert "name ILIKE %s OR email ILIKE %s" in count_sql
    assert count_params == ("%sample%", "%sample%")
    assert "ORDER BY id ASC LIMIT %s OFFSET %s" in page_sql
    assert page_params == ("%sample%", "%sample%", 2, 4)
    body = response.json()
    assert all(not {"password_hash", "token", "token_hash", "api_key"}.intersection(user) for user in body["users"])
    connection.close.assert_called_once()


def test_role_change_increments_token_version_and_revokes_the_old_http_token(monkeypatch):
    mutation_cursor = MagicMock()
    mutation_cursor.fetchone.return_value = (23,)
    assert main.set_user_role(mutation_cursor, 23, "admin") == (23,)
    mutation_sql, mutation_params = mutation_cursor.execute.call_args.args
    assert "token_version = token_version + 1" in mutation_sql
    assert mutation_params == ("admin", 23)

    new_version_row = (5, "admin", True, "User Became Admin", "user@example.com")
    old_token = _signed_token(version=4)
    response, connection, _auth, handler, _factory = _get(
        monkeypatch, "/admin/overview", new_version_row, token=old_token
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication token has been invalidated"
    handler.execute.assert_not_called()
    connection.close.assert_called_once()


def test_deactivation_increments_token_version_and_signup_cannot_choose_role():
    cursor = MagicMock()
    cursor.fetchone.return_value = (23,)
    main.set_user_active_status(cursor, 23, False)
    sql, params = cursor.execute.call_args.args
    assert "token_version = token_version + 1" in sql
    assert params == (False, 23)

    with pytest.raises(ValueError, match="is_active must be a boolean"):
        main.set_user_active_status(cursor, 23, 1)
    with pytest.raises(ValueError, match="role must be user or admin"):
        main.set_user_role(cursor, 23, "owner")

    with pytest.raises(ValidationError):
        main.SignupRequest(name="Person Name", email="person@example.com", password="safe-password", role="admin")

    signup_cursor = MagicMock()
    signup_cursor.fetchone.return_value = (24,)
    main.signup(main.SignupRequest(name="Person Name", email="person@example.com", password="safe-password"), (MagicMock(), signup_cursor))
    insert_sql, _params = signup_cursor.execute.call_args.args
    assert "INSERT INTO users (name, email, password_hash)" in insert_sql
    assert "role" not in insert_sql.lower()
