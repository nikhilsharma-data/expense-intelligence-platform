import streamlit as st
import requests
import pandas as pd
import matplotlib.pyplot as plt

BASE_URL = "https://expense-intelligence-platform.onrender.com"

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

summary = safe_get("summary",{},params={"range": date_range})
categories = safe_get("category-breakdown", [], params={"range": date_range})
trend = safe_get("monthly-trend", [], params={"range": date_range})
insights = safe_get("insights", {}, params={"range": date_range})

# -------------------------------------------------
# TABS
# -------------------------------------------------
tab1, tab2, tab3 = st.tabs(["📊 Dashboard", "🧠 Insights", "📂 Upload"])

# =================================================
# DASHBOARD TAB
# =================================================
with tab1:

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

        if not expense_df.empty:

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

        else:
            st.info("No expense data available.")

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

# =================================================
# UPLOAD TAB
# =================================================
with tab3:

    st.header("📂 Upload Bank Statement")
    st.caption(
    "Supports CSV uploads and future PDF bank statements."
    )
    st.caption("Recommended rows: up to 5,000")

    uploaded_file = st.file_uploader(
        "Upload Bank Statement",
        type=["csv", "pdf"]
    )

    if uploaded_file is not None:

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

            # Save uploaded PDF temporarily
            with open("uploaded_statement.pdf", "wb") as f:
                f.write(uploaded_file.getbuffer())

            st.success("PDF uploaded successfully!")

            st.info(
                "PDF parsing engine will be implemented next."
            )
            st.info(
            """
            ✅ Supported Statement Formats (Upcoming)

            • HDFC Bank PDF
            • SBI Bank PDF
            • ICICI Bank PDF
            • Axis Bank PDF
            • Kotak Bank PDF
            • CSV Statements

            PDF parsing engine is currently under development.
            """
            )

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