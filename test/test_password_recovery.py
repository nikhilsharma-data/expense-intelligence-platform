from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock

import bcrypt
import pytest
from fastapi import HTTPException
import requests
from streamlit.testing.v1 import AppTest

import main


GENERIC_MESSAGE = (
    "If an account exists for this email, a password reset link has been sent."
)
DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard.py"


def _db(*fetch_rows):
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.side_effect = list(fetch_rows)
    return conn, cur


@pytest.mark.parametrize("user_row", [(27,), None])
def test_forgot_password_returns_same_generic_message_for_existing_and_missing_user(
    monkeypatch, user_row
):
    conn, cur = _db(user_row)
    send_email = MagicMock()
    monkeypatch.setattr(main, "send_password_reset_email", send_email)

    response = main.forgot_password(
        main.ForgotPasswordRequest(email="person@example.com"), (conn, cur)
    )

    assert response == {"message": GENERIC_MESSAGE}
    assert send_email.called is bool(user_row)
    if user_row:
        conn.commit.assert_called_once()
    else:
        conn.commit.assert_not_called()


def test_forgot_password_rolls_back_new_token_if_email_delivery_fails(monkeypatch, caplog):
    conn, cur = _db((27,))
    monkeypatch.setattr(
        main,
        "send_password_reset_email",
        MagicMock(side_effect=RuntimeError("provider detail must not be logged")),
    )

    response = main.forgot_password(
        main.ForgotPasswordRequest(email="person@example.com"), (conn, cur)
    )

    assert response == {"message": GENERIC_MESSAGE}
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()
    assert "provider detail must not be logged" not in caplog.text


def test_resend_helper_uses_sdk_and_url_encodes_token(monkeypatch):
    send = MagicMock()
    monkeypatch.setattr(main, "RESEND_API_KEY", "test-resend-key")
    monkeypatch.setattr(main, "RESEND_FROM_EMAIL", "onboarding@resend.dev")
    monkeypatch.setattr(main, "PASSWORD_RESET_URL", "http://localhost:8501/?view=login")
    monkeypatch.setattr(main.resend, "api_key", None, raising=False)
    monkeypatch.setattr(main.resend.Emails, "send", send)

    main.send_password_reset_email("person@example.com", "test token/+?")

    assert main.resend.api_key == "test-resend-key"
    params = send.call_args.args[0]
    assert params["from"] == "onboarding@resend.dev"
    assert params["to"] == ["person@example.com"]
    query = parse_qs(urlsplit(params["text"].split("Reset your password: ", 1)[1].splitlines()[0]).query)
    assert query == {"view": ["login"], "reset_token": ["test token/+?"]}
    assert "test-resend-key" not in str(params)


def test_resend_helper_rejects_nonlocal_http_and_does_not_send(monkeypatch):
    send = MagicMock()
    monkeypatch.setattr(main, "RESEND_API_KEY", "test-resend-key")
    monkeypatch.setattr(main, "RESEND_FROM_EMAIL", "sender@example.com")
    monkeypatch.setattr(main, "PASSWORD_RESET_URL", "http://example.com/reset")
    monkeypatch.setattr(main.resend.Emails, "send", send)

    with pytest.raises(RuntimeError):
        main.send_password_reset_email("person@example.com", "synthetic-token")

    send.assert_not_called()


def test_resend_helper_requires_deployment_reset_url(monkeypatch):
    send = MagicMock()
    monkeypatch.setattr(main, "RESEND_API_KEY", "test-resend-key")
    monkeypatch.setattr(main, "RESEND_FROM_EMAIL", "sender@example.com")
    monkeypatch.setattr(main, "PASSWORD_RESET_URL", None)
    monkeypatch.setattr(main.resend.Emails, "send", send)

    with pytest.raises(RuntimeError):
        main.send_password_reset_email("person@example.com", "synthetic-token")

    send.assert_not_called()


def test_reset_password_hashes_password_and_invalidates_session_atomically():
    conn, cur = _db((12, 27))

    response = main.reset_password(
        main.ResetPasswordRequest(token="synthetic-token", new_password="new-password-123"),
        (conn, cur),
    )

    assert response == {"message": "Password has been reset successfully."}
    assert cur.execute.call_count == 2
    claim_sql, claim_params = cur.execute.call_args_list[0].args
    assert "UPDATE password_reset_tokens" in claim_sql
    assert "used_at IS NULL" in claim_sql
    assert "expires_at > NOW()" in claim_sql
    assert "RETURNING id, user_id" in claim_sql
    assert claim_params == (main.hashlib.sha256(b"synthetic-token").hexdigest(),)

    password_sql, password_params = cur.execute.call_args_list[1].args
    assert "UPDATE users" in password_sql
    stored_hash, user_id = password_params
    assert user_id == 27
    assert stored_hash != "new-password-123"
    assert bcrypt.checkpw(b"new-password-123", stored_hash.encode("utf-8"))
    assert "token_version = token_version + 1" in password_sql
    conn.commit.assert_called_once()


