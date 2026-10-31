from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import jwt
import requests
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

import main

DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard.py"


def _token(user_id=73, token_version=4):
    return jwt.encode(
        {
            "sub": str(user_id),
            "token_version": token_version,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        main.JWT_SECRET_KEY,
        algorithm=main.JWT_ALGORITHM,
    )


def test_dashboard_overview_builds_existing_sections_from_synthetic_rows():
    rows = [
        (date(2026, 1, 5), "Salary", 1000, "Income"),
        (date(2026, 1, 4), "Grocery Store", -30, "Groceries"),
        (date(2026, 1, 3), "Coffee", -10, "Food"),
        (date(2025, 12, 28), "Cafe", -20, "Food"),
    ]

    overview = main.build_dashboard_overview(rows, "all")

    assert overview["summary"] == {
        "total": 940.0,
        "total_expense": 60.0,
        "total_income": 1000.0,
        "savings_percent": 94.0,
    }
    assert overview["categories"] == [
        {"category": "Groceries", "total": 30.0},
        {"category": "Food", "total": 30.0},
    ]
    assert overview["trend"] == [
        {"month": "Dec-25", "total": -20.0, "income": 0.0, "expenses": 20.0},
        {"month": "Jan-26", "total": 960.0, "income": 1000.0, "expenses": 40.0},
    ]
    assert len(overview["transactions"]) == 4
    assert overview["transactions"][0]["description"] == "Salary"
    assert overview["transactions_total"] == 4
    assert overview["available_categories"] == ["Food", "Groceries", "Income"]
    assert overview["forecast"]["history_transactions"] == 3
    assert overview["research_evaluation"]["data_scope"].startswith("Synthetic evaluation")


def test_dashboard_overview_filters_date_sensitive_sections_but_keeps_history():
    today = date.today()
    recent = today - timedelta(days=2)
    old = today - timedelta(days=60)
    overview = main.build_dashboard_overview(
        [
            (recent, "Recent", -20, "Food"),
            (old, "Older", -80, "Shopping"),
        ],
        "30d",
    )

    assert overview["summary"]["total_expense"] == 20
    assert overview["categories"] == [{"category": "Food", "total": 20.0}]
    assert len(overview["transactions"]) == 2
    assert overview["forecast"]["history_transactions"] == 2


def test_dashboard_overview_is_authenticated_scoped_and_uses_one_transaction_query(monkeypatch):
    monkeypatch.setattr(main.app.router, "on_startup", [])
    conn = MagicMock()
    auth_cursor = MagicMock()
    auth_cursor.fetchone.return_value = (4, "user", True, "Synthetic User", "synthetic@example.com")
    conn.cursor.return_value.__enter__.return_value = auth_cursor
    query_cursor = conn.cursor.return_value
    query_cursor.fetchall.return_value = [
        (date(2026, 1, 5), "Salary", 1000, "Income"),
        (date(2026, 1, 4), "Coffee", -20, "Food"),
    ]
    connection_factory = MagicMock(return_value=conn)
    monkeypatch.setattr(main, "get_db_connection", connection_factory)
    headers = {"Authorization": f"Bearer {_token()}"}

    response = TestClient(main.app).get("/dashboard-overview?range=all", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "summary", "categories", "trend", "insights", "transactions",
        "transactions_total", "available_categories", "forecast", "anomalies",
        "research_evaluation",
    }
    assert body["summary"]["total_income"] == 1000
    assert len(body["transactions"]) == 2
    assert body["transactions_total"] == 2
    assert body["available_categories"] == ["Food", "Income"]
    assert len(body["research_evaluation"]["categorization"]["methods"]) == 2
    assert connection_factory.call_count == 1
    assert auth_cursor.execute.call_args.args[1] == (73,)
    query_sql, query_params = query_cursor.execute.call_args.args
    assert "WHERE user_id = %s" in query_sql
    assert query_params == (73,)
    query_cursor.fetchall.assert_called_once()
    conn.close.assert_called_once()


def test_dashboard_overview_rejects_unauthenticated_request_without_database_access(monkeypatch):
    monkeypatch.setattr(main.app.router, "on_startup", [])
    connection_factory = MagicMock(side_effect=AssertionError("unexpected connection"))
    monkeypatch.setattr(main, "get_db_connection", connection_factory)

    response = TestClient(main.app).get("/dashboard-overview")

    assert response.status_code == 401
    connection_factory.assert_not_called()


def test_dashboard_initial_load_uses_one_authenticated_overview_request(monkeypatch):
    calls = []
    payload = {
        "summary": {"total": 75, "total_expense": 25, "total_income": 100, "savings_percent": 75},
        "categories": [{"category": "Food", "total": 25}],
        "trend": [{"month": "Jan-26", "total": 75, "income": 100, "expenses": 25}],
        "insights": {"insights": ["Synthetic overview insight"]},
        "transactions": [{"date": "2026-01-01", "description": "Cafe", "amount": -25, "category": "Food"}],
        "transactions_total": 1,
        "available_categories": ["Food"],
        "forecast": {},
        "anomalies": {},
        "research_evaluation": {},
    }

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/me"):
            return SimpleNamespace(status_code=200, json=lambda: {"id": 73, "name": "Synthetic User", "email": "synthetic@example.com", "role": "user", "is_active": True}, text="")
        return SimpleNamespace(status_code=200, json=lambda: payload, text="")

    monkeypatch.setattr(requests, "get", fake_get)
    app = AppTest.from_file(str(DASHBOARD)).run(timeout=20)
    app.session_state["logged_in"] = True
    app.session_state["user_id"] = 73
    app.session_state["user_name"] = "Synthetic User"
    app.session_state["access_token"] = "synthetic-token"
    app.run(timeout=20)

    overview_calls = [call for call in calls if call[0].endswith("/dashboard-overview")]
    profile_calls = [call for call in calls if call[0].endswith("/me")]
    assert len(overview_calls) == len(profile_calls) == 1
    assert overview_calls[0][1]["headers"] == {"Authorization": "Bearer synthetic-token"}
    assert overview_calls[0][1]["params"] == {"range": "all"}
    assert profile_calls[0][1]["headers"] == {"Authorization": "Bearer synthetic-token"}
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["Total income"] == "100.00 units"
    assert metrics["Total expenses"] == "25.00 units"
    assert metrics["Net transaction flow"] == "75.00 units"
    assert metrics["Savings percentage"] == "75.0%"
    assert "Total Balance" not in metrics
    assert "Admin Portal" not in app.radio(key="workspace_page").options


def test_transactions_page_uses_bearer_search_category_and_bounded_pages(monkeypatch):
    calls = []
    overview = {
        "summary": {"total": 0, "total_expense": 0, "total_income": 0, "savings_percent": 0},
        "categories": [], "trend": [], "insights": {"insights": []},
        "transactions": [], "transactions_total": 0,
        "available_categories": ["Food"], "forecast": {}, "anomalies": {},
        "research_evaluation": {},
    }

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/me"):
            return SimpleNamespace(status_code=200, json=lambda: {"id": 73, "name": "Synthetic User", "email": "synthetic@example.com", "role": "user", "is_active": True}, text="")
        if url.endswith("/dashboard-overview"):
            return SimpleNamespace(status_code=200, json=lambda: overview, text="")
        return SimpleNamespace(
            status_code=200,
            json=lambda: [{"date": "2026-01-02", "description": "Cafe", "amount": -5, "category": "Food"}],
            headers={"X-Has-More": "true"},
        )

    monkeypatch.setattr(requests, "get", fake_get)
    app = AppTest.from_file(str(DASHBOARD)).run(timeout=20)
    app.session_state["logged_in"] = True
    app.session_state["user_id"] = 73
    app.session_state["user_name"] = "Synthetic User"
    app.session_state["access_token"] = "synthetic-token"
    app.run(timeout=20)

    app.radio(key="workspace_page").set_value("Transactions")
    app.run(timeout=20)
    app.text_input(key="transaction_search").set_value("cafe")
    app.selectbox(key="transaction_category").set_value("Food")
    app.run(timeout=20)

    transaction_request = next(call for call in reversed(calls) if call[0].endswith("/transactions"))
    assert transaction_request[1]["headers"] == {"Authorization": "Bearer synthetic-token"}
    assert transaction_request[1]["params"] == {"limit": 100, "offset": 0, "search": "cafe", "category": "Food"}
    assert any("More results are available" in item.value for item in app.caption)


def test_import_page_requires_replace_confirmation_before_upload(monkeypatch):
    upload_calls = []
    overview = {
        "summary": {"total": 0, "total_expense": 0, "total_income": 0, "savings_percent": 0},
        "categories": [], "trend": [], "insights": {"insights": []},
        "transactions": [], "transactions_total": 0,
        "available_categories": [], "forecast": {}, "anomalies": {},
        "research_evaluation": {},
    }
    def fake_get(url, **_kwargs):
        if url.endswith("/me"):
            return SimpleNamespace(status_code=200, json=lambda: {"id": 73, "name": "Synthetic User", "email": "synthetic@example.com", "role": "user", "is_active": True}, text="")
        return SimpleNamespace(status_code=200, json=lambda: overview, text="")
    monkeypatch.setattr(requests, "get", fake_get)

    def fake_post(url, **kwargs):
        upload_calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: {"rows_loaded": 1, "rows_skipped": 0}, text="")

    monkeypatch.setattr(requests, "post", fake_post)
    app = AppTest.from_file(str(DASHBOARD)).run(timeout=20)
    app.session_state["logged_in"] = True
    app.session_state["user_id"] = 73
    app.session_state["user_name"] = "Synthetic User"
    app.session_state["access_token"] = "synthetic-token"
    app.run(timeout=20)
    app.radio(key="workspace_page").set_value("Import Statements")
    app.run(timeout=20)
    app.file_uploader(key="statement_upload").set_value(
        ("synthetic.csv", b"Date,Description,Amount\n2026-01-01,Cafe,-5\n", "text/csv")
    )
    app.run(timeout=20)

    process_button = app.button(key="process_statement")
    assert process_button.disabled
    process_button.click().run(timeout=20)
    assert upload_calls == []

    app.checkbox(key="confirm_replace_import").set_value(True)
    app.run(timeout=20)
    app.button(key="process_statement").click().run(timeout=20)
    assert len(upload_calls) == 1
    assert upload_calls[0][0].endswith("/upload")
    assert upload_calls[0][1]["headers"] == {"Authorization": "Bearer synthetic-token"}


