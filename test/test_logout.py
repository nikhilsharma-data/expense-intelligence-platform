from pathlib import Path
from types import SimpleNamespace

import requests
from streamlit.testing.v1 import AppTest


DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard.py"
OFFLINE_WARNING = "server could not be reached to revoke the token"


def _logged_in_dashboard(monkeypatch, post):
    monkeypatch.setattr(
        requests,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(
            status_code=503,
            text="Unavailable",
            json=lambda: {},
        ),
    )
    monkeypatch.setattr(requests, "post", post)

    app = AppTest.from_file(str(DASHBOARD)).run(timeout=20)
    app.session_state["logged_in"] = True
    app.session_state["user_id"] = 73
    app.session_state["user_name"] = "Synthetic User"
    app.session_state["access_token"] = "synthetic-access-token"
    app.run(timeout=20)
    return app


def _logout_button(app):
    return next(button for button in app.button if button.label == "Logout")


def test_dashboard_logout_posts_bearer_token_then_clears_local_state(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200)

    app = _logged_in_dashboard(monkeypatch, fake_post)
    _logout_button(app).click()
    app.run(timeout=20)

    assert len(calls) == 1
    assert calls[0][0].endswith("/logout")
    assert calls[0][1]["headers"] == {
        "Authorization": "Bearer synthetic-access-token"
    }
    assert app.session_state["logged_in"] is False
    assert app.session_state["user_id"] is None
    assert app.session_state["access_token"] is None
    assert not app.warning


def test_dashboard_logout_clears_local_state_and_warns_if_backend_is_unavailable(
    monkeypatch,
):
    calls = []

    def unavailable_post(url, **kwargs):
        calls.append((url, kwargs))
        raise requests.ConnectionError("private connection detail")

    app = _logged_in_dashboard(monkeypatch, unavailable_post)
    _logout_button(app).click()
    app.run(timeout=20)

    assert len(calls) == 1
    assert calls[0][0].endswith("/logout")
    assert app.session_state["logged_in"] is False
    assert app.session_state["user_id"] is None
    assert app.session_state["access_token"] is None
    assert any(OFFLINE_WARNING in warning.value for warning in app.warning)
    assert not any("private connection detail" in warning.value for warning in app.warning)
