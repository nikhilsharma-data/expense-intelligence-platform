import streamlit as st
import requests
import pandas as pd
import matplotlib.pyplot as plt

BASE_URL = "http://127.0.0.1:8000"

# ---------------------------------------------------
# SESSION STATE
# ---------------------------------------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "user_id" not in st.session_state:
    st.session_state.user_id = None

if "user_name" not in st.session_state:
    st.session_state.user_name = None
    
# ---------------------------------------------------
# LOGIN / SIGNUP SCREEN
# ---------------------------------------------------
if not st.session_state.logged_in:

    st.title("🔐 Expense Intelligence Login")

    auth_tab1, auth_tab2 = st.tabs(["Login", "Signup"])

    # LOGIN
    with auth_tab1:
        email = st.text_input("Email")
        password = st.text_input(
            "Password",
            type="password"
        )

        if st.button("Login"):

            response = requests.post(
                f"{BASE_URL}/login",
                json={
                    "email": email,
                    "password": password
                }
            )

            data = response.json()

            if data.get("status") == "success":
                st.session_state.logged_in = True
                st.session_state.user_id = data["user_id"]
                st.session_state.user_name = data["name"]

                st.success("Login successful!")
                st.rerun()

            else:
                st.error(
                    data.get("error", "Login failed")
                )

    # SIGNUP
    with auth_tab2:
        name = st.text_input("Name")
        signup_email = st.text_input("Signup Email")
        signup_password = st.text_input(
            "Signup Password",
            type="password"
        )

        if st.button("Create Account"):

            response = requests.post(
                f"{BASE_URL}/signup",
                json={
                    "name": name,
                    "email": signup_email,
                    "password": signup_password
                }
            )

            data = response.json()

            if data.get("status") == "success":
                st.success(
                    "Account created! Please login."
                )
            else:
                st.error(
                    data.get("error", "Signup failed")
                )

    st.stop()
    
st.sidebar.success(
    f"Welcome, {st.session_state.user_name}"
)

if st.sidebar.button("Logout"):
    st.session_state.logged_in = False
    st.session_state.user_id = None
    st.session_state.user_name = None
    st.rerun()

# -------------------------------------------------
# PAGE CONFIG
# -------------------------------------------------
st.set_page_config(
    page_title="Expense Intelligence Platform",
    page_icon="💰",
    layout="wide"
)

# Sidebar width CSS
st.markdown("""
<style>
section[data-testid="stSidebar"] {
    width: 250px !important;
}
</style>
""", unsafe_allow_html=True)

st.title("💰 Expense Intelligence Platform")
st.caption("Track spending. Detect patterns. Improve savings.")

# -------------------------------------------------
# SIDEBAR FILTERS
# -------------------------------------------------
st.sidebar.header("Filters")

date_range = st.sidebar.selectbox(
    "Choose Period",
    ["all", "30d", "90d", "ytd"],
    index=0,
    key="date_filter"
)

# -------------------------------------------------
# API CALLS
# -------------------------------------------------
def safe_get(endpoint, default, params=None):
    try:
        return requests.get(
            f"{BASE_URL}/{endpoint}",
            params=params
        ).json()
    except:
        return default

summary = safe_get("summary",{},params={"range": date_range, "user_id": st.session_state.user_id})
categories = safe_get("category-breakdown", [], params={"range": date_range, "user_id": st.session_state.user_id})
trend = safe_get("monthly-trend", [], params={"range": date_range, "user_id": st.session_state.user_id})
insights = safe_get("insights", {}, params={"range": date_range, "user_id": st.session_state.user_id})
transactions = safe_get("transactions", [], params={"user_id": st.session_state.user_id})

has_data = False
if summary:
    expense = abs(safe_number(summary.get("total_expense", 0)))
    income = safe_number(summary.get("total_income", 0))

    if expense > 0 or income > 0:
        has_data = True

# -------------------------------------------------
# TABS
# -------------------------------------------------
tab1, tab2, tab3 = st.tabs(["📊 Dashboard", "🧠 Insights", "📂 Upload"])