@pytest.mark.parametrize("token", ["invalid-token", "expired-token", "already-used-token"])
def test_invalid_expired_and_used_tokens_are_rejected_without_password_change(token):
    conn, cur = _db(None)

    with pytest.raises(HTTPException) as error:
        main.reset_password(
            main.ResetPasswordRequest(token=token, new_password="new-password-123"),
            (conn, cur),
        )

    assert error.value.status_code == 400
    assert error.value.detail == "Invalid or expired password reset token"
    assert cur.execute.call_count == 1
    assert "UPDATE users" not in cur.execute.call_args.args[0]
    conn.commit.assert_not_called()
    conn.rollback.assert_called_once()


def test_reset_password_rejects_short_password_before_claiming_token():
    conn, cur = _db((12, 27))

    with pytest.raises(HTTPException) as error:
        main.reset_password(
            main.ResetPasswordRequest(token="synthetic-token", new_password="short"),
            (conn, cur),
        )

    assert error.value.status_code == 400
    assert cur.execute.call_count == 0
    conn.commit.assert_not_called()


def test_signup_and_login_still_hash_passwords_and_issue_access_tokens(monkeypatch):
    signup_conn, signup_cur = _db((51,))
    signup_response = main.signup(
        main.SignupRequest(
            name="Test User",
            email="person@example.com",
            password="old-password-123",
        ),
        (signup_conn, signup_cur),
    )

    assert signup_response == {"status": "success", "user_id": 51}
    signup_sql, signup_params = signup_cur.execute.call_args.args
    assert "INSERT INTO users" in signup_sql
    assert bcrypt.checkpw(b"old-password-123", signup_params[2].encode("utf-8"))
    signup_conn.commit.assert_called_once()

    login_conn = MagicMock()
    login_cur = MagicMock()
    login_cur.fetchone.return_value = (
        51,
        "Test User",
        signup_params[2],
    )
    token_conn = MagicMock()
    token_conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (0,)
    monkeypatch.setattr(main, "get_db_connection", lambda: token_conn)

    login_response = main.login(
        main.LoginRequest(email="person@example.com", password="old-password-123"),
        (login_conn, login_cur),
    )

    assert login_response["status"] == "success"
    assert login_response["user_id"] == 51
    assert login_response["access_token"]


def test_dashboard_forgot_password_form_calls_api_with_email(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"message": GENERIC_MESSAGE},
        )

    monkeypatch.setattr(requests, "post", fake_post)
    app = AppTest.from_file(str(DASHBOARD)).run(timeout=20)
    app.text_input(key="forgot_password_email").set_value("person@example.com")
    app.button(key="FormSubmitter:forgot_password_form-Send reset link").click()
    app.run(timeout=20)

    assert len(calls) == 1
    assert calls[0][0].endswith("/forgot-password")
    assert calls[0][1]["json"] == {"email": "person@example.com"}
    assert any(GENERIC_MESSAGE in item.value for item in app.success)


def test_dashboard_reset_form_posts_password_and_clears_query_token(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"message": "Password has been reset successfully."},
        )

    monkeypatch.setattr(requests, "post", fake_post)
    app = AppTest.from_file(str(DASHBOARD)).run(timeout=20)
    app.query_params["reset_token"] = "synthetic-reset-token"
    app.run(timeout=20)
    app.text_input(key="reset_new_password").set_value("new-password-123")
    app.text_input(key="reset_confirm_password").set_value("new-password-123")
    app.button(key="FormSubmitter:reset_password_form-Reset password").click()
    app.run(timeout=20)

    assert len(calls) == 1
    assert calls[0][0].endswith("/reset-password")
    assert calls[0][1]["json"] == {
        "token": "synthetic-reset-token",
        "new_password": "new-password-123",
    }
    assert "reset_token" not in app.query_params
    assert any("Password reset successfully" in item.value for item in app.success)


def test_dashboard_reset_form_rejects_short_or_mismatched_passwords(monkeypatch):
    send = MagicMock()
    monkeypatch.setattr(requests, "post", send)
    app = AppTest.from_file(str(DASHBOARD)).run(timeout=20)
    app.query_params["reset_token"] = "synthetic-reset-token"
    app.run(timeout=20)
    app.text_input(key="reset_new_password").set_value("short")
    app.text_input(key="reset_confirm_password").set_value("short")
    app.button(key="FormSubmitter:reset_password_form-Reset password").click()
    app.run(timeout=20)
    assert any("at least 8 characters" in item.value for item in app.error)
    send.assert_not_called()

    app.text_input(key="reset_new_password").set_value("new-password-123")
    app.text_input(key="reset_confirm_password").set_value("different-password")
    app.button(key="FormSubmitter:reset_password_form-Reset password").click()
    app.run(timeout=20)
    assert any("do not match" in item.value for item in app.error)
    send.assert_not_called()
