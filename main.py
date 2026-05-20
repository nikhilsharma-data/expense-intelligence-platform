from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from matplotlib import lines
import pandas as pd
from streamlit import pdf, text
from db import conn, cursor
import pdfplumber
import re
from io import BytesIO

app = FastAPI(title="Expense Intelligence API")

# ---------------------------------------
# ✅ ADD THIS BLOCK HERE (ONCE)
# ---------------------------------------
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    date DATE,
    description TEXT,
    category TEXT,
    amount NUMERIC
)
""")
conn.commit()

# ---------------------------------------------------
# CORS (safe for local Streamlit + future deployment)
# ---------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------
# HELPERS
# ---------------------------------------------------
from datetime import date, timedelta

def safe_number(val):
    return float(val) if val is not None else 0

def get_date_filter(range_value):
    today = date.today()

    if range_value == "30d":
        return today - timedelta(days=30)

    elif range_value == "90d":
        return today - timedelta(days=90)

    elif range_value == "ytd":
        return date(today.year, 1, 1)

    return None


def categorize(description):

    desc = description.upper()

    # ---------------- INCOME ----------------
    if "SALARY" in desc or "NEFT CR" in desc:
        return "Income"

    if "CASH DEP" in desc or "CREDIT INTEREST" in desc or "REV-" in desc:
        return "Income"
    
    if "REV-" in desc or "REVERSAL" in desc:
        return "Income"

    # ---------------- FOOD ----------------
    elif "FOOD" in desc or "GROCER" in desc or "RESTAURANT" in desc:
        return "Food"

    # ---------------- FUEL ----------------
    elif "FUEL" in desc or "PETROL" in desc:
        return "Fuel"

    # ---------------- EMI / LOAN ----------------
    elif "EMI" in desc or "LOAN" in desc:
        return "EMI"

    # ---------------- UTILITIES ----------------
    elif "ELECTRIC" in desc or "MOBILE" in desc or "BILLPAY" in desc:
        return "Utilities"

    # ---------------- INSURANCE ----------------
    elif "INSURE" in desc:
        return "Insurance"

    # ---------------- SHOPPING ----------------
    elif "SHOP" in desc or "MART" in desc:
        return "Shopping"

    # ---------------- SUBSCRIPTION ----------------
    elif "STREAMING" in desc or "NETFLIX" in desc or "SUBSCRIPTION" in desc:
        return "Subscription"

    # ---------------- TRANSFER ----------------
    elif "IMPS" in desc or "NEFT DR" in desc or "UPI" in desc:
        return "Transfer"

    # ---------------- CREDIT CARD ----------------
    elif "CREDITCARD" in desc or "CARD" in desc:
        return "Card Payment"

    # ---------------- VEHICLE ----------------
    elif "MOTOR" in desc or "VEHICLE" in desc:
        return "Vehicle"

    # ---------------- DEFAULT ----------------
    else:
        return "Others"


# ---------------------------------------------------
# ROOT
# ---------------------------------------------------
@app.get("/")
def root():
    return {"status": "Expense Intelligence API Running"}


# ---------------------------------------------------
# UPLOAD CSV
# ---------------------------------------------------
@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    
    print("UPLOAD API HIT")

    try:

        filename = file.filename.lower()

        # =================================================
        # CSV FLOW
        # =================================================
        if filename.endswith(".csv"):

            df = pd.read_csv(file.file)

            df.columns = df.columns.str.strip().str.lower()

            required_cols = ["date", "description", "amount"]

            for col in required_cols:
                if col not in df.columns:
                    return {"error": f"Missing column: {col}"}

            df["date"] = pd.to_datetime(
                df["date"],
                errors="coerce"
            )

            df["amount"] = pd.to_numeric(
                df["amount"],
                errors="coerce"
            )

            df = df.dropna(subset=["date", "amount"])

        # =================================================
        # PDF FLOW
        # =================================================
        elif filename.endswith(".pdf"):

            pdf_bytes = await file.read()

            extracted_text = ""

            with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:

                for page in pdf.pages:

                    # -------------------------
                    # TEXT EXTRACTION
                    # -------------------------
                    text = page.extract_text()

                    print("\n===== PAGE DEBUG =====")

                    if text:
                        print(text[:1000])
                        extracted_text += text + "\n"
                    else:
                        print("extract_text() returned None")

                    # -------------------------
                    # TABLE EXTRACTION
                    # -------------------------
                    tables = page.extract_tables()

                    print(f"Tables found: {len(tables)}")

                    if tables:
                        print("Sample table:")
                        try:
                            print(tables[0][:5])
                        except Exception as e:
                            print(f"Table debug error: {e}")
                    else:
                        print("No tables found")
        
                    
                    print("\n========== PAGE TEXT ==========\n")
                    print(text)

                    if text:
                        extracted_text += text + "\n"

            transactions = []

            for page in pdf.pages:

                tables = page.extract_tables()

                for table in tables:

                    for row in table[1:]:   # skip header row

                        try:
                            date = row[0]
                            description = row[1]

                            withdrawal = row[4]
                            deposit = row[5]

                            if deposit and deposit.strip():
                                amount = float(deposit.replace(",", ""))
                            elif withdrawal and withdrawal.strip():
                                amount = -float(withdrawal.replace(",", ""))
                            else:
                                continue

                            transactions.append({
                                "date": date,
                                "description": description,
                                "amount": amount
                            })

                        except Exception as e:
                            print("Row parse error:", row, e)

            for txn in transactions:
                print(txn)
            # -------------------------
            # SAVE PDF TRANSACTIONS TO DB
            # -------------------------

            cursor.execute("DELETE FROM transactions")

            for txn in transactions:

                category = categorize(txn["description"])

                cursor.execute("""
                    INSERT INTO transactions
                    (date, description, amount, category)
                    VALUES (%s, %s, %s, %s)
                """, (
                    pd.to_datetime(txn["date"], dayfirst=True),
                    txn["description"],
                    txn["amount"],
                    category
                ))

            conn.commit()

            print(f"{len(transactions)} transactions inserted into DB")
            
            return {
                "status": "success",
                "preview": extracted_text[:5000],
                "transactions": transactions[:20]
            }

        # =================================================
        # CLEAR OLD DATA
        # =================================================
        cursor.execute("DELETE FROM transactions")

        for _, row in df.iterrows():

            category = categorize(
                row["description"]
            )

            cursor.execute("""
                INSERT INTO transactions
                (date, description, amount, category)
                VALUES (%s,%s,%s,%s)
            """, (
                row["date"],
                row["description"],
                row["amount"],
                category
            ))

        conn.commit()

        return {
            "status": "success",
            "rows_loaded": len(df)
        }

    except Exception as e:
        import traceback
        traceback.print_exc()

        conn.rollback()

        raise e


# ---------------------------------------------------
# SUMMARY
# ---------------------------------------------------
@app.get("/summary")
def get_summary(range: str = "all"):

    start_date = get_date_filter(range)

    if start_date:
        cursor.execute("""
        SELECT
            COALESCE(SUM(amount),0),
            COALESCE(SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END),0)
        FROM transactions
        WHERE date >= %s
    """, (start_date,))
    else:
        cursor.execute("""
        SELECT
            COALESCE(SUM(amount),0),
            COALESCE(SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END),0)
        FROM transactions
    """)

    result = cursor.fetchone()

    total = safe_number(result[0])
    expense = abs(safe_number(result[1]))
    income = safe_number(result[2])

    savings_pct = (total / income * 100) if income > 0 else 0

    return {
        "total": total,
        "total_expense": expense,
        "total_income": income,
        "savings_percent": round(savings_pct, 1)
    }


# ---------------------------------------------------
# CATEGORY BREAKDOWN
# ---------------------------------------------------
@app.get("/category-breakdown")
def category_breakdown(range: str = "all"):

    start_date = get_date_filter(range)

    if start_date:
        cursor.execute("""
            SELECT category, ABS(SUM(amount)) AS total
            FROM transactions
            WHERE amount < 0
            AND date >= %s
            GROUP BY category
            ORDER BY total DESC
        """, (start_date,))
    else:
        cursor.execute("""
            SELECT category, ABS(SUM(amount)) AS total
            FROM transactions
            WHERE amount < 0
            GROUP BY category
            ORDER BY total DESC
        """)

    rows = cursor.fetchall()

    return [
        {
            "category": r[0],
            "total": safe_number(r[1])
        }
        for r in rows
    ]


# ---------------------------------------------------
# MONTHLY TREND
# ---------------------------------------------------
@app.get("/monthly-trend")
def monthly_trend(range: str = "all"):

    start_date = get_date_filter(range)

    if start_date:
        cursor.execute("""
            SELECT
                TO_CHAR(DATE_TRUNC('month', date), 'Mon-YY') AS month,
                SUM(amount) AS total
            FROM transactions
            WHERE date >= %s
            GROUP BY DATE_TRUNC('month', date)
            ORDER BY DATE_TRUNC('month', date)
        """, (start_date,))
    else:
        cursor.execute("""
            SELECT
                TO_CHAR(DATE_TRUNC('month', date), 'Mon-YY') AS month,
                SUM(amount) AS total
            FROM transactions
            GROUP BY DATE_TRUNC('month', date)
            ORDER BY DATE_TRUNC('month', date)
        """)

    rows = cursor.fetchall()

    return [
        {
            "month": r[0],
            "total": safe_number(r[1])
        }
        for r in rows
    ]


# ---------------------------------------------------
# INSIGHTS ENGINE
# ---------------------------------------------------
@app.get("/insights")
def get_insights(range: str = "all"):

    insights = []

    start_date = get_date_filter(range)

    where_clause = ""
    params = ()

    if start_date:
        where_clause = "WHERE date >= %s"
        params = (start_date,)

    # --------------------------------------------------
    # Income + Expense
    # --------------------------------------------------
    cursor.execute(f"""
        SELECT
            COALESCE(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END),0),
            COALESCE(ABS(SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END)),0)
        FROM transactions
        {where_clause}
    """, params)

    income, expense = cursor.fetchone()

    income = safe_number(income)
    expense = safe_number(expense)

    # --------------------------------------------------
    # Savings Insight
    # --------------------------------------------------
    if income > 0:
        savings = income - expense
        ratio = (savings / income) * 100

        if savings >= 0:
            insights.append(
                f"You saved ₹{savings:,.0f} this period ({ratio:.1f}% of income)"
            )
        else:
            insights.append(
                f"Warning: Overspent by ₹{abs(savings):,.0f}"
            )

    # --------------------------------------------------
    # Spend Ratio Warning
    # --------------------------------------------------
    if income > 0:
        spend_ratio = (expense / income) * 100

        if spend_ratio > 80:
            insights.append(
                "Warning: You are spending more than 80% of income"
            )

    # --------------------------------------------------
    # Top Category
    # --------------------------------------------------
    if start_date:
        cursor.execute("""
            SELECT category, ABS(SUM(amount)) total
            FROM transactions
            WHERE amount < 0
            AND date >= %s
            GROUP BY category
            ORDER BY total DESC
            LIMIT 1
        """, params)
    else:
        cursor.execute("""
            SELECT category, ABS(SUM(amount)) total
            FROM transactions
            WHERE amount < 0
            GROUP BY category
            ORDER BY total DESC
            LIMIT 1
        """)

    row = cursor.fetchone()

    if row:
        insights.append(
            f"Highest expense category is {row[0]} (₹{row[1]:,.0f})"
        )

    # --------------------------------------------------
    # Top Merchants
    # --------------------------------------------------
    if start_date:
        cursor.execute("""
            SELECT description, ABS(SUM(amount)) total
            FROM transactions
            WHERE amount < 0
            AND date >= %s
            GROUP BY description
            ORDER BY total DESC
            LIMIT 3
        """, params)
    else:
        cursor.execute("""
            SELECT description, ABS(SUM(amount)) total
            FROM transactions
            WHERE amount < 0
            GROUP BY description
            ORDER BY total DESC
            LIMIT 3
        """)

    rows = cursor.fetchall()

    if rows:
        names = [r[0] for r in rows]

        insights.append(
            f"Top spending merchants: {', '.join(names)}"
        )

    # --------------------------------------------------
    # Largest Single Expense
    # --------------------------------------------------
    if start_date:
        cursor.execute("""
            SELECT description, ABS(amount)
            FROM transactions
            WHERE amount < 0
            AND date >= %s
            ORDER BY ABS(amount) DESC
            LIMIT 1
        """, params)
    else:
        cursor.execute("""
            SELECT description, ABS(amount)
            FROM transactions
            WHERE amount < 0
            ORDER BY ABS(amount) DESC
            LIMIT 1
        """)

    row = cursor.fetchone()

    if row:
        insights.append(
            f"Largest single expense: {row[0]} (₹{row[1]:,.0f})"
        )

    # --------------------------------------------------
    # Weekend Spending
    # --------------------------------------------------
    if start_date:
        cursor.execute("""
            SELECT COALESCE(ABS(SUM(amount)),0)
            FROM transactions
            WHERE amount < 0
            AND EXTRACT(DOW FROM date) IN (0,6)
            AND date >= %s
        """, params)
    else:
        cursor.execute("""
            SELECT COALESCE(ABS(SUM(amount)),0)
            FROM transactions
            WHERE amount < 0
            AND EXTRACT(DOW FROM date) IN (0,6)
        """)

    row = cursor.fetchone()

    weekend = safe_number(row[0]) if row else 0

    if expense > 0:
        pct = (weekend / expense) * 100

        if pct > 35:
            insights.append(
                f"Weekend spending is high ({pct:.1f}% of expenses)"
            )

    # --------------------------------------------------
    # Average Monthly Spend
    # --------------------------------------------------
    if start_date:
        cursor.execute("""
            SELECT AVG(month_total)
            FROM (
                SELECT ABS(SUM(amount)) AS month_total
                FROM transactions
                WHERE amount < 0
                AND date >= %s
                GROUP BY DATE_TRUNC('month', date)
            ) t
        """, params)
    else:
        cursor.execute("""
            SELECT AVG(month_total)
            FROM (
                SELECT ABS(SUM(amount)) AS month_total
                FROM transactions
                WHERE amount < 0
                GROUP BY DATE_TRUNC('month', date)
            ) t
        """)

    row = cursor.fetchone()

    avg_month = safe_number(row[0]) if row else 0

    if avg_month > 0:
        insights.append(
            f"Average monthly spend: ₹{avg_month:,.0f}"
        )

    # --------------------------------------------------
    # Fallback
    # --------------------------------------------------
    if not insights:
        insights.append("No insights available for selected period")

    return {"insights": insights}