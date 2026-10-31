from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

import main


def _db_for_token_version(version, role="user", is_active=True):
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (version, role, is_active, "Test User", "person@example.com")
    return conn


def _credentials(payload):
    token = jwt.encode(payload, main.JWT_SECRET_KEY, algorithm=main.JWT_ALGORITHM)
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _request():
    return Request({"type": "http", "headers": [], "method": "GET", "path": "/", "query_string": b""})


def _connection_with_token_version(version, handler_row=None):
    conn = MagicMock()
    auth_cursor = MagicMock()
    auth_cursor.fetchone.return_value = (version, "user", True, "Test User", "person@example.com")
    conn.cursor.return_value.__enter__.return_value = auth_cursor
    conn.cursor.return_value.fetchone.return_value = handler_row
    return conn


def test_auth_requires_bearer_credentials():
    with pytest.raises(HTTPException) as error:
        main.get_current_user_id(_request(), None)
    assert error.value.status_code == 401


def test_login_failure_does_not_reveal_account_existence(monkeypatch):
    unknown_cursor = MagicMock()
    unknown_cursor.fetchone.return_value = None
    with pytest.raises(HTTPException) as unknown_error:
        main.login(main.LoginRequest(email="person@example.com", password="password"), (MagicMock(), unknown_cursor))

    existing_cursor = MagicMock()
    existing_cursor.fetchone.return_value = (7, "Person", "stored-hash", True, 0)
    monkeypatch.setattr(main, "verify_password", lambda _password, _hashed: False)
    with pytest.raises(HTTPException) as wrong_password_error:
        main.login(main.LoginRequest(email="person@example.com", password="wrong-password"), (MagicMock(), existing_cursor))

    assert unknown_error.value.status_code == wrong_password_error.value.status_code == 401
    assert unknown_error.value.detail == wrong_password_error.value.detail == "Invalid email or password"


def test_auth_rejects_invalid_and_expired_tokens(monkeypatch):
    with pytest.raises(HTTPException) as invalid:
        main.get_current_user_id(
            _request(),
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-jwt"),
        )
    assert invalid.value.status_code == 401

    expired = _credentials({"sub": "7", "token_version": 0, "exp": datetime.now(timezone.utc) - timedelta(minutes=1)})
    with pytest.raises(HTTPException) as error:
        main.get_current_user_id(_request(), expired)
    assert error.value.status_code == 401


def test_auth_validates_subject_and_token_version(monkeypatch):
    conn = _db_for_token_version(2)
    monkeypatch.setattr(main, "get_db_connection", lambda: conn)
    valid = _credentials({"sub": "7", "token_version": 2, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)})
    valid_request = _request()
    assert main.get_current_user_id(valid_request, valid) == 7
    conn.close()

    malformed_subject = _credentials({"sub": "not-an-id", "token_version": 2, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)})
    with pytest.raises(HTTPException) as error:
        main.get_current_user_id(_request(), malformed_subject)
    assert error.value.status_code == 401

    invalidated = _credentials({"sub": "7", "token_version": 1, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)})
    with pytest.raises(HTTPException) as error:
        main.get_current_user_id(_request(), invalidated)
    assert error.value.status_code == 401


def test_auth_rejects_token_after_account_deletion(monkeypatch):
    conn = _db_for_token_version(None)
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    monkeypatch.setattr(main, "get_db_connection", lambda: conn)
    token = _credentials({"sub": "7", "token_version": 0, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)})
    with pytest.raises(HTTPException) as error:
        main.get_current_user_id(_request(), token)
    assert error.value.status_code == 401


def test_auth_database_failure_rolls_back_and_closes_new_request_connection(monkeypatch):
    conn = MagicMock()
    auth_cursor = MagicMock()
    auth_cursor.execute.side_effect = RuntimeError("simulated token lookup failure")
    conn.cursor.return_value.__enter__.return_value = auth_cursor
    monkeypatch.setattr(main, "get_db_connection", lambda: conn)
    request = _request()
    token = _credentials(
        {
            "sub": "73",
            "token_version": 1,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        }
    )

    with pytest.raises(RuntimeError, match="simulated token lookup failure"):
        main.get_current_user_id(request, token)

    conn.rollback.assert_called_once()
    conn.close.assert_called_once()
    assert request.state.db_connection is None


def test_all_user_data_routes_depend_on_verified_identity():
    protected_paths = {
        "/upload", "/delete-transactions", "/delete-account", "/summary",
        "/transactions", "/category-breakdown", "/monthly-trend", "/insights",
        "/forecast", "/anomalies", "/research-evaluation", "/dashboard-overview", "/logout",
    }
    routes = {route.path: route for route in main.app.routes if getattr(route, "path", None)}
    for path in protected_paths:
        route = routes[path]
        assert any(dependency.call is main.get_current_user_id for dependency in route.dependant.dependencies)
        assert any(dependency.call is main.get_db for dependency in route.dependant.dependencies)
    for path in ("/admin/overview", "/admin/users"):
        assert any(dependency.call is main.require_admin for dependency in routes[path].dependant.dependencies)
        assert any(dependency.call is main.get_db for dependency in routes[path].dependant.dependencies)
    assert any(dependency.call is main.get_current_principal for dependency in routes["/me"].dependant.dependencies)


def test_research_evaluation_endpoint_rejects_unauthenticated_http_request(monkeypatch):
    monkeypatch.setattr(main.app.router, "on_startup", [])
    response = TestClient(main.app).get("/research-evaluation")

    assert response.status_code == 401
    assert response.json()["detail"] == "Bearer authentication is required"


