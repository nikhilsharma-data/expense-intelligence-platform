import os
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from api_config import resolve_expense_api_base_url

# ---------------------------------------------------
# PAGE CONFIG (MUST BE FIRST)
# ---------------------------------------------------
st.set_page_config(
    page_title="Expense Intelligence Platform",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

try:
    _secret_api_url = st.secrets.get("EXPENSE_API_BASE_URL", None)
    _secret_deployment_env = st.secrets.get("EXPENSE_DEPLOYMENT_ENV", None)
except Exception:
    _secret_api_url = None
    _secret_deployment_env = None

_deployment_environment = os.getenv("EXPENSE_DEPLOYMENT_ENV")
if not (_deployment_environment and _deployment_environment.strip()):
    _deployment_environment = _secret_deployment_env or "development"

BASE_URL = resolve_expense_api_base_url(
    environment_url=os.getenv("EXPENSE_API_BASE_URL"),
    secret_url=_secret_api_url,
    deployment_environment=_deployment_environment,
)
REQUEST_TIMEOUT = 30

# ---------------------------------------------------
# PROFESSIONAL CUSTOM CSS
# ---------------------------------------------------
st.markdown(
    """
<style>
    html, body, [class*="css"] {
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    header[data-testid="stHeader"] {
        background: transparent;
    }
    footer {
        display: none;
    }

    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0E1117 0%, #1A1C23 100%);
        border-right: 1px solid #2A2D36;
    }
    [data-testid="stSidebar"] * {
        color: #E0E0E0;
    }
    .sidebar-user {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 12px 16px;
        background: rgba(255,255,255,0.05);
        border-radius: 12px;
        margin-bottom: 20px;
    }
    .sidebar-user .avatar {
        width: 42px;
        height: 42px;
        background: linear-gradient(135deg, #4F8BF9, #6C63FF);
        border-radius: 12px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 700;
        font-size: 18px;
        color: white;
    }

    .top-bar {
        background: linear-gradient(90deg, #0E1117 0%, #1A1C23 100%);
        border-bottom: 1px solid #2A2D36;
        padding: 16px 32px;
        margin-bottom: 24px;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    .top-bar h2 {
        margin: 0;
        font-weight: 600;
        background: linear-gradient(135deg, #4F8BF9, #6C63FF);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }

    .stButton button {
        border-radius: 12px;
        border: none;
        background: linear-gradient(135deg, #4F8BF9, #6C63FF);
        color: white;
        font-weight: 600;
        padding: 10px 24px;
        transition: all 0.3s ease;
        box-shadow: 0 4px 12px rgba(79, 139, 249, 0.3);
    }
    .stButton button:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 20px rgba(79, 139, 249, 0.5);
    }

    .metric-card {
        background: linear-gradient(135deg, #1E1E24, #2A2D36);
        border-radius: 16px;
        padding: 24px 20px;
        box-shadow: 0 4px 16px rgba(0,0,0,0.25);
        transition: transform 0.2s;
        border: 1px solid #2A2D36;
    }
    .metric-card:hover {
        transform: translateY(-4px);
    }
    .metric-icon {
        font-size: 28px;
        margin-bottom: 12px;
    }
    .metric-label {
        font-size: 14px;
        color: #A0A4B0;
        margin-bottom: 6px;
    }
    .metric-value {
        font-size: 28px;
        font-weight: 700;
        color: white;
    }
    .metric-delta {
        font-size: 13px;
        color: #4CAF50;
    }

    [data-testid="stDataFrame"] {
        border-radius: 12px !important;
        overflow: hidden;
        border: 1px solid #2A2D36;
    }

    [data-testid="stFileUploader"] {
        border: 2px dashed #4F8BF9 !important;
        border-radius: 16px !important;
        background: rgba(79,139,249,0.05);
        transition: all 0.3s;
    }
    [data-testid="stFileUploader"]:hover {
        background: rgba(79,139,249,0.1);
        border-color: #6C63FF !important;
    }

    .danger-zone {
        border: 2px solid #FF4B4B;
        border-radius: 16px;
        padding: 24px;
        background: rgba(255,75,75,0.05);
        margin-top: 24px;
    }

    .footer {
        text-align: center;
        color: #666;
        margin-top: 48px;
        padding: 24px;
        border-top: 1px solid #2A2D36;
    }
</style>
""",
    unsafe_allow_html=True,
)

st.markdown(
    """
<style>
    :root { color-scheme: dark; }
    [data-testid="stAppViewContainer"] { background: #0b1120; color: #e5edf8; }
    [data-testid="stSidebar"] { background: #101a2b; border-right: 1px solid #25334a; }
    [data-testid="stSidebar"] * { color: #d7e2f0; }
    .main .block-container { max-width: 1480px; padding-top: 2rem; padding-bottom: 3rem; }
    h1, h2, h3 { color: #f3f7fc; letter-spacing: -0.025em; }
    [data-testid="stMetric"] { background: #141f32; border: 1px solid #263650; border-radius: 14px; padding: 16px 18px; }
    [data-testid="stMetricLabel"] { color: #9fb0c7; }
    [data-testid="stMetricValue"] { color: #f5f8fd; font-weight: 650; }
    [data-testid="stDataFrame"] { border: 1px solid #263650; border-radius: 12px; overflow: hidden; }
    [data-testid="stRadio"] label { border-radius: 8px; }
    .stButton button { background: #3478f6; border: 1px solid #4a89ff; border-radius: 9px; box-shadow: none; color: white; font-weight: 600; transition: background-color 120ms ease; }
    .stButton button:hover { background: #2867d8; border-color: #74a4ff; box-shadow: none; transform: none; }
    .stButton button:focus { box-shadow: 0 0 0 2px #0b1120, 0 0 0 4px #73a2ff; }
    [data-testid="stFileUploader"] { border: 1px dashed #4a6c9d; border-radius: 12px; background: #111d30; }
    [data-testid="stAlert"] { border-radius: 10px; }
    .brand-mark { color: #8db7ff; font-size: 13px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; }
    .page-caption { color: #9fb0c7; margin-top: -.6rem; }
    .panel-note { background: #141f32; border: 1px solid #263650; border-radius: 12px; padding: 16px; }
</style>
""",
    unsafe_allow_html=True,
)


# ---------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------
def safe_number(value):
    try:
        return float(value or 0)
    except Exception:
        return 0


def response_detail(response, fallback):
    try:
        return response.json().get("detail", fallback)
    except Exception:
        return fallback


def auth_headers():
    token = st.session_state.get("access_token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def safe_get(endpoint, default, params=None):
    try:
        response = requests.get(
            f"{BASE_URL}/{endpoint}",
            params=params,
            headers=auth_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 200:
            return response.json()
        if response.status_code == 401:
            handle_unauthorized(response)

        st.error(f"Error loading {endpoint}: {response_detail(response, response.text)}")
        return default
    except Exception as exc:
        st.error(f"Connection error: {exc}")
        return default


def post_json(endpoint, payload):
    return requests.post(
        f"{BASE_URL}/{endpoint}",
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )


def reset_session():
    st.session_state.logged_in = False
    st.session_state.user_id = None
    st.session_state.user_name = None
    st.session_state.access_token = None
    st.session_state.processed_upload_key = None
    st.session_state.upload_notice = None
    st.session_state.action_notice = None
    st.session_state.logout_warning = None
    st.session_state.password_reset_token = None
    st.session_state.current_role = None
    st.session_state.role_access_token = None
    st.session_state.current_user_profile = None
    st.session_state.filtered_export_csv = None
    st.session_state.filtered_export_filter_signature = None


def handle_unauthorized(response):
    if response.status_code != 401:
        return False
    reset_session()
    st.session_state.logout_warning = "Your session is no longer valid. Please sign in again."
    st.rerun()
    return True


def clear_dashboard_widget_state():
    for widget_key in (
        "statement_upload",
        "confirm_replace_import",
        "transaction_search",
        "transaction_category",
        "transaction_date_from",
        "transaction_date_to",
        "transaction_page",
        "confirm_txn",
        "confirm_acct",
    ):
        if widget_key in st.session_state:
            del st.session_state[widget_key]


# ---------------------------------------------------
# SESSION STATE
# ---------------------------------------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "user_name" not in st.session_state:
    st.session_state.user_name = None
if "access_token" not in st.session_state:
    st.session_state.access_token = None
if "processed_upload_key" not in st.session_state:
    st.session_state.processed_upload_key = None
if "upload_notice" not in st.session_state:
    st.session_state.upload_notice = None
if "action_notice" not in st.session_state:
    st.session_state.action_notice = None
if "logout_warning" not in st.session_state:
    st.session_state.logout_warning = None
if "password_reset_token" not in st.session_state:
    st.session_state.password_reset_token = None
if "current_role" not in st.session_state:
    st.session_state.current_role = None
if "role_access_token" not in st.session_state:
    st.session_state.role_access_token = None
if "current_user_profile" not in st.session_state:
    st.session_state.current_user_profile = None
if st.session_state.pop("_clear_dashboard_widget_state", False):
    clear_dashboard_widget_state()

# ---------------------------------------------------
# PASSWORD RESET LINK
# ---------------------------------------------------
reset_token_from_url = st.query_params.get("reset_token")
if reset_token_from_url:
    st.session_state.password_reset_token = reset_token_from_url
    del st.query_params["reset_token"]

reset_token = st.session_state.password_reset_token
if reset_token:
    st.title("Reset your password")
    with st.form("reset_password_form"):
        new_password = st.text_input("New password", type="password", key="reset_new_password")
        confirm_password = st.text_input("Confirm new password", type="password", key="reset_confirm_password")
        submitted = st.form_submit_button("Reset password", type="primary")

    if submitted:
        if len(new_password) < 8:
            st.error("Password must be at least 8 characters long.")
        elif new_password != confirm_password:
            st.error("The passwords do not match.")
        else:
            try:
                response = post_json(
                    "reset-password",
                    {"token": reset_token, "new_password": new_password},
                )
                if response.status_code == 200:
                    st.session_state.password_reset_token = None
                    st.success("Password reset successfully. You can now sign in.")
                else:
                    st.error(response_detail(response, "Password reset failed."))
            except Exception:
                st.error("Could not reach the password reset service. Please try again.")
    st.stop()

# ---------------------------------------------------
# LOGIN / SIGNUP
# ---------------------------------------------------
if not st.session_state.logged_in:
    st.markdown(
        """
    <style>
        .stApp {
            background: linear-gradient(135deg, #0E1117 0%, #1A1C23 100%);
        }
        .login-title {
            font-size: 32px;
            font-weight: 700;
            background: linear-gradient(135deg, #4F8BF9, #6C63FF);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-align: center;
            margin-bottom: 32px;
        }
    </style>
    """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns([1, 1.5, 1])
    with col2:
        if st.session_state.logout_warning:
            st.warning(st.session_state.logout_warning)
            st.session_state.logout_warning = None

        st.markdown(
            '<div class="login-title">Expense Intelligence</div>',
            unsafe_allow_html=True,
        )

        auth_tab1, auth_tab2 = st.tabs(["Login", "Signup"])

        with auth_tab1:
            email = st.text_input(
                "Email",
                placeholder="you@example.com",
                key="login_email",
            )
            password = st.text_input(
                "Password",
                type="password",
                placeholder="Enter your password",
                key="login_password",
            )
            if st.button("Sign In", key="login_btn", use_container_width=True):
                if not email.strip() or not password:
                    st.error("Enter both email and password.")
                    st.stop()

                response = post_json(
                    "login",
                    {"email": email.strip(), "password": password},
                )
                if response.status_code == 200:
                    data = response.json()
                    st.session_state.logged_in = True
                    st.session_state.user_id = data["user_id"]
                    st.session_state.user_name = data["name"]
                    st.session_state.access_token = data["access_token"]
                    st.session_state.current_role = None
                    st.session_state.role_access_token = None
                    st.session_state.current_user_profile = None
                    st.session_state.logout_warning = None
                    clear_dashboard_widget_state()
                    st.success("Login successful!")
                    st.rerun()
                else:
                    st.error(response_detail(response, "Login failed"))

            with st.expander("Forgot your password?"):
                with st.form("forgot_password_form"):
                    reset_email = st.text_input(
                        "Account email",
                        placeholder="you@example.com",
                        key="forgot_password_email",
                    )
                    send_reset = st.form_submit_button("Send reset link")
                if send_reset:
                    if not reset_email.strip():
                        st.error("Enter your account email.")
                    else:
                        try:
                            response = post_json(
                                "forgot-password",
                                {"email": reset_email.strip()},
                            )
                            if response.status_code == 200:
                                st.success(response.json().get("message", "If an account exists, a reset link will be sent."))
                            else:
                                st.error(response_detail(response, "Could not request a reset link."))
                        except Exception:
                            st.error("Could not reach the password reset service. Please try again.")

        with auth_tab2:
            name = st.text_input(
                "Full Name",
                placeholder="John Doe",
                key="signup_name",
            )
            signup_email = st.text_input(
                "Email",
                placeholder="you@example.com",
                key="signup_email",
            )
            signup_password = st.text_input(
                "Password",
                type="password",
                placeholder="Min. 6 characters",
                key="signup_password",
            )
            if st.button("Create Account", key="signup_btn", use_container_width=True):
                if not name.strip() or not signup_email.strip() or len(signup_password) < 6:
                    st.error("Enter a name, valid email, and password with at least 6 characters.")
                    st.stop()

                response = post_json(
                    "signup",
                    {
                        "name": name.strip(),
                        "email": signup_email.strip(),
                        "password": signup_password,
                    },
                )
                if response.status_code == 200:
                    st.success("Account created! Please sign in.")
                else:
                    st.error(response_detail(response, "Signup failed"))
    st.stop()

# Resolve authorization state from the API whenever the access token changes.
if st.session_state.role_access_token != st.session_state.access_token:
    try:
        profile_response = requests.get(
            f"{BASE_URL}/me",
            headers=auth_headers(),
            timeout=REQUEST_TIMEOUT,
        )
    except Exception:
        st.error("Could not verify your account profile. Please retry when the API is available.")
        st.stop()

    if profile_response.status_code == 401:
        reset_session()
        st.session_state.logout_warning = "Your session is no longer valid. Please sign in again."
        st.rerun()
    if profile_response.status_code != 200:
        st.error(response_detail(profile_response, "Your account profile could not be verified."))
        st.stop()

    profile = profile_response.json()
    if profile.get("role") not in {"user", "admin"} or profile.get("is_active") is not True:
        reset_session()
        st.session_state.logout_warning = "Your account profile is not active. Please sign in again."
        st.rerun()
    st.session_state.current_user_profile = profile
    st.session_state.current_role = profile["role"]
    st.session_state.role_access_token = st.session_state.access_token
    st.session_state.user_name = profile.get("name") or st.session_state.user_name

# ---------------------------------------------------
# ---------------------------------------------------
# PRIMARY NAVIGATION AND DATA
# ---------------------------------------------------
with st.sidebar:
    st.markdown("<div class='brand-mark'>Expense Intelligence</div>", unsafe_allow_html=True)
    st.caption("Personal finance workspace")
    st.markdown("---")
    st.markdown(f"**{st.session_state.user_name or 'Account'}**")
    st.caption(f"{st.session_state.current_role.title()} account · imported amount units")
    navigation = ["Overview", "My Accounts", "Transactions", "Analytics", "Intelligence", "Forecast", "Import Statements", "Settings", "Research"]
    if st.session_state.current_role == "admin":
        navigation.append("Admin Portal")
    if st.session_state.get("workspace_page") == "Admin Portal" and st.session_state.current_role != "admin":
        del st.session_state["workspace_page"]
    page = st.radio(
        "Workspace",
        navigation,
        key="workspace_page",
        label_visibility="collapsed",
    )
    st.markdown("---")
    period = st.selectbox("Overview period", ["all", "30d", "90d", "ytd"], key="date_filter")
    if st.button("Logout", key="_logout_button", use_container_width=True):
        warning = None
        try:
            response = requests.post(f"{BASE_URL}/logout", headers=auth_headers(), timeout=REQUEST_TIMEOUT)
            if response.status_code != 200:
                warning = "You were logged out on this device, but server-side session revocation could not be confirmed."
        except Exception:
            warning = "You were logged out on this device, but the server could not be reached to revoke the token."
        reset_session()
        clear_dashboard_widget_state()
        st.session_state.logout_warning = warning
        st.rerun()

needs_overview = page in {"Overview", "Transactions", "Analytics", "Intelligence", "Forecast", "Research"}
overview = {}
if needs_overview:
    with st.spinner("Loading your financial overview..."):
        overview = safe_get("dashboard-overview", {}, params={"range": period})

summary = overview.get("summary", {})
categories = overview.get("categories", [])
trend = overview.get("trend", [])
insights = overview.get("insights", {})
recent_transactions = overview.get("transactions", [])
forecast = overview.get("forecast", {})
anomalies = overview.get("anomalies", {})
research_evaluation = overview.get("research_evaluation", {})
transaction_count = int(overview.get("transactions_total", 0) or 0)
category_options = overview.get("available_categories", [])
api_available = bool(overview)

if st.session_state.action_notice:
    st.success(st.session_state.action_notice)
    st.session_state.action_notice = None

st.markdown(f"<div class='brand-mark'>{page}</div>", unsafe_allow_html=True)
st.title(page)
st.caption("Your statement data is shown in imported amount units; currency is unknown unless stated in your source statement.")


def format_amount(value):
    return f"{safe_number(value):,.2f} units"


def show_cashflow_chart(rows):
    if not rows:
        st.info("Monthly cash-flow history is not available for this period yet.")
        return
    frame = pd.DataFrame(rows)
    for column in ("income", "expenses"):
        frame[column] = pd.to_numeric(frame.get(column, 0), errors="coerce").fillna(0)
    fig = px.bar(
        frame, x="month", y=["income", "expenses"], barmode="group",
        title="Monthly income and expenses",
        labels={"value": "Imported amount units", "month": "Month", "variable": "Series"},
        color_discrete_map={"income": "#25b987", "expenses": "#4f8ff7"},
    )
    fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", legend_title_text="Historical actuals")
    st.plotly_chart(fig, use_container_width=True)


def show_category_chart(rows):
    if not rows:
        st.info("No categorized expenses are available for this period.")
        return
    frame = pd.DataFrame(rows)
    frame["total"] = pd.to_numeric(frame["total"], errors="coerce").fillna(0).abs()
    left, right = st.columns([1.15, 1])
    with left:
        fig = px.bar(
            frame.sort_values("total"), x="total", y="category", orientation="h",
            title="Spending by category", labels={"total": "Imported amount units", "category": ""},
            color_discrete_sequence=["#548df5"],
        )
        fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)
    with right:
        fig = px.pie(frame, values="total", names="category", hole=.62, title="Expense distribution", color_discrete_sequence=px.colors.qualitative.Safe)
        fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)


def show_forecast():
    st.subheader("Monthly expense forecast")
    if not forecast:
        st.info("Forecast details are unavailable because the backend did not return a result.")
        return
    st.caption(
        f"Horizon: {forecast.get('horizon_months', '—')} month(s) · "
        f"Historical months used: {forecast.get('history_months', 0)} · "
        f"Transactions used: {forecast.get('history_transactions', 0)}. "
        f"{forecast.get('currency', 'Amounts are shown in imported units; currency is unknown.')}"
    )
    actuals = forecast.get("historical_actuals", [])
    if actuals:
        frame = pd.DataFrame(actuals)
        frame["series"] = "Historical actuals"
        fig = px.line(frame, x="month", y="total", color="series", markers=True, title="Historical actuals and future estimate", labels={"total": "Expense (imported amount units)", "month": "Month"}, color_discrete_sequence=["#4f8ff7"])
        if forecast.get("available"):
            fig.add_scatter(x=[forecast.get("forecast_month")], y=[forecast.get("forecast_amount")], mode="markers", name="Future forecast", marker={"size": 13, "symbol": "diamond", "color": "#a78bfa"})
        fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)
    if forecast.get("available"):
        st.metric(f"Forecast · {forecast.get('forecast_month')} (imported units)", format_amount(forecast.get("forecast_amount")))
        st.write(forecast.get("method", ""))
        st.caption(forecast.get("limitations", ""))
    else:
        st.info(forecast.get("reason") or "There is not enough historical data to produce a reliable forecast.")
    evaluation = forecast.get("evaluation", {})
    st.markdown("#### Chronological holdout evaluation")
    if evaluation.get("available") and all(evaluation.get(key) is not None for key in ("mae", "rmse", "baseline_mae", "baseline_rmse")):
        st.caption(f"{evaluation.get('periods')} evaluated periods · errors are in imported amount units.")
        st.dataframe(pd.DataFrame([
            {"Method": "Recent-month mean", "MAE": evaluation["mae"], "RMSE": evaluation["rmse"]},
            {"Method": "Last-month baseline", "MAE": evaluation["baseline_mae"], "RMSE": evaluation["baseline_rmse"]},
        ]), use_container_width=True, hide_index=True)
    else:
        st.info(evaluation.get("reason") or "Evaluation metrics are not available because there is insufficient holdout history.")


if page == "Overview":
    if not api_available:
        st.error("The financial overview could not be loaded. Check that the configured API is available and try again.")
    elif transaction_count == 0:
        st.info("No transactions yet. Import a CSV or PDF statement to build your overview.")
    else:
        income = safe_number(summary.get("total_income", 0))
        expenses = abs(safe_number(summary.get("total_expense", 0)))
        net_flow = safe_number(summary.get("total", 0))
        savings_percent = safe_number(summary.get("savings_percent", 0))
        kpis = st.columns(4)
        kpis[0].metric("Total income", format_amount(income))
        kpis[1].metric("Total expenses", format_amount(expenses))
        kpis[2].metric("Net transaction flow", format_amount(net_flow), help="Net sum of imported transactions; this is not a bank balance.")
        kpis[3].metric("Savings percentage", f"{savings_percent:.1f}%")
        st.caption(f"{transaction_count:,} imported transactions · currency not identified")
        show_cashflow_chart(trend)
        show_category_chart(categories)
        st.subheader("Recent transactions")
        if recent_transactions:
            recent = pd.DataFrame(recent_transactions)
            recent["amount"] = pd.to_numeric(recent["amount"], errors="coerce")
            recent = recent.rename(columns={"date": "Date", "description": "Description", "amount": "Amount (imported units)", "category": "Category"})
            st.dataframe(recent, use_container_width=True, hide_index=True)
        else:
            st.info("No recent transaction rows are available.")

elif page == "My Accounts":
    st.markdown("### Connected accounts")
    st.info("Bank-account connections are not supported yet. Current data comes from imported statements and is not grouped by bank account.")

elif page == "Transactions":
    st.caption(f"{transaction_count:,} transactions in the selected overview period. Results load in pages to keep the dashboard responsive.")
    filter_col1, filter_col2 = st.columns([2, 1])
    search = filter_col1.text_input("Search description", key="transaction_search")
    selected_category = filter_col2.selectbox("Category", ["All categories"] + list(category_options), key="transaction_category")
    date_cols = st.columns(2)
    date_from = date_cols[0].date_input("From date", value=None, key="transaction_date_from")
    date_to = date_cols[1].date_input("To date", value=None, key="transaction_date_to")
    filter_signature = (period, search, selected_category, date_from, date_to)
    if st.session_state.get("filtered_export_filter_signature") != filter_signature:
        st.session_state.filtered_export_csv = None
        st.session_state.filtered_export_filter_signature = filter_signature
    page_size = 100
    page_number = st.number_input("Page", min_value=1, value=1, step=1, key="transaction_page")
    query = {"limit": page_size, "offset": (int(page_number) - 1) * page_size}
    if period == "30d":
        query["date_from"] = (date.today() - timedelta(days=30)).isoformat()
    elif period == "90d":
        query["date_from"] = (date.today() - timedelta(days=90)).isoformat()
    elif period == "ytd":
        query["date_from"] = date(date.today().year, 1, 1).isoformat()
    if search.strip(): query["search"] = search.strip()
    if selected_category != "All categories": query["category"] = selected_category
    if date_from:
        requested_start = date_from.isoformat()
        query["date_from"] = max(query.get("date_from", requested_start), requested_start)
    if date_to: query["date_to"] = date_to.isoformat()
    if date_from and date_to and date_from > date_to:
        st.error("The start date must be on or before the end date.")
    else:
        try:
            response = requests.get(f"{BASE_URL}/transactions", params=query, headers=auth_headers(), timeout=REQUEST_TIMEOUT)
            if response.status_code == 200:
                rows = response.json()
                if rows:
                    frame = pd.DataFrame(rows).rename(columns={"date": "Date", "description": "Description", "amount": "Amount (imported units)", "category": "Category"})
                    st.dataframe(frame, use_container_width=True, hide_index=True)
                    st.download_button("Export current page as CSV", frame.to_csv(index=False).encode("utf-8"), "transactions_page.csv", "text/csv")
                    if response.headers.get("X-Has-More") == "true":
                        st.caption("More results are available on the next page.")
                else:
                    st.info("No transactions match these filters.")
            elif response.status_code == 401:
                handle_unauthorized(response)
            else:
                st.error(response_detail(response, "Transactions could not be loaded."))
        except Exception:
            st.error("Could not reach the transaction service. Please try again.")
    if st.button("Prepare CSV export of all filtered results", key="prepare_all_export"):
        export_query = {key: value for key, value in query.items() if key not in {"limit", "offset"}}
        try:
            response = requests.get(f"{BASE_URL}/transactions", params=export_query, headers=auth_headers(), timeout=REQUEST_TIMEOUT)
            if response.status_code == 200:
                export_rows = response.json()
                export_frame = pd.DataFrame(export_rows, columns=["date", "description", "amount", "category"])
                st.session_state.filtered_export_csv = export_frame.to_csv(index=False).encode("utf-8")
                st.success(f"Prepared {len(export_frame):,} filtered rows for export.")
            elif response.status_code == 401:
                handle_unauthorized(response)
            else:
                st.error(response_detail(response, "CSV export could not be prepared."))
        except Exception:
            st.error("Could not reach the transaction service. Please try again.")
    if st.session_state.get("filtered_export_csv") is not None:
        st.download_button("Download all filtered transactions", st.session_state.filtered_export_csv, "transactions_filtered.csv", "text/csv", key="download_all_transactions")

elif page == "Analytics":
    if not api_available:
        st.error("Analytics could not be loaded because the API did not return an overview.")
    elif transaction_count == 0:
        st.info("Import statement data to see spending summaries and trends.")
    else:
        st.subheader("Cash-flow patterns")
        show_cashflow_chart(trend)
        show_category_chart(categories)
        st.subheader("Spending summaries")
        for insight in insights.get("insights", []):
            st.write(f"• {insight}")
        if not insights.get("insights"):
            st.info("There are no summaries for this period yet.")

elif page == "Intelligence":
    if not api_available:
        st.error("Intelligence results could not be loaded from the API.")
    elif transaction_count == 0:
        st.info("Import transactions to review possible unusual-spending patterns.")
    else:
        st.subheader("Possible unusual spending")
        st.caption(anomalies.get("label", "Flags are indicators for review, not confirmed fraud."))
        flags = anomalies.get("flags", [])
        if flags:
            st.dataframe(pd.DataFrame([{"Date": f.get("date"), "Description": f.get("description"), "Category": f.get("category"), "Amount (imported units)": f.get("amount"), "Reason": f.get("reason")} for f in flags]), use_container_width=True, hide_index=True)
        else:
            st.info("No transactions were flagged. Sparse category history may limit what can be assessed.")

elif page == "Forecast":
    if not api_available:
        st.error("Forecasting could not be loaded because the API did not return an overview.")
    else:
        show_forecast()

elif page == "Import Statements":
    st.subheader("Import a statement")
    max_mb = max(1, int(os.getenv("MAX_UPLOAD_SIZE_MB", "10")))
    max_rows = max(1, int(os.getenv("MAX_UPLOAD_ROWS", "5000")))
    st.caption(f"CSV and PDF · configured API limits: up to {max_mb} MB and {max_rows:,} rows/transactions. Successful imports replace the full transaction history for this account.")
    st.warning("Important: importing a valid statement deletes and replaces your existing transactions. Multi-account and append imports are not supported.")
    uploaded_file = st.file_uploader("Choose a CSV or PDF statement", type=["csv", "pdf"], max_upload_size=max_mb, key="statement_upload")
    if uploaded_file:
        file_bytes = uploaded_file.getvalue()
        ext = uploaded_file.name.rsplit(".", 1)[-1].lower()
        st.caption(f"Selected {uploaded_file.name} · {len(file_bytes) / (1024 * 1024):.2f} MB")
        if ext == "csv":
            try:
                preview = pd.read_csv(uploaded_file, nrows=max_rows + 1)
                if len(preview) > max_rows:
                    st.error(f"The CSV exceeds the configured {max_rows:,}-row limit.")
                else:
                    st.dataframe(preview.head(8), use_container_width=True, hide_index=True)
                    st.caption(f"Preview: {len(preview):,} row(s) read. Server validation remains authoritative.")
            except Exception:
                st.error("The CSV could not be previewed. Check the file format and required columns.")
        confirmed_replace = st.checkbox("I understand this import replaces all existing transactions", key="confirm_replace_import")
        upload_key = (st.session_state.user_id, uploaded_file.name, len(file_bytes), hash(file_bytes))
        if st.session_state.processed_upload_key == upload_key:
            st.success(st.session_state.upload_notice or "This exact file has already been processed in this session.")
        elif st.button("Process and replace transaction history", key="process_statement", type="primary", disabled=not confirmed_replace) and confirmed_replace:
            files = {"file": (uploaded_file.name, file_bytes, "text/csv" if ext == "csv" else "application/pdf")}
            try:
                with st.spinner("Validating and importing statement..."):
                    response = requests.post(f"{BASE_URL}/upload", headers=auth_headers(), files=files, timeout=REQUEST_TIMEOUT)
                if response.status_code == 200:
                    result = response.json()
                    st.session_state.processed_upload_key = upload_key
                    accepted = result.get("rows_loaded", 0)
                    rejected = result.get("rows_skipped", 0)
                    st.session_state.upload_notice = f"{accepted:,} accepted; {rejected:,} skipped."
                    st.success(f"Import complete: {accepted:,} accepted; {rejected:,} skipped.")
                    st.rerun()
                elif response.status_code == 401:
                    handle_unauthorized(response)
                else:
                    st.error(response_detail(response, "The statement could not be imported. Existing transactions were not replaced unless the server confirmed success."))
            except Exception:
                st.error("The upload service could not be reached. Existing transactions were not replaced unless the server confirmed success.")
    st.download_button("Download CSV template", "Date,Description,Amount\n", "expense_template.csv", "text/csv")

elif page == "Settings":
    st.subheader("Account and data controls")
    st.info("Password changes are handled through the password-recovery email flow. Account identity and data remain private to your session.")
    left, right = st.columns(2)
    with left:
        st.markdown("#### Delete transactions")
        st.warning("This permanently removes all transactions for your account.")
        if st.checkbox("Confirm deletion of all my transactions", key="confirm_txn") and st.button("Delete all transactions", key="del_txn", type="primary"):
            try:
                response = requests.delete(f"{BASE_URL}/delete-transactions", headers=auth_headers(), timeout=REQUEST_TIMEOUT)
                if response.status_code == 200:
                    st.session_state.action_notice = "All transactions deleted."
                    st.session_state.upload_notice = None
                    st.rerun()
                elif response.status_code == 401: handle_unauthorized(response)
                else: st.error(response_detail(response, "Deletion failed."))
            except Exception: st.error("The transaction service could not be reached.")
    with right:
        st.markdown("#### Delete account")
        st.error("This permanently deletes your account and its data; this action cannot be undone.")
        if st.checkbox("Confirm permanent account deletion", key="confirm_acct") and st.button("Delete my account", key="del_acct", type="primary"):
            try:
                response = requests.delete(f"{BASE_URL}/delete-account", headers=auth_headers(), timeout=REQUEST_TIMEOUT)
                if response.status_code == 200:
                    reset_session()
                    st.session_state["_clear_dashboard_widget_state"] = True
                    st.success("Account deleted.")
                    st.rerun()
                elif response.status_code == 401: handle_unauthorized(response)
                else: st.error(response_detail(response, "Deletion failed."))
            except Exception: st.error("The account service could not be reached.")

elif page == "Research":
    st.caption("All scores on this page use synthetic examples only. They do not describe your transactions or establish real-world accuracy.")
    evaluation = research_evaluation.get("categorization", {})
    if evaluation.get("evaluation_size", 0) > 0:
        st.subheader("Categorization · synthetic holdout")
        st.caption(f"{evaluation.get('dataset_size')} examples · {evaluation.get('evaluation_label', '')} {evaluation.get('methodology', '')}")
        method_names = {"keyword_rules_baseline": "Keyword rules", "multinomial_naive_bayes": "Multinomial Naive Bayes"}
        rows = [{"Method": method_names.get(name, name), "Accuracy": metrics.get("accuracy"), "Macro precision": metrics.get("precision_macro"), "Macro recall": metrics.get("recall_macro"), "Macro F1": metrics.get("f1_macro"), "Examples": metrics.get("sample_count")} for name, metrics in evaluation.get("methods", {}).items()]
        if rows: st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        independent = evaluation.get("independent_synthetic_holdout", {})
        if independent.get("evaluation_size", 0):
            st.markdown("#### Independently authored synthetic challenge set")
            st.caption(f"{independent['evaluation_size']} manually labeled examples. {independent.get('methodology', '')}")
            st.dataframe(pd.DataFrame([{"Method": method_names.get(name, name), "Accuracy": m.get("accuracy"), "Macro precision": m.get("precision_macro"), "Macro recall": m.get("recall_macro"), "Macro F1": m.get("f1_macro"), "Examples": m.get("sample_count")} for name, m in independent.get("methods", {}).items()]), use_container_width=True, hide_index=True)
        with st.expander("Methodology and limits"):
            st.write(evaluation.get("limitations", ""))
    else:
        st.info("Synthetic categorization evaluation metrics are unavailable.")
    anomaly_evaluation = research_evaluation.get("anomaly_detection", {})
    if anomaly_evaluation.get("evaluation_size", 0) and anomaly_evaluation.get("f1") is not None:
        st.subheader("Unusual-spending detection · synthetic scenarios")
        st.caption(anomaly_evaluation.get("methodology", ""))
        metric_cols = st.columns(4)
        for col, label, key in zip(metric_cols, ("Precision", "Recall", "F1", "Accuracy"), ("precision", "recall", "f1", "accuracy")):
            col.metric(label, f"{anomaly_evaluation[key]:.1%}")
        st.caption(f"Confusion counts: {anomaly_evaluation.get('confusion_matrix')}. {anomaly_evaluation.get('limitations', '')}")
        with st.expander("Synthetic anomaly cases"):
            st.dataframe(pd.DataFrame(anomaly_evaluation.get("cases", [])), use_container_width=True, hide_index=True)
    else:
        st.info("Synthetic anomaly evaluation metrics are unavailable.")

elif page == "Admin Portal":
    st.caption("Read-only system administration. Every view is protected by server-side administrator authorization.")
    admin_view = st.radio("Admin Portal", ["Overview", "User Management"], horizontal=True, key="admin_portal_view")
    if admin_view == "Overview":
        admin_data = safe_get("admin/overview", {})
        if admin_data:
            st.caption("System totals only; no individual transaction details are exposed.")
            metrics = st.columns(4)
            metrics[0].metric("Users", f"{int(admin_data.get('total_users', 0)):,}")
            metrics[1].metric("Active users", f"{int(admin_data.get('active_users', 0)):,}")
            metrics[2].metric("Inactive users", f"{int(admin_data.get('inactive_users', 0)):,}")
            metrics[3].metric("Transactions", f"{int(admin_data.get('total_transactions', 0)):,}")
            st.caption(f"Administrator accounts: {int(admin_data.get('admin_users', 0)):,}")
        else:
            st.error("Administrator overview is unavailable; the API response was not successful.")
    else:
        user_search = st.text_input("Search users by name or email", key="admin_user_search")
        admin_limit = st.selectbox("Rows per page", [10, 25, 50, 100], index=1, key="admin_user_page_size")
        admin_page = st.number_input("Page", min_value=1, value=1, step=1, key="admin_user_page")
        users_payload = safe_get(
            "admin/users",
            {},
            params={"search": user_search, "limit": admin_limit, "offset": (int(admin_page) - 1) * admin_limit},
        )
        if users_payload and isinstance(users_payload.get("users"), list):
            user_rows = users_payload["users"]
            st.caption(f"{int(users_payload.get('total', 0)):,} matching account(s). User listing is read-only.")
            if user_rows:
                st.dataframe(
                    pd.DataFrame(user_rows).rename(columns={
                        "id": "ID", "name": "Name", "email": "Email",
                        "role": "Role", "is_active": "Active",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("No users match this search.")
        else:
            st.error("User management data is unavailable; the API response was not successful.")

st.markdown("---")
st.caption("Expense Intelligence · imported transaction data · currency not identified unless provided by the statement")
