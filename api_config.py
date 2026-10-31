"""Safe selection of the dashboard's FastAPI base URL."""

LOCAL_API_BASE_URL = "http://127.0.0.1:8000"


def resolve_expense_api_base_url(
    environment_url=None,
    secret_url=None,
    deployment_environment="development",
):
    """Prefer explicit deployment config; never silently fall back to hosted API."""
    for configured_url in (environment_url, secret_url):
        if isinstance(configured_url, str) and configured_url.strip():
            return configured_url.strip().rstrip("/")

    environment = str(deployment_environment or "development").strip().lower()
    if environment in {"prod", "production"}:
        raise RuntimeError(
            "EXPENSE_API_BASE_URL must be configured through the deployment "
            "environment or Streamlit secrets in production."
        )

    return LOCAL_API_BASE_URL