def test_research_evaluation_endpoint_accepts_authenticated_http_request(monkeypatch):
    monkeypatch.setattr(main.app.router, "on_startup", [])
    connections = []

    def connection_factory():
        conn = _db_for_token_version(3)
        connections.append(conn)
        return conn

    monkeypatch.setattr(main, "get_db_connection", connection_factory)
    token = jwt.encode(
        {
            "sub": "73",
            "token_version": 3,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        main.JWT_SECRET_KEY,
        algorithm=main.JWT_ALGORITHM,
    )

    response = TestClient(main.app).get(
        "/research-evaluation",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert len(connections) == 1
    connections[0].close.assert_called_once()
    body = response.json()

    assert set(body) == {"categorization", "anomaly_detection", "data_scope"}
    assert body["data_scope"].startswith("Synthetic evaluation examples only")
    categorization = body["categorization"]
    assert isinstance(categorization["dataset_size"], int)
    assert isinstance(categorization["independent_synthetic_holdout"]["evaluation_size"], int)
    assert set(categorization["methods"]) == {"keyword_rules_baseline", "multinomial_naive_bayes"}
    anomaly = body["anomaly_detection"]
    assert isinstance(anomaly["evaluation_size"], int)
    assert isinstance(anomaly["unscored_insufficient_history"], int)
    assert isinstance(anomaly["confusion_matrix"]["true_positive"], int)
    assert anomaly["cases"]
    assert not {"user_id", "email", "transactions"}.intersection(body)


def test_authenticated_http_request_shares_one_database_connection(monkeypatch):
    monkeypatch.setattr(main.app.router, "on_startup", [])
    conn = MagicMock()
    auth_cursor = MagicMock()
    auth_cursor.fetchone.return_value = (6, "user", True, "Test User", "person@example.com")
    conn.cursor.return_value.__enter__.return_value = auth_cursor
    handler_cursor = conn.cursor.return_value
    handler_cursor.fetchone.return_value = (100, -25, 125)
    connection_factory = MagicMock(return_value=conn)
    monkeypatch.setattr(main, "get_db_connection", connection_factory)
    token = jwt.encode(
        {
            "sub": "73",
            "token_version": 6,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        main.JWT_SECRET_KEY,
        algorithm=main.JWT_ALGORITHM,
    )

    response = TestClient(main.app).get(
        "/summary",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["total_income"] == 125
    connection_factory.assert_called_once()
    assert auth_cursor.execute.call_args.args[1] == (73,)
    assert handler_cursor.execute.call_args.args[1] == (73,)
    conn.close.assert_called_once()
    handler_cursor.close.assert_called_once()


def test_logout_endpoint_rejects_unauthenticated_http_request(monkeypatch):
    monkeypatch.setattr(main.app.router, "on_startup", [])
    connection = MagicMock(side_effect=AssertionError("database must not be accessed"))
    monkeypatch.setattr(main, "get_db_connection", connection)

    response = TestClient(main.app).post("/logout")

    assert response.status_code == 401
    connection.assert_not_called()


def test_authenticated_logout_increments_token_version_and_commits(monkeypatch):
    monkeypatch.setattr(main.app.router, "on_startup", [])
    logout_conn = _connection_with_token_version(4)
    logout_cursor = logout_conn.cursor.return_value
    logout_cursor.fetchone.return_value = (73,)
    connection_factory = MagicMock(return_value=logout_conn)
    monkeypatch.setattr(main, "get_db_connection", connection_factory)
    credentials = _credentials(
        {
            "sub": "73",
            "token_version": 4,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        }
    )

    response = TestClient(main.app).post(
        "/logout",
        headers={"Authorization": f"Bearer {credentials.credentials}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "message": "Logged out; server-side sessions have been revoked."
    }
    sql, params = logout_cursor.execute.call_args.args
    assert "UPDATE users" in sql
    assert "token_version = token_version + 1" in sql
    assert params == (73,)
    logout_conn.commit.assert_called_once()
    logout_conn.rollback.assert_not_called()
    connection_factory.assert_called_once()
    logout_conn.close.assert_called_once()


def test_logout_rejects_reuse_of_the_same_jwt_on_protected_endpoint(monkeypatch):
    monkeypatch.setattr(main.app.router, "on_startup", [])
    logout_conn = _connection_with_token_version(4)
    logout_cursor = logout_conn.cursor.return_value
    logout_cursor.fetchone.return_value = (73,)
    after_logout_conn = _connection_with_token_version(5)
    connections = []

    def connection_factory():
        conn = logout_conn if not connections else after_logout_conn
        connections.append(conn)
        return conn

    monkeypatch.setattr(main, "get_db_connection", connection_factory)
    credentials = _credentials(
        {
            "sub": "73",
            "token_version": 4,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        }
    )
    headers = {"Authorization": f"Bearer {credentials.credentials}"}
    client = TestClient(main.app)

    logout_response = client.post("/logout", headers=headers)
    protected_response = client.get("/summary", headers=headers)

    assert logout_response.status_code == 200
    assert protected_response.status_code == 401
    assert protected_response.json()["detail"] == "Authentication token has been invalidated"
    assert len(connections) == 2
    logout_conn.commit.assert_called_once()
    assert logout_conn.close.call_count == 1
    assert after_logout_conn.close.call_count == 1


def test_logout_database_failure_rolls_back_and_returns_safe_error():
    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = RuntimeError("private database detail")

    with pytest.raises(HTTPException) as error:
        main.logout(73, (conn, cursor))

    assert error.value.status_code == 500
    assert "private database detail" not in error.value.detail
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()
