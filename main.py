import logging
import os
import re
from datetime import date, timedelta
from io import BytesIO
from typing import List, Optional

import bcrypt
import pandas as pd
import pdfplumber
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from db import get_db_connection

# -------------------------------
# Configuration
# -------------------------------
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("expense_api")

# -------------------------------
# App & Middleware
# -------------------------------
app = FastAPI(title="Expense Intelligence API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------
# Pydantic Models
# -------------------------------
class SignupRequest(BaseModel):
    name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class StatusMessage(BaseModel):
    message: str


class SignupResponse(BaseModel):
    status: str
    user_id: int


class LoginResponse(BaseModel):
    status: str
    user_id: int
    name: str


class TransactionOut(BaseModel):
    date: str
    description: str
    amount: float
    category: str


class CategoryOut(BaseModel):
    category: str
    total: float


class TrendOut(BaseModel):
    month: str
    total: float


class SummaryOut(BaseModel):
    total: float
    total_expense: float
    total_income: float
    savings_percent: float


class UploadResponse(BaseModel):
    status: str
    rows_loaded: Optional[int] = None
    preview: Optional[str] = None
    transactions: Optional[List[dict]] = None


class InsightResponse(BaseModel):
    insights: List[str]


# -------------------------------
# Database Dependency
# -------------------------------
def get_db():
    conn = get_db_connection()
    cursor = None
    try:
        cursor = conn.cursor()
        yield conn, cursor
    except Exception:
        conn.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        conn.close()


# -------------------------------
# Startup Event
# -------------------------------
@app.on_event("startup")
def startup_db():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER,
                    date DATE,
                    description TEXT,
                    category TEXT,
                    amount NUMERIC
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_transactions_user_id
                ON transactions(user_id);
                """
            )
        conn.commit()
    finally:
        conn.close()
    logger.info("Database tables and indexes verified.")


# -------------------------------
# Helpers
# -------------------------------
def safe_number(val):
    return float(val) if val is not None else 0.0


def get_date_filter(range_value: str) -> Optional[date]:
    today = date.today()

    if range_value == "30d":
        return today - timedelta(days=30)

    if range_value == "90d":
        return today - timedelta(days=90)

    if range_value == "ytd":
        return date(today.year, 1, 1)

    return None


def build_date_clause(start_date):
    if start_date:
        return " AND date >= %s", (start_date,)
    return "", ()


PDF_LINE_TRANSACTION_RE = re.compile(
    r"^(\d{2}[-/]\d{2}[-/]\d{4})\s+(.+?)\s+"
    r"([\d,]+(?:\.\d{1,2})?)\((Dr|Cr)\)\s+"
    r"[\d,]+(?:\.\d{1,2})?\((Dr|Cr)\)\s*$",
    re.IGNORECASE,
)


def parse_money(value):
    if value is None:
        return None

    cleaned = (
        str(value)
        .replace(",", "")
        .replace("INR", "")
        .replace("Rs.", "")
        .replace("₹", "")
        .strip()
    )

    if not cleaned:
        return None

    try:
        return float(cleaned)
    except ValueError:
        return None


def add_pdf_transaction(transactions, seen, date_str, description, amount):
    if not date_str or not description or amount is None:
        return

    key = (str(date_str).strip(), str(description).strip(), round(float(amount), 2))
    if key in seen:
        return

    seen.add(key)
    transactions.append(
        {
            "date": key[0],
            "description": key[1],
            "amount": key[2],
        }
    )


def parse_dr_cr_text_line(line):
    match = PDF_LINE_TRANSACTION_RE.match(" ".join(str(line).split()))
    if not match:
        return None

    date_str, description, amount_text, amount_type, _balance_type = match.groups()
    amount = parse_money(amount_text)
    if amount is None:
        return None

    if amount_type.lower() == "dr":
        amount = -amount

    return date_str, description, amount


def parse_pdf_text_transactions(text):
    transactions = []

    for line in (text or "").splitlines():
        parsed = parse_dr_cr_text_line(line)
        if parsed:
            transactions.append(parsed)

    return transactions


def parse_pdf_table_transactions(table):
    transactions = []

    for row in table[1:]:
        cells = [str(cell).strip() if cell is not None else "" for cell in row]
        cells = [cell for cell in cells if cell]

        if not cells:
            continue

        parsed = parse_dr_cr_text_line(" ".join(cells))
        if parsed:
            transactions.append(parsed)
            continue

        if len(row) < 2:
            continue

        date_str = row[0]
        description = row[1]
        withdrawal = row[4] if len(row) > 4 else None
        deposit = row[5] if len(row) > 5 else None

        deposit_amount = parse_money(deposit)
        withdrawal_amount = parse_money(withdrawal)

        if deposit_amount is not None:
            transactions.append((date_str, description, deposit_amount))
        elif withdrawal_amount is not None:
            transactions.append((date_str, description, -withdrawal_amount))

    return transactions


def has_keyword(desc: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in desc for keyword in keywords)


def categorize(description: str) -> str:
    desc = " ".join(str(description or "").upper().split())

    category_rules = [
        (
            "Income",
            (
                "SALARY",
                "NEFT CR",
                "IMPS FROM",
                "CASH DEP",
                "CREDIT INTEREST",
                "INTEREST CAPITALISED",
                "FREELANCE PAYMENT",
                "REFUND",
                "REV-",
                "REVERSAL",
            ),
        ),
        (
            "Rent",
            (
                "RENT",
                "HOUSE RENT",
                "LANDLORD",
            ),
        ),
        (
            "Food",
            (
                "SWIGGY",
                "ZOMATO",
                "FOOD",
                "FOODHUB",
                "RESTAURANT",
                "CAFE",
                "DINING",
                "EATERY",
                "PIZZA",
                "DOMINOS",
                "MCDONALD",
                "KFC",
            ),
        ),
        (
            "Groceries",
            (
                "GROCER",
                "GROCERMART",
                "MILK",
                "BIG BAZAAR",
                "DMART",
                "D MART",
                "SUPERMARKET",
                "HYPERMARKET",
                "RELIANCE FRESH",
                "JIOMART",
            ),
        ),
        (
            "Transport",
            (
                "UBER",
                "OLA",
                "RAPIDO",
                "METRO",
                "RAILWAY",
                "IRCTC",
                "BUS",
                "TAXI",
                "CAB",
                "FASTAG",
                "TOLL",
            ),
        ),
        (
            "Fuel",
            (
                "FUEL",
                "PETROL",
                "DIESEL",
                "FUELSTATION",
                "HPCL",
                "BPCL",
                "IOCL",
                "INDIAN OIL",
                "PETROL PUMP",
            ),
        ),
        (
            "Utilities",
            (
                "ELECTRIC",
                "ELECTRICBOARD",
                "BILLPAY",
                "BILL PAY",
                "WATER",
                "GAS",
                "LPG",
                "BROADBAND",
                "INTERNET",
                "MOBILEOPERATOR",
                "RELIANCEJIO",
                "JIO",
                "AIRTEL",
                "VODAFONE",
                "VI ",
                "POSTPAID",
                "PREPAID",
            ),
        ),
        (
            "Healthcare",
            (
                "PHARMACY",
                "MEDICAL",
                "MEDICINE",
                "HOSPITAL",
                "CLINIC",
                "DOCTOR",
                "LAB",
                "DIAGNOSTIC",
                "HEALTH",
            ),
        ),
        (
            "Insurance",
            (
                "INSURE",
                "INSURANCE",
                "HOMEINSURE",
                "ZENITHINSURE",
            ),
        ),
        (
            "Investments",
            (
                "SIP",
                "MUTUAL FUND",
                "MF ",
                "ZERODHA",
                "GROWW",
                "UPSTOX",
                "INVEST",
                "NPS",
                "PPF",
                "FD ",
                "FIXED DEPOSIT",
            ),
        ),
        (
            "Subscriptions",
            (
                "STREAMING",
                "NETFLIX",
                "SUBSCRIPTION",
                "SPOTIFY",
                "HOTSTAR",
                "PRIME",
                "YOUTUBE",
                "APPLE.COM",
                "GOOGLE PLAY",
            ),
        ),
        (
            "Shopping",
            (
                "AMAZON",
                "FLIPKART",
                "MYNTRA",
                "SHOP",
                "SHOPMART",
                "MART",
                "MALL",
                "RETAIL",
                "STORE",
                "BAZAAR",
            ),
        ),
        (
            "EMI & Loans",
            (
                "EMI",
                "LOAN",
                "VEHICLELOAN",
                "HOME LOAN",
                "HL ",
                "PERSONAL LOAN",
            ),
        ),
        (
            "Credit Card Payment",
            (
                "CREDITCARD",
                "CREDIT CARD",
                "CC9876",
                "MOPSOTHDRCARD",
            ),
        ),
        (
            "Cash Withdrawal",
            (
                "ATM",
                "ATL/",
                "CASH WDL",
                "CASH WITHDRAW",
            ),
        ),
        (
            "Vehicle",
            (
                "MOTOR",
                "VEHICLE",
                "APEX MOTORS",
                "SERVICE CENTER",
                "GARAGE",
            ),
        ),
        (
            "Transfers",
            (
                "UPI",
                "PAYTM",
                "PHONEPE",
                "GPAY",
                "GOOGLEPAY",
                "IMPS",
                "NEFT DR",
                "RTGS",
                "MB:",
            ),
        ),
        (
            "Bank Charges",
            (
                "CHARGE",
                "FEE",
                "PENALTY",
                "MIN BAL",
                "SMS ALERT",
            ),
        ),
    ]

    for category, keywords in category_rules:
        if has_keyword(desc, keywords):
            return category

    return "Others"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


# -------------------------------
# Root
# -------------------------------
@app.get("/")
def root():
    return {"status": "Expense Intelligence API Running"}


# -------------------------------
# Auth Endpoints
# -------------------------------
@app.post("/signup", response_model=SignupResponse)
def signup(user: SignupRequest, db=Depends(get_db)):
    conn, cur = db

    try:
        cur.execute("SELECT id FROM users WHERE email = %s", (user.email,))

        if cur.fetchone():
            raise HTTPException(status_code=409, detail="Email already registered")

        hashed_pw = hash_password(user.password)
        cur.execute(
            """
            INSERT INTO users (name, email, password_hash)
            VALUES (%s, %s, %s) RETURNING id
            """,
            (user.name, user.email, hashed_pw),
        )
        user_id = cur.fetchone()[0]
        conn.commit()

        logger.info("New user signed up: %s (id=%s)", user.email, user_id)
        return {"status": "success", "user_id": user_id}

    except HTTPException:
        raise

    except Exception:
        conn.rollback()
        logger.exception("Signup failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/login", response_model=LoginResponse)
def login(user: LoginRequest, db=Depends(get_db)):
    conn, cur = db

    try:
        cur.execute(
            "SELECT id, name, password_hash FROM users WHERE email = %s",
            (user.email,),
        )
        row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        user_id, name, stored_hash = row

        if not verify_password(user.password, stored_hash):
            raise HTTPException(status_code=401, detail="Invalid password")

        logger.info("User logged in: %s", user.email)
        return {"status": "success", "user_id": user_id, "name": name}

    except HTTPException:
        raise

    except Exception:
        logger.exception("Login failed")
        raise HTTPException(status_code=500, detail="Internal server error")


# -------------------------------
# File Upload
# -------------------------------
@app.post("/upload", response_model=UploadResponse)
async def upload_file(
    user_id: int,
    file: UploadFile = File(...),
    db=Depends(get_db),
):
    conn, cur = db
    logger.info("Upload request from user %s: %s", user_id, file.filename)

    try:
        filename = file.filename.lower()

        if filename.endswith(".csv"):
            df = pd.read_csv(file.file)
            df.columns = df.columns.str.strip().str.lower()

            required_cols = ["date", "description", "amount"]
            for col in required_cols:
                if col not in df.columns:
                    raise HTTPException(status_code=400, detail=f"Missing column: {col}")

            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
            df = df.dropna(subset=["date", "amount"])

            cur.execute("DELETE FROM transactions WHERE user_id = %s", (user_id,))

            for _, row in df.iterrows():
                category = categorize(str(row["description"]))
                cur.execute(
                    """
                    INSERT INTO transactions
                    (user_id, date, description, amount, category)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (user_id, row["date"], row["description"], row["amount"], category),
                )

            conn.commit()
            logger.info("CSV uploaded for user %s: %s rows", user_id, len(df))
            return {"status": "success", "rows_loaded": len(df)}

        if filename.endswith(".pdf"):
            pdf_bytes = await file.read()
            extracted_text = ""
            transactions = []
            seen_transactions = set()

            with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()

                    if text:
                        extracted_text += text + "\n"
                        logger.debug("Page text length: %s", len(text))
                        for date_str, description, amount in parse_pdf_text_transactions(text):
                            add_pdf_transaction(
                                transactions,
                                seen_transactions,
                                date_str,
                                description,
                                amount,
                            )
                    else:
                        logger.debug("No text found on page")

                    tables = page.extract_tables()
                    logger.debug("Tables found on page: %s", len(tables))

                    for table in tables:
                        if not table:
                            continue

                        for date_str, description, amount in parse_pdf_table_transactions(table):
                            add_pdf_transaction(
                                transactions,
                                seen_transactions,
                                date_str,
                                description,
                                amount,
                            )

            if not transactions:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "No transactions were found in this PDF. "
                        "Please upload a text-based statement or CSV file."
                    ),
                )

            cur.execute("DELETE FROM transactions WHERE user_id = %s", (user_id,))

            for txn in transactions:
                category = categorize(txn["description"])
                cur.execute(
                    """
                    INSERT INTO transactions
                    (user_id, date, description, amount, category)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        pd.to_datetime(txn["date"], dayfirst=True),
                        txn["description"],
                        txn["amount"],
                        category,
                    ),
                )

            conn.commit()
            logger.info(
                "PDF uploaded for user %s: %s transactions",
                user_id,
                len(transactions),
            )
            return {
                "status": "success",
                "preview": extracted_text[:5000],
                "transactions": transactions[:20],
            }

        raise HTTPException(status_code=400, detail="Unsupported file type. Use CSV or PDF.")

    except HTTPException:
        raise

    except Exception:
        conn.rollback()
        logger.exception("Upload failed")
        raise HTTPException(status_code=500, detail="Internal server error")


# -------------------------------
# Data Management
# -------------------------------
@app.delete("/delete-transactions", response_model=StatusMessage)
def delete_transactions(user_id: int, db=Depends(get_db)):
    conn, cur = db

    try:
        cur.execute("DELETE FROM transactions WHERE user_id = %s", (user_id,))
        conn.commit()

        logger.info("All transactions deleted for user %s", user_id)
        return {"message": "Transactions deleted successfully"}

    except Exception:
        conn.rollback()
        logger.exception("Delete transactions failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.delete("/delete-account", response_model=StatusMessage)
def delete_account(user_id: int, db=Depends(get_db)):
    conn, cur = db

    try:
        cur.execute("DELETE FROM transactions WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()

        logger.info("Account and transactions deleted for user %s", user_id)
        return {"message": "Account deleted successfully"}

    except Exception:
        conn.rollback()
        logger.exception("Delete account failed")
        raise HTTPException(status_code=500, detail="Internal server error")


# -------------------------------
# Analytics Endpoints
# -------------------------------
@app.get("/summary", response_model=SummaryOut)
def get_summary(range: str = "all", user_id: int = None, db=Depends(get_db)):
    if user_id is None:
        raise HTTPException(status_code=400, detail="user_id required")

    conn, cur = db
    start_date = get_date_filter(range)
    clause, extra_params = build_date_clause(start_date)

    try:
        cur.execute(
            f"""
            SELECT COALESCE(SUM(amount),0),
                   COALESCE(SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END),0),
                   COALESCE(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END),0)
            FROM transactions
            WHERE user_id = %s{clause}
            """,
            (user_id,) + extra_params,
        )
        total, expense, income = cur.fetchone()

        total = safe_number(total)
        expense = abs(safe_number(expense))
        income = safe_number(income)
        savings_pct = (total / income * 100) if income > 0 else 0

        return {
            "total": total,
            "total_expense": expense,
            "total_income": income,
            "savings_percent": round(savings_pct, 1),
        }

    except Exception:
        logger.exception("Summary failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/transactions", response_model=List[TransactionOut])
def get_transactions(user_id: int, db=Depends(get_db)):
    if user_id is None:
        raise HTTPException(status_code=400, detail="user_id required")

    conn, cur = db

    try:
        cur.execute(
            """
            SELECT date, description, amount, category
            FROM transactions
            WHERE user_id = %s
            ORDER BY date DESC
            """,
            (user_id,),
        )
        rows = cur.fetchall()

        return [
            {
                "date": str(row[0]),
                "description": row[1],
                "amount": row[2],
                "category": row[3],
            }
            for row in rows
        ]

    except Exception:
        logger.exception("Transactions fetch failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/category-breakdown", response_model=List[CategoryOut])
def category_breakdown(
    range: str = "all",
    user_id: int = None,
    db=Depends(get_db),
):
    if user_id is None:
        raise HTTPException(status_code=400, detail="user_id required")

    conn, cur = db
    start_date = get_date_filter(range)
    clause, extra_params = build_date_clause(start_date)

    try:
        cur.execute(
            f"""
            SELECT category, ABS(SUM(amount)) AS total
            FROM transactions
            WHERE user_id = %s AND amount < 0{clause}
            GROUP BY category
            ORDER BY total DESC
            """,
            (user_id,) + extra_params,
        )
        rows = cur.fetchall()

        return [{"category": row[0], "total": safe_number(row[1])} for row in rows]

    except Exception:
        logger.exception("Category breakdown failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/monthly-trend", response_model=List[TrendOut])
def monthly_trend(range: str = "all", user_id: int = None, db=Depends(get_db)):
    if user_id is None:
        raise HTTPException(status_code=400, detail="user_id required")

    conn, cur = db
    start_date = get_date_filter(range)
    clause, extra_params = build_date_clause(start_date)

    try:
        cur.execute(
            f"""
            SELECT TO_CHAR(DATE_TRUNC('month', date), 'Mon-YY') AS month,
                   SUM(amount) AS total
            FROM transactions
            WHERE user_id = %s{clause}
            GROUP BY DATE_TRUNC('month', date)
            ORDER BY DATE_TRUNC('month', date)
            """,
            (user_id,) + extra_params,
        )
        rows = cur.fetchall()

        return [{"month": row[0], "total": safe_number(row[1])} for row in rows]

    except Exception:
        logger.exception("Monthly trend failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/insights", response_model=InsightResponse)
def get_insights(range: str = "all", user_id: int = None, db=Depends(get_db)):
    if user_id is None:
        raise HTTPException(status_code=400, detail="user_id required")

    conn, cur = db
    start_date = get_date_filter(range)
    clause, extra_params = build_date_clause(start_date)
    params = (user_id,) + extra_params

    try:
        insights = []

        cur.execute(
            f"""
            SELECT COALESCE(SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END),0),
                   COALESCE(ABS(SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END)),0)
            FROM transactions
            WHERE user_id = %s{clause}
            """,
            params,
        )
        income, expense = cur.fetchone()
        income = safe_number(income)
        expense = safe_number(expense)

        if income > 0:
            savings = income - expense
            ratio = (savings / income) * 100

            if savings >= 0:
                insights.append(
                    f"You saved Rs.{savings:,.0f} this period ({ratio:.1f}% of income)"
                )
            else:
                insights.append(f"Warning: Overspent by Rs.{abs(savings):,.0f}")

            spend_ratio = (expense / income) * 100
            if spend_ratio > 80:
                insights.append("Warning: You are spending more than 80% of income")

        cur.execute(
            f"""
            SELECT category, ABS(SUM(amount)) AS total
            FROM transactions
            WHERE user_id = %s{clause} AND amount < 0
            GROUP BY category
            ORDER BY total DESC
            LIMIT 1
            """,
            params,
        )
        row = cur.fetchone()
        if row:
            insights.append(f"Highest expense category is {row[0]} (Rs.{row[1]:,.0f})")

        cur.execute(
            f"""
            SELECT description, ABS(SUM(amount)) AS total
            FROM transactions
            WHERE user_id = %s{clause} AND amount < 0
            GROUP BY description
            ORDER BY total DESC
            LIMIT 3
            """,
            params,
        )
        rows = cur.fetchall()
        if rows:
            names = [row[0] for row in rows]
            insights.append(f"Top spending merchants: {', '.join(names)}")

        cur.execute(
            f"""
            SELECT description, ABS(amount)
            FROM transactions
            WHERE user_id = %s{clause} AND amount < 0
            ORDER BY ABS(amount) DESC
            LIMIT 1
            """,
            params,
        )
        row = cur.fetchone()
        if row:
            insights.append(f"Largest single expense: {row[0]} (Rs.{row[1]:,.0f})")

        cur.execute(
            f"""
            SELECT COALESCE(ABS(SUM(amount)),0)
            FROM transactions
            WHERE user_id = %s{clause}
            AND amount < 0
            AND EXTRACT(DOW FROM date) IN (0,6)
            """,
            params,
        )
        row = cur.fetchone()
        weekend = safe_number(row[0]) if row else 0

        if expense > 0:
            pct = (weekend / expense) * 100
            if pct > 35:
                insights.append(f"Weekend spending is high ({pct:.1f}% of expenses)")

        cur.execute(
            f"""
            SELECT AVG(month_total)
            FROM (
                SELECT ABS(SUM(amount)) AS month_total
                FROM transactions
                WHERE user_id = %s{clause}
                AND amount < 0
                GROUP BY DATE_TRUNC('month', date)
            ) t
            """,
            params,
        )
        row = cur.fetchone()
        avg_month = safe_number(row[0]) if row else 0

        if avg_month > 0:
            insights.append(f"Average monthly spend: Rs.{avg_month:,.0f}")

        if not insights:
            insights.append("No insights available for selected period")

        return {"insights": insights}

    except Exception:
        logger.exception("Insights generation failed")
        raise HTTPException(status_code=500, detail="Internal server error")
