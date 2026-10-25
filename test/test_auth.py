from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from fastapi.security import HTTPAuthorizationCredentials

import main


def _db_for_token_version(version):
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (version,)
    return conn


def _credentials(payload):
    token = jwt.encode(payload, main.JWT_SECRET_KEY, algorithm=main.JWT_ALGORITHM)
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_auth_requires_bearer_credentials():
    with pytest.raises(HTTPException) as error:
        main.get_current_user_id(None)
    assert error.value.status_code == 401


def test_login_failure_does_not_reveal_account_existence(monkeypatch):
    unknown_cursor = MagicMock()
    unknown_cursor.fetchone.return_value = None
    with pytest.raises(HTTPException) as unknown_error:
        main.login(main.LoginRequest(email="person@example.com", password="password"), (MagicMock(), unknown_cursor))

    existing_cursor = MagicMock()
    existing_cursor.fetchone.return_value = (7, "Person", "stored-hash")
    monkeypatch.setattr(main, "verify_password", lambda _password, _hashed: False)
    with pytest.raises(HTTPException) as wrong_password_error:
        main.login(main.LoginRequest(email="person@example.com", password="wrong-password"), (MagicMock(), existing_cursor))

    assert unknown_error.value.status_code == wrong_password_error.value.status_code == 401
    assert unknown_error.value.detail == wrong_password_error.value.detail == "Invalid email or password"


def test_auth_rejects_invalid_and_expired_tokens(monkeypatch):
    with pytest.raises(HTTPException) as invalid:
        main.get_current_user_id(HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-jwt"))
    assert invalid.value.status_code == 401

    expired = _credentials({"sub": "7", "token_version": 0, "exp": datetime.now(timezone.utc) - timedelta(minutes=1)})
    with pytest.raises(HTTPException) as error:
        main.get_current_user_id(expired)
    assert error.value.status_code == 401


def test_auth_validates_subject_and_token_version(monkeypatch):
    monkeypatch.setattr(main, "get_db_connection", lambda: _db_for_token_version(2))
    valid = _credentials({"sub": "7", "token_version": 2, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)})
    assert main.get_current_user_id(valid) == 7

    malformed_subject = _credentials({"sub": "not-an-id", "token_version": 2, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)})
    with pytest.raises(HTTPException) as error:
        main.get_current_user_id(malformed_subject)
    assert error.value.status_code == 401

    invalidated = _credentials({"sub": "7", "token_version": 1, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)})
    with pytest.raises(HTTPException) as error:
        main.get_current_user_id(invalidated)
    assert error.value.status_code == 401


def test_auth_rejects_token_after_account_deletion(monkeypatch):
    conn = _db_for_token_version(None)
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    monkeypatch.setattr(main, "get_db_connection", lambda: conn)
    token = _credentials({"sub": "7", "token_version": 0, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)})
    with pytest.raises(HTTPException) as error:
        main.get_current_user_id(token)
    assert error.value.status_code == 401


def test_all_user_data_routes_depend_on_verified_identity():
    protected_paths = {
        "/upload", "/delete-transactions", "/delete-account", "/summary",
        "/transactions", "/category-breakdown", "/monthly-trend", "/insights",
        "/forecast", "/anomalies", "/research-evaluation",
    }
    routes = {route.path: route for route in main.app.routes if getattr(route, "path", None)}
    for path in protected_paths:
        route = routes[path]
        assert any(dependency.call is main.get_current_user_id for dependency in route.dependant.dependencies)


def test_research_evaluation_endpoint_rejects_unauthenticated_http_request(monkeypatch):
    monkeypatch.setattr(main.app.router, "on_startup", [])
    response = TestClient(main.app).get("/research-evaluation")

    assert response.status_code == 401
    assert response.json()["detail"] == "Bearer authentication is required"


def test_research_evaluation_endpoint_accepts_authenticated_http_request(monkeypatch):
    monkeypatch.setattr(main.app.router, "on_startup", [])
    monkeypatch.setattr(main, "get_db_connection", lambda: _db_for_token_version(3))
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