# =================================================
# DASHBOARD TAB
# =================================================
with tab1:
    
    if not has_data:

        st.info(
            """
            No bank statement uploaded yet.

            Please go to Upload tab and upload
            a CSV or PDF bank statement
            to begin analysis.
            """
        )

    else:

    # ---------------- KPI SECTION ----------------
        st.header("📌 Summary")

    balance = summary.get("total") or 0
    expense = summary.get("total_expense") or 0
    income = summary.get("total_income") or 0

    expense = abs(expense)
    expense_ratio = (expense / income * 100) if income else 0

    col1, col2, col3 = st.columns(3)

    with col1:
        with st.container(border=True):
            st.metric("Balance", f"₹ {balance:,.0f}")

    with col2:
        with st.container(border=True):
            st.metric(
                "Expense",
                f"₹ {expense:,.0f}",
                f"{expense_ratio:.1f}% of income"
            )

    with col3:
        with st.container(border=True):
            st.metric("Income", f"₹ {income:,.0f}")

    st.markdown("---")

    # ---------------- CATEGORY SECTION ----------------
    st.header("📊 Category Breakdown")

    if categories:

        df = pd.DataFrame(categories)

        df["total"] = pd.to_numeric(df["total"], errors="coerce")
        df["category"] = df["category"].fillna("Others").astype(str)

        # Sidebar Filter
        st.sidebar.header("Filters")

        selected_category = st.sidebar.selectbox(
            "Choose Category",
            ["All"] + sorted(df["category"].unique().tolist()),
            key="cat_filter"
        )

        if selected_category != "All":
            df = df[df["category"] == selected_category]


        # Expenses only
        expense_df = df.copy()

        if expense_df.empty:

            st.info(
                """
                No transaction data available yet.

                Upload a CSV or PDF bank statement
                to generate analytics and insights.
                """
            )

        else:

            expense_df["total"] = expense_df["total"].abs()
            expense_df = expense_df.sort_values("total", ascending=True)

            # Horizontal Bar Chart
            fig_bar, ax = plt.subplots(
            figsize=(8,4),
            facecolor="#0E1117"
            )

            ax.set_facecolor("#0E1117")

            ax.barh(
                expense_df["category"],
                expense_df["total"]
            )

            ax.tick_params(colors="white")
            ax.set_xlabel("Amount", color="white")
            ax.set_title("Expense by Category", color="white")

            plt.tight_layout()
            st.pyplot(fig_bar, width="stretch")

        # ---------------- DONUT CHART ----------------
        st.subheader("🥧 Expense Distribution")

        pie_df = df.copy()

        if not pie_df.empty:

            pie_df["total"] = pie_df["total"].abs()
            pie_df = pie_df.sort_values("total", ascending=False)

            top5 = pie_df.head(5).copy()
            rest = pie_df.iloc[5:]["total"].sum()

            if rest > 0:
                top5.loc[len(top5)] = ["Other", rest]

            pie_df = top5

            fig, ax = plt.subplots(
                figsize=(4,4),
                facecolor="#0E1117"
            )

            ax.set_facecolor("#0E1117")

            ax.pie(
                pie_df["total"],
                labels=pie_df["category"],
                autopct="%1.1f%%",
                startangle=90,
                radius=0.82,
                wedgeprops=dict(width=0.35),
                textprops={
                    "color": "white",
                    "fontsize": 9
                }
            )

            ax.set_title(
                "Top Expense Share",
                color="white"
            )

            plt.tight_layout()

            st.pyplot(fig, width="stretch")

        # Export CSV
        csv = df.to_csv(index=False).encode("utf-8")

        st.download_button(
            "⬇ Download Category Report",
            csv,
            file_name="category_report.csv",
            mime="text/csv",
            width="stretch"
        )

    else:
        st.warning("No category data available. Upload CSV first.")

    st.markdown("---")

    # ---------------- TREND SECTION ----------------
    st.header("📈 Net Cashflow Trend")
    st.caption("Monthly balance movement")

    if trend:

        trend_df = pd.DataFrame(trend)
        trend_df["total"] = pd.to_numeric(
            trend_df["total"],
            errors="coerce"
        )

        st.line_chart(
            trend_df.set_index("month"),
            width="stretch"
        )

    else:
        st.info("No trend data available.")

