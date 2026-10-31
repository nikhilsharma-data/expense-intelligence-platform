import pytest

from api_config import LOCAL_API_BASE_URL, resolve_expense_api_base_url


def test_local_dashboard_uses_loopback_api_default():
    assert resolve_expense_api_base_url() == "http://127.0.0.1:8000"
    assert LOCAL_API_BASE_URL == "http://127.0.0.1:8000"


def test_explicit_environment_url_precedes_streamlit_secret():
    assert resolve_expense_api_base_url(
        environment_url="https://api.example.test/",
        secret_url="https://secret-api.example.test",
    ) == "https://api.example.test"


def test_streamlit_secret_configures_production_api_url():
    assert resolve_expense_api_base_url(
        secret_url="https://api.example.test/",
        deployment_environment="production",
    ) == "https://api.example.test"


def test_missing_production_api_configuration_fails_closed():
    with pytest.raises(RuntimeError, match="must be configured"):
        resolve_expense_api_base_url(deployment_environment="production")


def test_missing_development_configuration_never_uses_hosted_api():
    resolved = resolve_expense_api_base_url(deployment_environment="development")

    assert resolved == LOCAL_API_BASE_URL
    assert "onrender.com" not in resolved