def test_admin_portal_navigation_and_read_only_views_use_authenticated_api(monkeypatch):
    calls = []
    overview = {
        "summary": {"total": 0, "total_expense": 0, "total_income": 0, "savings_percent": 0},
        "categories": [], "trend": [], "insights": {"insights": []},
        "transactions": [], "transactions_total": 0, "available_categories": [],
        "forecast": {}, "anomalies": {}, "research_evaluation": {},
    }

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/me"):
            payload = {"id": 73, "name": "Synthetic Admin", "email": "admin@example.com", "role": "admin", "is_active": True}
        elif url.endswith("/dashboard-overview"):
            payload = overview
        elif url.endswith("/admin/overview"):
            payload = {"total_users": 4, "active_users": 3, "inactive_users": 1, "admin_users": 1, "total_transactions": 20}
        else:
            payload = {"users": [{"id": 4, "name": "Synthetic User", "email": "user@example.com", "role": "user", "is_active": True}], "total": 1, "limit": 25, "offset": 0}
        return SimpleNamespace(status_code=200, json=lambda: payload, text="", headers={})

    monkeypatch.setattr(requests, "get", fake_get)
    app = AppTest.from_file(str(DASHBOARD)).run(timeout=20)
    app.session_state["logged_in"] = True
    app.session_state["user_id"] = 73
    app.session_state["user_name"] = "Synthetic Admin"
    app.session_state["access_token"] = "synthetic-admin-token"
    app.run(timeout=20)

    navigation = app.radio(key="workspace_page")
    assert "Admin Portal" in navigation.options
    navigation.set_value("Admin Portal")
    app.run(timeout=20)
    assert any(call[0].endswith("/admin/overview") for call in calls)
    assert {metric.label for metric in app.metric}.issuperset({"Users", "Active users", "Inactive users", "Transactions"})

    app.radio(key="admin_portal_view").set_value("User Management")
    app.run(timeout=20)
    user_call = next(call for call in reversed(calls) if call[0].endswith("/admin/users"))
    assert user_call[1]["headers"] == {"Authorization": "Bearer synthetic-admin-token"}
    assert not any("Deactivate" in button.label or "Change role" in button.label for button in app.button)