# =================================================
# INSIGHTS TAB
# =================================================
with tab2:

    st.header("🧠 Smart Insights")

    if insights.get("insights"):

        for item in insights["insights"]:

            txt = item.lower()

            if "warning" in txt:
                st.warning(item)

            elif "saved" in txt:
                st.success(item)

            else:
                st.info(item)

    else:
        st.info("No insights available.")
        
        
# ---------------- TRANSACTION TABLE ----------------

    st.header("📋 Transaction Details")

    if transactions:

        txn_df = pd.DataFrame(transactions)
        txn_df["type"] = txn_df["amount"].apply(
        lambda x: "Credit" if x > 0 else "Debit"
    )

        txn_df["display_amount"] = txn_df["amount"].apply(
        lambda x: f"+₹ {x:,.0f}" if x > 0 else f"-₹ {abs(x):,.0f}"
    )
    
        search_text = st.text_input(
            "Search description"
        )

        if search_text:
            txn_df = txn_df[
                txn_df["description"].str.contains(
                    search_text,
                    case=False,
                    na=False
                )
            ]

        display_df = txn_df[
        ["date", "description", "type", "display_amount", "category"]
        ]

        display_df.columns = [
            "Date",
            "Description",
            "Type",
            "Amount",
            "Category"
        ]

        st.dataframe(
            display_df,
            width="stretch",
            hide_index=True
        )

        csv = txn_df.to_csv(index=False)

        st.download_button(
            "⬇ Download Transactions CSV",
            csv,
            file_name="transactions.csv",
            mime="text/csv"
        )

    else:
        st.info("No transactions available.")

# =================================================
# UPLOAD TAB
# =================================================
with tab3:

    st.header("📂 Upload Bank Statement")
    st.caption(
    "Supports CSV and PDF bank statements."
    )
    st.caption("Recommended rows: up to 5,000")

    uploaded_file = st.file_uploader(
        "Upload Bank Statement",
        type=["csv", "pdf"]
    )

    if uploaded_file is not None:
        
        st.caption(
            f"Last uploaded file: {uploaded_file.name}"
        )

        file_type = uploaded_file.name.split(".")[-1].lower()

        # =========================================
        # CSV Upload Flow
        # =========================================
        if file_type == "csv":

            files = {
                "file": (
                    uploaded_file.name,
                    uploaded_file.getvalue(),
                    "text/csv"
                )
            }

            response = requests.post(
                f"{BASE_URL}/upload",
                params={"user_id": st.session_state.user_id},
                files=files
            )

            if response.status_code == 200:
                st.success("CSV uploaded successfully!")
                st.rerun()
            else:
                st.error("CSV upload failed.")

        # =========================================
        # PDF Upload Flow
        # =========================================
        elif file_type == "pdf":

            files = {
                "file": (
                    uploaded_file.name,
                    uploaded_file.getvalue(),
                    "application/pdf"
                )
            }

            response = requests.post(
                f"{BASE_URL}/upload",
                params={"user_id": st.session_state.user_id},
                files=files
            )
            
            print("PDF Upload Response:", response.status_code)
            print(response.text)

            if response.status_code == 200:

                st.success("PDF uploaded successfully!")
                st.rerun()

                # ==============================
                # Empty PDF Detection
                # ==============================
                if not extracted_text.strip():

                    st.warning(
                        """
                        No readable text detected.

                        This may be:
                        • scanned/image-based PDF
                        • unsupported statement format
                        • extraction issue in parser
                        """
                    )

            else:
                st.error(f"Status Code: {response.status_code}")
                st.error(f"Response: {response.text}")

    sample_csv = """Date,Description,Amount
2026-01-01,Salary,60000
2026-01-02,Rent,-18000
2026-01-03,Swiggy,-450
"""

    st.download_button(
        "⬇ Download Sample CSV",
        sample_csv,
        file_name="sample_expense.csv",
        mime="text/csv",
        width="stretch"
    )