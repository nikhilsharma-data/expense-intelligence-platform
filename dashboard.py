import os

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

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
except Exception:
    _secret_api_url = None

BASE_URL = (
    os.getenv("EXPENSE_API_BASE_URL")
    or _secret_api_url
    or "https://expense-intelligence-platform.onrender.com"
).rstrip("/")
REQUEST_TIMEOUT = 30

# ---------------------------------------------------
# PROFESSIONAL CUSTOM CSS
# ---------------------------------------------------
st.markdown(
    """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
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

# ---------------------------------------------------
# PASSWORD RESET LINK
# ---------------------------------------------------
reset_token = st.query_params.get("reset_token")
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
                    if "reset_token" in st.query_params:
                        del st.query_params["reset_token"]
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
                    st.session_state.logout_warning = None
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

# ---------------------------------------------------
# TOP NAVIGATION BAR
# ---------------------------------------------------
st.markdown(
    """
<div class="top-bar">
    <h2>Expense Intelligence</h2>
    <div style="color: #A0A4B0; font-size: 14px;">Smart spending insights</div>
</div>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------
# SIDEBAR
# ---------------------------------------------------
with st.sidebar:
    avatar = st.session_state.user_name[0].upper() if st.session_state.user_name else "U"
    st.markdown(
        f"""
    <div class="sidebar-user">
        <div class="avatar">{avatar}</div>
        <div>
            <div style="font-weight:600;">{st.session_state.user_name}</div>
            <div style="font-size:12px; color:#A0A4B0;">Personal account</div>
        </div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    st.markdown("**Period**")
    date_range = st.selectbox(
        "",
        ["all", "30d", "90d", "ytd"],
        index=0,
        key="date_filter",
        label_visibility="collapsed",
    )

    if st.button("Logout", use_container_width=True):
        logout_warning = None
        try:
            response = requests.post(
                f"{BASE_URL}/logout",
                headers=auth_headers(),
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code != 200:
                logout_warning = (
                    "You were logged out on this device, but server-side session "
                    "revocation could not be confirmed. Your token may remain valid."
                )
        except Exception:
            logout_warning = (
                "You were logged out on this device, but the server could not be "
                "reached to revoke the token. It may remain valid until it expires."
            )
        reset_session()
        st.session_state.logout_warning = logout_warning
        st.rerun()

# ---------------------------------------------------
# DATA FETCHING
# ---------------------------------------------------
with st.spinner("Crunching your numbers..."):
    summary = safe_get(
        "summary",
        {},
        params={"range": date_range},
    )
    categories = safe_get(
        "category-breakdown",
        [],
        params={"range": date_range},
    )
    trend = safe_get(
        "monthly-trend",
        [],
        params={"range": date_range},
    )
    insights = safe_get(
        "insights",
        {},
        params={"range": date_range},
    )
    transactions = safe_get(
        "transactions",
        [],
    )
    forecast = safe_get("forecast", {})
    anomalies = safe_get("anomalies", {})
    research_evaluation = safe_get("research-evaluation", {})

has_data = bool(
    summary
    and (
        safe_number(summary.get("total_income", 0)) > 0
        or safe_number(summary.get("total_expense", 0)) > 0
    )
)

if st.session_state.action_notice:
    st.success(st.session_state.action_notice)
    st.session_state.action_notice = None

# ---------------------------------------------------
# TABS
# ---------------------------------------------------
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["Dashboard", "Insights", "Upload", "Settings", "Research"]
)

# ==================== DASHBOARD TAB ====================
with tab1:
    if not has_data:
        st.info(
            "No data yet. Upload a bank statement in the **Upload** tab to unlock insights."
        )
    else:
        balance = safe_number(summary.get("total", 0))
        expense = abs(safe_number(summary.get("total_expense", 0)))
        income = safe_number(summary.get("total_income", 0))
        expense_ratio = (expense / income * 100) if income else 0

        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(
                f"""
            <div class="metric-card">
                <div class="metric-icon">Balance</div>
                <div class="metric-label">Total Balance</div>
                <div class="metric-value">{balance:,.0f} amount units</div>
            </div>
            """,
                unsafe_allow_html=True,
            )
        with col2:
            st.markdown(
                f"""
            <div class="metric-card">
                <div class="metric-icon">Outflow</div>
                <div class="metric-label">Expenses</div>
                <div class="metric-value">{expense:,.0f} amount units</div>
                <div class="metric-delta">{expense_ratio:.1f}% of income</div>
            </div>
            """,
                unsafe_allow_html=True,
            )
        with col3:
            st.markdown(
                f"""
            <div class="metric-card">
                <div class="metric-icon">Inflow</div>
                <div class="metric-label">Income</div>
                <div class="metric-value">{income:,.0f} amount units</div>
            </div>
            """,
                unsafe_allow_html=True,
            )

        st.markdown("<br>", unsafe_allow_html=True)

        st.markdown("### Category Breakdown")
        if categories:
            df = pd.DataFrame(categories)
            df["total"] = pd.to_numeric(df["total"], errors="coerce")
            df["category"] = df["category"].fillna("Others").astype(str)

            with st.sidebar:
                selected_category = st.selectbox(
                    "Filter by Category",
                    ["All"] + sorted(df["category"].unique()),
                    key="cat_filter",
                )

            if selected_category != "All":
                df = df[df["category"] == selected_category]

            expense_df = df.copy()
            expense_df["total"] = expense_df["total"].abs()
            expense_df = expense_df.sort_values("total", ascending=True)

            if not expense_df.empty:
                fig_bar = px.bar(
                    expense_df,
                    x="total",
                    y="category",
                    orientation="h",
                    title="Spending by Category",
                    labels={"total": "Amount (imported units)", "category": ""},
                    color="total",
                    color_continuous_scale="blues",
                )
                fig_bar.update_layout(
                    template="plotly_dark",
                    font_family="Inter",
                    title_font_size=16,
                )
                st.plotly_chart(fig_bar, use_container_width=True)

                st.markdown("#### Expense Distribution")
                pie_df = expense_df.nlargest(5, "total").copy()
                rest = expense_df.iloc[5:]["total"].sum() if len(expense_df) > 5 else 0
                if rest > 0:
                    pie_df.loc[len(pie_df)] = {"category": "Other", "total": rest}

                fig_donut = px.pie(
                    pie_df,
                    values="total",
                    names="category",
                    hole=0.5,
                    color_discrete_sequence=px.colors.sequential.Blues_r,
                )
                fig_donut.update_layout(template="plotly_dark", font_family="Inter")
                st.plotly_chart(fig_donut, use_container_width=True)

                csv = df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    "Download Report",
                    csv,
                    file_name="category_report.csv",
                    mime="text/csv",
                )
        else:
            st.warning("No category data available.")

        st.markdown("---")

        st.markdown("### Net Cashflow Trend")
        if trend:
            trend_df = pd.DataFrame(trend)
            trend_df["total"] = pd.to_numeric(trend_df["total"], errors="coerce")
            fig_trend = px.line(
                trend_df,
                x="month",
                y="total",
                title="Monthly balance movement",
                labels={"month": "Month", "total": "Net cashflow (imported units)"},
            )
            fig_trend.update_layout(template="plotly_dark", font_family="Inter")
            st.plotly_chart(fig_trend, use_container_width=True)
        else:
            st.info("Not enough data for trend analysis.")

# ==================== INSIGHTS TAB ====================
with tab2:
    st.markdown("### Smart Insights")
    if insights.get("insights"):
        for item in insights["insights"]:
            text = item.lower()
            if "warning" in text:
                st.warning(item)
            elif "saved" in text or "great" in text:
                st.success(item)
            else:
                st.info(item)
    else:
        st.info("Upload transactions to unlock AI-powered insights.")

    st.markdown("---")
    st.markdown("### Transaction History")
    if transactions:
        txn_df = pd.DataFrame(transactions)
        txn_df["amount"] = pd.to_numeric(txn_df["amount"], errors="coerce").fillna(0)
        txn_df["type"] = txn_df["amount"].apply(lambda x: "Credit" if x > 0 else "Debit")
        txn_df["display_amount"] = txn_df["amount"].apply(
            lambda x: f"+{x:,.0f} units" if x > 0 else f"-{abs(x):,.0f} units"
        )

        search = st.text_input("Search by description")
        if search:
            txn_df = txn_df[
                txn_df["description"].str.contains(search, case=False, na=False)
            ]

        display_df = txn_df[["date", "description", "type", "display_amount", "category"]]
        display_df.columns = ["Date", "Description", "Type", "Amount", "Category"]
        st.dataframe(display_df, use_container_width=True, hide_index=True)

        csv = txn_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Export Transactions",
            csv,
            file_name="transactions.csv",
            mime="text/csv",
        )
    else:
        st.info("No transactions yet.")

# ==================== UPLOAD TAB ====================
with tab3:
    st.markdown("### Upload Statement")
    st.markdown("Supports CSV and PDF bank statements. Maximum 5,000 rows recommended.")

    uploaded_file = st.file_uploader(
        "Drag and drop your file here",
        type=["csv", "pdf"],
    )
    if uploaded_file:
        file_type = uploaded_file.name.split(".")[-1].lower()
        file_bytes = uploaded_file.getvalue()
        upload_key = (
            st.session_state.user_id,
            uploaded_file.name,
            len(file_bytes),
        )

        if st.session_state.processed_upload_key == upload_key:
            if st.session_state.upload_notice:
                st.success(st.session_state.upload_notice)
            else:
                st.info("This file has already been processed.")
        else:
            if st.button("Process statement", key="process_statement", type="primary"):
                files = {
                    "file": (
                        uploaded_file.name,
                        file_bytes,
                        "text/csv" if file_type == "csv" else "application/pdf",
                    )
                }
                with st.spinner("Processing statement..."):
                    response = requests.post(
                        f"{BASE_URL}/upload",
                        headers=auth_headers(),
                        files=files,
                        timeout=REQUEST_TIMEOUT,
                    )
                    if response.status_code == 200:
                        data = response.json()
                        st.session_state.processed_upload_key = upload_key

                        if (
                            file_type == "pdf"
                            and "preview" in data
                            and not data["preview"].strip()
                        ):
                            st.session_state.upload_notice = (
                                "File processed, but no readable text was found. The PDF may be scanned."
                            )
                        else:
                            st.session_state.upload_notice = (
                                "Statement processed successfully!"
                            )

                        st.rerun()
                    else:
                        st.error(
                            response_detail(response, "Upload failed")
                        )

    with st.expander("Need a sample? Download the template"):
        sample_csv = """Date,Description,Amount
2026-01-01,Salary,60000
2026-01-02,Rent,-18000
2026-01-03,Swiggy,-450
"""
        st.download_button(
            "Download Sample CSV",
            sample_csv,
            file_name="sample_expense.csv",
            mime="text/csv",
        )

# ==================== SETTINGS TAB ====================
with tab4:
    st.markdown("### Account Settings")
    st.markdown("Manage your data and account.")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(
            """
        <div style="background: #1E1E24; border-radius:16px; padding:24px; border:1px solid #2A2D36;">
            <h4 style="margin-top:0;">Delete Transactions</h4>
            <p style="color:#A0A4B0;">Remove all uploaded statements and their data.</p>
        </div>
        """,
            unsafe_allow_html=True,
        )
        confirm_txn = st.checkbox(
            "I understand, delete all transactions",
            key="confirm_txn",
        )
        if confirm_txn:
            if st.button("Delete All Transactions", key="del_txn", type="primary"):
                with st.spinner("Deleting transactions..."):
                    response = requests.delete(
                        f"{BASE_URL}/delete-transactions",
                        headers=auth_headers(),
                        timeout=REQUEST_TIMEOUT,
                    )
                if response.status_code == 200:
                    st.session_state.action_notice = "All transactions deleted."
                    st.session_state.upload_notice = None
                    st.rerun()
                else:
                    st.error(response_detail(response, "Deletion failed"))

    with col2:
        st.markdown(
            """
        <div class="danger-zone">
            <h4 style="margin-top:0;">Delete Account</h4>
            <p style="color:#FF6B6B;">Permanently erase your account and all data. This cannot be undone.</p>
        </div>
        """,
            unsafe_allow_html=True,
        )
        confirm_acct = st.checkbox(
            "I understand, delete my account",
            key="confirm_acct",
        )
        if confirm_acct:
            if st.button("Delete My Account", key="del_acct", type="primary"):
                with st.spinner("Deleting account..."):
                    response = requests.delete(
                        f"{BASE_URL}/delete-account",
                        headers=auth_headers(),
                        timeout=REQUEST_TIMEOUT,
                    )
                if response.status_code == 200:
                    reset_session()
                    st.success("Account deleted. Goodbye!")
                    st.rerun()
                else:
                    st.error(response_detail(response, "Deletion failed"))

# ==================== RESEARCH TAB ====================
with tab5:
    st.markdown("### Research Evaluation")
    st.caption(
        "Categorization and anomaly scores below use fixed synthetic examples only. "
        "They are not estimates of performance on your transactions or real bank statements."
    )

    evaluation = research_evaluation.get("categorization")
    anomaly_evaluation = research_evaluation.get("anomaly_detection")
    categorization_metrics_valid = bool(
        evaluation
        and evaluation.get("evaluation_size", 0) > 0
        and all(
            evaluation.get("methods", {}).get(method, {}).get("accuracy") is not None
            for method in ("keyword_rules_baseline", "multinomial_naive_bayes")
        )
    )
    anomaly_metrics_valid = bool(
        anomaly_evaluation
        and anomaly_evaluation.get("evaluation_size", 0) > 0
        and anomaly_evaluation.get("f1") is not None
    )
    if categorization_metrics_valid:
        st.markdown("#### Rule-vocabulary coverage check")
        st.caption(
            f"{evaluation['dataset_size']} synthetic labeled descriptions: "
            f"{evaluation['training_size']} training and "
            f"{evaluation['evaluation_size']} held-out examples. "
            f"{evaluation['evaluation_label']} {evaluation['methodology']}"
        )
        method_names = {
            "keyword_rules_baseline": "Keyword rules",
            "multinomial_naive_bayes": "Multinomial Naive Bayes",
        }
        metric_rows = []
        for method, metrics in evaluation["methods"].items():
            metric_rows.append({
                "Method": method_names.get(method, method),
                "Accuracy": metrics.get("accuracy"),
                "Macro precision": metrics.get("precision_macro"),
                "Macro recall": metrics.get("recall_macro"),
                "Macro F1": metrics.get("f1_macro"),
                "Held-out examples": metrics.get("sample_count"),
            })
        st.dataframe(pd.DataFrame(metric_rows), use_container_width=True, hide_index=True)
        rule_f1 = evaluation["methods"]["keyword_rules_baseline"]["f1_macro"]
        nb_f1 = evaluation["methods"]["multinomial_naive_bayes"]["f1_macro"]
        if rule_f1 != nb_f1:
            higher_f1_method = "Keyword rules" if rule_f1 > nb_f1 else "Multinomial Naive Bayes"
            st.caption(f"On this synthetic holdout only, {higher_f1_method} has the higher macro F1.")
        else:
            st.caption("The methods have equal macro F1 on this synthetic holdout.")
        with st.expander("Categorization details and limitations"):
            st.write(evaluation["limitations"])
            st.write("Category distribution", evaluation["category_distribution"])
            selected_method = st.selectbox(
                "Confusion matrix",
                list(evaluation["methods"]),
                format_func=lambda method: method_names.get(method, method),
                key="research_confusion_method",
            )
            matrix = evaluation["methods"][selected_method]["confusion_matrix"]
            st.dataframe(pd.DataFrame(matrix).T, use_container_width=True)

        independent = evaluation.get("independent_synthetic_holdout", {})
        if independent.get("evaluation_size", 0) > 0:
            st.markdown("#### Independently authored synthetic challenge set")
            st.caption(
                f"{independent['evaluation_size']} manually labeled examples. "
                f"{independent['methodology']}"
            )
            independent_rows = []
            for method, metrics in independent["methods"].items():
                independent_rows.append({
                    "Method": method_names.get(method, method),
                    "Accuracy": metrics["accuracy"],
                    "Macro precision": metrics["precision_macro"],
                    "Macro recall": metrics["recall_macro"],
                    "Macro F1": metrics["f1_macro"],
                    "Held-out examples": metrics["sample_count"],
                })
            st.dataframe(pd.DataFrame(independent_rows), use_container_width=True, hide_index=True)
            with st.expander("Challenge set confusion matrix and limitations"):
                st.write(independent["limitations"])
                challenge_method = st.selectbox(
                    "Challenge set confusion matrix",
                    list(independent["methods"]),
                    format_func=lambda method: method_names.get(method, method),
                    key="research_independent_confusion_method",
                )
                challenge_matrix = independent["methods"][challenge_method]["confusion_matrix"]
                st.dataframe(pd.DataFrame(challenge_matrix).T, use_container_width=True)

    else:
        st.info("Categorization evaluation metrics are unavailable for this response.")

    if anomaly_metrics_valid:
        st.markdown("#### Unusual-spending detection")
        st.caption(anomaly_evaluation["methodology"])
        anomaly_cols = st.columns(4)
        for column, label, key in zip(
            anomaly_cols,
            ("Accuracy", "Precision", "Recall", "F1"),
            ("accuracy", "precision", "recall", "f1"),
        ):
            column.metric(label, f"{anomaly_evaluation[key]:.1%}")
        st.caption(
            f"{anomaly_evaluation['evaluation_size']} of "
            f"{anomaly_evaluation['labelled_target_count']} labeled targets were assessable; "
            f"{anomaly_evaluation['unscored_insufficient_history']} were withheld for sparse history. "
            f"Confusion counts: {anomaly_evaluation['confusion_matrix']}. "
            f"{anomaly_evaluation['limitations']}"
        )
        with st.expander("Anomaly evaluation cases and explanations"):
            st.dataframe(
                pd.DataFrame([
                    {
                        "Date": case["date"],
                        "Description": case["description"],
                        "Category": case["category"],
                        "Amount (imported units)": case["amount"],
                        "Expected": case["expected_anomaly"],
                        "Predicted": case["predicted_anomaly"],
                        "Assessment": case["assessment"],
                        "Prior same-category rows": case["historical_sample_size"],
                        "Reason": case["explanation"] or case["label_rationale"],
                    }
                    for case in anomaly_evaluation["cases"]
                ]),
                use_container_width=True,
                hide_index=True,
            )
    else:
        st.info("Anomaly evaluation metrics are unavailable for this response.")

    st.markdown("---")
    st.markdown("#### Your forecast and unusual-spending review")
    if forecast:
        st.caption(
            f"Forecast horizon: {forecast.get('horizon_months', '—')} month(s). "
            f"Historical months used: {forecast.get('history_months', 0)}; "
            f"transactions: {forecast.get('history_transactions', 0)}. "
            f"{forecast.get('currency', 'Amounts are shown in imported units; currency is unknown.')}"
        )
        history_rows = forecast.get("historical_actuals", [])
        if history_rows:
            actual_df = pd.DataFrame(history_rows)
            actual_df["series"] = "Historical actual"
            fig_forecast = px.line(
                actual_df,
                x="month",
                y="total",
                markers=True,
                color="series",
                title="Historical monthly expenses and next-month forecast",
                labels={"month": "Month", "total": "Expense amount (imported units)"},
                color_discrete_sequence=["#4EA1FF"],
            )
            if forecast.get("available"):
                fig_forecast.add_scatter(
                    x=[forecast["forecast_month"]],
                    y=[forecast["forecast_amount"]],
                    mode="markers",
                    name="Future forecast",
                    marker={"size": 12, "symbol": "diamond", "color": "#A78BFA"},
                )
            else:
                fig_forecast.update_layout(title="Historical monthly expenses")
            fig_forecast.update_layout(template="plotly_dark", font_family="Inter")
            st.plotly_chart(fig_forecast, use_container_width=True)
        if forecast.get("available"):
            st.write(forecast.get("method", ""))
            st.metric(
                f"Forecast for {forecast['forecast_month']} (imported units)",
                f"{safe_number(forecast['forecast_amount']):,.2f}",
                help="Historical actuals and the future estimate are shown as separate series.",
            )
            st.caption(forecast.get("limitations", ""))
        else:
            st.info(forecast.get("reason") or "A forecast is not available for this account yet.")

        forecast_metrics = forecast.get("evaluation", {})
        st.markdown("##### Chronological forecast evaluation")
        if forecast_metrics.get("available"):
            st.caption(
                f"{forecast_metrics['periods']} held-out months. "
                f"{forecast_metrics['method']} Metrics are in imported amount units."
            )
            st.dataframe(
                pd.DataFrame([
                    {"Method": "Recent-month mean", "MAE": forecast_metrics["mae"], "RMSE": forecast_metrics["rmse"]},
                    {"Method": "Last-month baseline", "MAE": forecast_metrics["baseline_mae"], "RMSE": forecast_metrics["baseline_rmse"]},
                ]),
                use_container_width=True,
                hide_index=True,
            )
            model_errors = (forecast_metrics["mae"], forecast_metrics["rmse"])
            baseline_errors = (forecast_metrics["baseline_mae"], forecast_metrics["baseline_rmse"])
            if all(value is not None for value in model_errors + baseline_errors):
                if model_errors == baseline_errors:
                    st.caption("The methods have equal MAE and RMSE on these held-out months.")
                elif all(model <= baseline for model, baseline in zip(model_errors, baseline_errors)):
                    st.caption("The recent-month mean has lower or equal errors on these held-out months.")
                elif all(baseline <= model for model, baseline in zip(model_errors, baseline_errors)):
                    st.caption("The last-month baseline has lower or equal errors on these held-out months.")
                else:
                    st.caption("The methods trade off MAE and RMSE on these held-out months.")
        else:
            st.info(forecast_metrics.get("reason") or "Forecast metrics cannot be calculated yet.")
    else:
        st.info("Forecast details are unavailable because the backend did not return a result.")

    if anomalies:
        flags = anomalies.get("flags", [])
        st.markdown("##### Possible unusual spending")
        st.caption(anomalies.get("label", "Flags are indicators for review, not confirmed fraud."))
        if flags:
            st.dataframe(
                pd.DataFrame([
                    {
                        "Date": flag.get("date"),
                        "Description": flag.get("description"),
                        "Category": flag.get("category"),
                        "Amount (imported units)": flag.get("amount"),
                        "Reason": flag.get("reason"),
                    }
                    for flag in flags
                ]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info(
                "No transactions were flagged by the configured rule. A category needs at least "
                f"{anomalies.get('minimum_history', 3)} earlier transactions to be assessed."
            )

# ---------------------------------------------------
# FOOTER
# ---------------------------------------------------
st.markdown(
    """
<div class="footer">
    Copyright 2026 Expense Intelligence Platform. Secure.
</div>
""",
    unsafe_allow_html=True,
)
