import hashlib
from html import escape
import logging
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from math import isfinite
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from typing import Annotated, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import bcrypt
import jwt
import pandas as pd
import pdfplumber
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from psycopg2.extras import execute_values

import resend

from db import get_db_connection
from analytics import detect_unusual_spending, monthly_spending_forecast
from research_evaluation import evaluate_anomaly_detection, evaluate_categorization

# -------------------------------
# Configuration
# -------------------------------
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:8501,http://127.0.0.1:8501",
    ).split(",")
    if origin.strip()
]

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

MAX_UPLOAD_SIZE_BYTES = (
    int(os.getenv("MAX_UPLOAD_SIZE_MB", "10")) * 1024 * 1024
)
MAX_UPLOAD_ROWS = int(os.getenv("MAX_UPLOAD_ROWS", "5000"))

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRATION_MINUTES = int(
    os.getenv("JWT_EXPIRATION_MINUTES", "60")
)
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL")
PASSWORD_RESET_URL = os.getenv("PASSWORD_RESET_URL")

if not JWT_SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY environment variable is required")

bearer_scheme = HTTPBearer(auto_error=False)

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
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------
# Pydantic Models
# -------------------------------
class SignupRequest(BaseModel):
    model_config = {"extra": "forbid"}

    name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str
    
class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class StatusMessage(BaseModel):
    message: str


class SignupResponse(BaseModel):
    status: str
    user_id: int


class LoginResponse(BaseModel):
    status: str
    user_id: int
    name: str
    access_token: str


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
    rows_skipped: Optional[int] = None
    preview: Optional[str] = None
    transactions: Optional[List[dict]] = None


class InsightResponse(BaseModel):
    insights: List[str]


class DashboardTrendOut(BaseModel):
    month: str
    total: float
    income: float
    expenses: float


class DashboardOverviewOut(BaseModel):
    summary: SummaryOut
    categories: List[CategoryOut]
    trend: List[DashboardTrendOut]
    insights: InsightResponse
    transactions: List[TransactionOut]
    transactions_total: int
    available_categories: List[str]
    forecast: dict
    anomalies: dict
    research_evaluation: dict


class MeOut(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool


class AdminOverviewOut(BaseModel):
    total_users: int
    active_users: int
    inactive_users: int
    admin_users: int
    total_transactions: int


class AdminUserOut(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool


class AdminUsersOut(BaseModel):
    users: List[AdminUserOut]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    user_id: int
    name: str
    email: str
    role: str
    is_active: bool


# -------------------------------
# Database Dependency
# -------------------------------
def _request_connection(request: Request):
    conn = getattr(request.state, "db_connection", None)
    created = conn is None
    if created:
        conn = get_db_connection()
        request.state.db_connection = conn
    return conn, created


def get_db(request: Request):
    conn, _created = _request_connection(request)
    cursor = None
    try:
        cursor = conn.cursor()
        yield conn, cursor
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            try:
                conn.close()
            finally:
                request.state.db_connection = None


# -------------------------------
# Database schema readiness
# -------------------------------
def validate_transaction_ownership_schema(conn):
    """Fail startup unless the active schema enforces transaction ownership.

    The versioned migration is deliberately separate from application startup.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT is_nullable, data_type
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'transactions'
              AND column_name = 'user_id'
            """
        )
        ownership_column = cur.fetchone()
        if ownership_column is None:
            raise RuntimeError(
                "Database schema is missing transactions.user_id. Create the base "
                "tables and apply the transaction-ownership migration before startup."
            )
        nullable, data_type = ownership_column
        if nullable != "NO" or data_type != "integer":
            raise RuntimeError(
                "Database schema requires transactions.user_id to be a non-null "
                "INTEGER. Apply the transaction-ownership migration before startup."
            )

        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_constraint AS ownership_fk
                JOIN pg_class AS transaction_table
                  ON transaction_table.oid = ownership_fk.conrelid
                JOIN pg_namespace AS transaction_schema
                  ON transaction_schema.oid = transaction_table.relnamespace
                JOIN pg_class AS user_table
                  ON user_table.oid = ownership_fk.confrelid
                JOIN pg_namespace AS user_schema
                  ON user_schema.oid = user_table.relnamespace
                JOIN pg_attribute AS transaction_user_id
                  ON transaction_user_id.attrelid = transaction_table.oid
                 AND transaction_user_id.attname = 'user_id'
                 AND transaction_user_id.attnum = ANY(ownership_fk.conkey)
                JOIN pg_attribute AS user_id
                  ON user_id.attrelid = user_table.oid
                 AND user_id.attname = 'id'
                 AND user_id.attnum = ANY(ownership_fk.confkey)
                WHERE ownership_fk.contype = 'f'
                  AND ownership_fk.convalidated
                  AND ownership_fk.confdeltype = 'c'
                  AND ownership_fk.conkey = ARRAY[transaction_user_id.attnum]::smallint[]
                  AND ownership_fk.confkey = ARRAY[user_id.attnum]::smallint[]
                  AND transaction_schema.nspname = current_schema()
                  AND transaction_table.relname = 'transactions'
                  AND user_schema.nspname = current_schema()
                  AND user_table.relname = 'users'
            )
            """
        )
        ownership_fk_exists = cur.fetchone()

    if not ownership_fk_exists or not ownership_fk_exists[0]:
        raise RuntimeError(
            "Database schema is missing the validated transactions.user_id -> "
            "users.id ON DELETE CASCADE foreign key. Apply the transaction-ownership "
            "migration before startup."
        )


def validate_user_role_schema(conn):
    """Require the explicit user-role migration before serving requests."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, is_nullable, data_type, column_default
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'users'
              AND column_name IN ('role', 'is_active')
            """
        )
        columns = {row[0]: row[1:] for row in cur.fetchall()}
        role = columns.get("role")
        active = columns.get("is_active")
        role_default = re.sub(r"\s+|::text|[()']", "", str(role[2]).lower()) if role else ""
        active_default = re.sub(r"\s+|::boolean|[()]", "", str(active[2]).lower()) if active else ""
        if role is None or role[0] != "NO" or role[1] != "text" or role_default != "user":
            raise RuntimeError(
                "Database schema requires users.role as non-null TEXT with a user default. "
                "Apply the user-role migration before startup."
            )
        if active is None or active[0] != "NO" or active[1] != "boolean" or active_default != "true":
            raise RuntimeError(
                "Database schema requires users.is_active as non-null BOOLEAN with a true default. "
                "Apply the user-role migration before startup."
            )

        cur.execute("SELECT count(*) FROM users WHERE role NOT IN ('user', 'admin') OR role IS NULL OR is_active IS NULL")
        invalid_data = cur.fetchone()
        if not invalid_data or invalid_data[0]:
            raise RuntimeError("Database contains invalid user roles or activation states; repair them before startup.")

        cur.execute(
            """
            SELECT pg_get_constraintdef(role_constraint.oid), role_constraint.convalidated
            FROM pg_constraint AS role_constraint
            JOIN pg_class AS user_table ON user_table.oid = role_constraint.conrelid
            JOIN pg_namespace AS user_schema ON user_schema.oid = user_table.relnamespace
            WHERE role_constraint.contype = 'c'
              AND user_schema.nspname = current_schema()
              AND user_table.relname = 'users'
              AND pg_get_constraintdef(role_constraint.oid) ILIKE '%role%'
            """
        )
        role_constraints = cur.fetchall()

    def equivalent_role_check(definition):
        compact = re.sub(r"\s+|::text|\"", "", str(definition).lower())
        return compact in {
            "check((role=any(array['user','admin'])))",
            "check((role=any(array['admin','user'])))",
        }

    if not role_constraints or not any(equivalent_role_check(row[0]) and row[1] for row in role_constraints):
        raise RuntimeError(
            "Database schema is missing a validated users.role check limited to user/admin. "
            "Apply the user-role migration before startup."
        )
    if any(not equivalent_role_check(row[0]) for row in role_constraints):
        raise RuntimeError("Database schema has a conflicting users.role constraint.")


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
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user' CONSTRAINT users_role_allowed_check CHECK (role IN ('user', 'admin')),
                    is_active BOOLEAN NOT NULL DEFAULT TRUE
                );
                """
            )

            cur.execute(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token_hash TEXT NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    used_at TIMESTAMPTZ NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_token_hash
                ON password_reset_tokens(token_hash);
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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

        validate_transaction_ownership_schema(conn)
        validate_user_role_schema(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    logger.info("Database tables and transaction ownership constraints verified.")
# -------------------------------
# Helpers
# -------------------------------
def safe_number(val):
    return float(val) if val is not None else 0.0


def bulk_insert_transactions(cur, transactions):
    if not transactions:
        return
    execute_values(
        cur,
        """
        INSERT INTO transactions (user_id, date, description, amount, category)
        VALUES %s
        """,
        transactions,
        page_size=1000,
    )


def validate_required_text(value: str, field_name: str, min_length: int = 1):
    if not value or len(value.strip()) < min_length:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be at least {min_length} character(s)",
        )


def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="A valid email address is required")
    return email


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
        amount = float(cleaned)
        return amount if isfinite(amount) else None
    except ValueError:
        return None


def add_pdf_transaction(transactions, date_str, description, amount):
    if not date_str or not description or amount is None:
        return

    key = (str(date_str).strip(), str(description).strip(), round(float(amount), 2))
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


def extract_pdf_transactions(pdf_bytes):
    """Extract supported statement rows, converting parser failures to safe client errors."""
    extracted_text = ""
    transactions = []
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    extracted_text += text + "\n"
                    for date_str, description, amount in parse_pdf_text_transactions(text):
                        add_pdf_transaction(transactions, date_str, description, amount)
                tables = page.extract_tables()
                for table in tables:
                    if not table:
                        continue
                    for date_str, description, amount in parse_pdf_table_transactions(table):
                        add_pdf_transaction(transactions, date_str, description, amount)
    except Exception:
        logger.info("Uploaded PDF could not be parsed")
        raise HTTPException(
            status_code=422,
            detail="PDF could not be read. Upload a valid text-based statement or CSV file.",
        ) from None
    return extracted_text, transactions


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
    
def generate_password_reset_token() -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()

    return raw_token, token_hash


PASSWORD_RESET_GENERIC_MESSAGE = (
    "If an account exists for this email, a password reset link has been sent."
)
# Match the token lifetime so a later request cannot invalidate a still-valid
# link by replacing it during its 15-minute validity window.
PASSWORD_RESET_COOLDOWN_SECONDS = 15 * 60
PASSWORD_RESET_LOCK_NAMESPACE = 0x455850


def build_password_reset_url(raw_token: str) -> str:
    parsed = urlsplit(PASSWORD_RESET_URL)
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    is_secure = parsed.scheme == "https" or (
        parsed.scheme == "http" and parsed.hostname in local_hosts
    )
    if not is_secure or not parsed.netloc or parsed.username or parsed.password:
        raise RuntimeError("PASSWORD_RESET_URL must be HTTPS, except for local development")

    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "reset_token"]
    query.append(("reset_token", raw_token))
    return urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        urlencode(query),
        parsed.fragment,
    ))


def send_password_reset_email(recipient: str, raw_token: str) -> None:
    if not RESEND_API_KEY or not RESEND_FROM_EMAIL or not PASSWORD_RESET_URL:
        raise RuntimeError("Password reset email configuration is incomplete")

    reset_url = build_password_reset_url(raw_token)
    resend.api_key = RESEND_API_KEY
    resend.Emails.send({
        "from": RESEND_FROM_EMAIL,
        "to": [recipient],
        "subject": "Reset your Expense Intelligence password",
        "html": (
            "<p>We received a request to reset your password.</p>"
            f'<p><a href="{escape(reset_url, quote=True)}">Reset your password</a></p>'
            "<p>This link expires in 15 minutes. If you did not request it, "
            "you can ignore this email.</p>"
        ),
        "text": (
            "We received a request to reset your password.\n\n"
            f"Reset your password: {reset_url}\n\n"
            "This link expires in 15 minutes. If you did not request it, "
            "you can ignore this email."
        ),
    })


def finalize_password_reset_email_attempt(
    user_id: int,
    token_hash: str,
    delivered: bool,
) -> None:
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if delivered:
            cursor.execute(
                """
                UPDATE password_reset_tokens
                SET used_at = NOW()
                WHERE user_id = %s
                  AND token_hash <> %s
                  AND used_at IS NULL
                """,
                (user_id, token_hash),
            )
        else:
            cursor.execute(
                """
                UPDATE password_reset_tokens
                SET used_at = NOW()
                WHERE token_hash = %s
                  AND used_at IS NULL
                """,
                (token_hash,),
            )
        conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        logger.warning("Password reset token finalization failed")
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def deliver_password_reset_email(
    recipient: str,
    raw_token: str,
    user_id: int,
    token_hash: str,
) -> None:
    try:
        send_password_reset_email(recipient, raw_token)
    except Exception:
        # The cooldown attempt is already committed. Mark only this newly
        # created token unusable, leaving any previously delivered link intact.
        logger.warning("Password reset email delivery failed")
        finalize_password_reset_email_attempt(user_id, token_hash, delivered=False)
        return

    finalize_password_reset_email_attempt(user_id, token_hash, delivered=True)

def create_access_token(user_id: int, token_version: Optional[int] = None) -> str:
    if token_version is None:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT token_version FROM users WHERE id = %s", (user_id,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=401, detail="User not found")
                token_version = row[0]
        finally:
            conn.close()

    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=JWT_EXPIRATION_MINUTES
    )

    payload = {
        "sub": str(user_id),
        "token_version": token_version,
        "exp": expires_at,
    }

    return jwt.encode(
        payload,
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
    
def _authenticate_principal(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials],
) -> AuthenticatedPrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer authentication is required", headers={"WWW-Authenticate": "Bearer"})
    token = credentials.credentials

    try:
        payload = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub", "token_version"]},
        )

        user_id = payload.get("sub")
        token_version = payload.get("token_version")

        if (
            not isinstance(user_id, str)
            or not user_id.isdecimal()
            or not isinstance(token_version, int)
            or isinstance(token_version, bool)
            or token_version < 0
        ):
            raise HTTPException(
                status_code=401,
                detail="Invalid authentication token",
            )

        user_id = int(user_id)
        conn, created_for_auth = _request_connection(request)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT token_version, role, is_active, name, email
                    FROM users
                    WHERE id = %s
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
        except Exception:
            if created_for_auth:
                try:
                    conn.rollback()
                finally:
                    conn.close()
                    request.state.db_connection = None
            raise

        def close_failed_auth_connection():
            if created_for_auth:
                try:
                    conn.rollback()
                finally:
                    conn.close()
                    request.state.db_connection = None

        if not row:
            close_failed_auth_connection()
            raise HTTPException(status_code=401, detail="Invalid authentication token")
        current_version, role, is_active, name, email = row
        if token_version != current_version:
            close_failed_auth_connection()
            raise HTTPException(status_code=401, detail="Authentication token has been invalidated")
        if is_active is not True:
            close_failed_auth_connection()
            raise HTTPException(status_code=401, detail="Account is inactive")
        if role not in {"user", "admin"}:
            close_failed_auth_connection()
            raise HTTPException(status_code=401, detail="Account role is invalid")
        return AuthenticatedPrincipal(user_id, name, email, role, is_active)

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Authentication token has expired",
        )

    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token",
        )


def get_current_principal(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> AuthenticatedPrincipal:
    return _authenticate_principal(request, credentials)


def get_current_user_id(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> int:
    """Compatibility dependency for existing user-scoped handlers."""
    return _authenticate_principal(request, credentials).user_id


def require_admin(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
) -> AuthenticatedPrincipal:
    if principal.role != "admin":
        conn = getattr(request.state, "db_connection", None)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                logger.warning("Database rollback failed while rejecting non-admin access")
            try:
                conn.close()
            except Exception:
                logger.warning("Database close failed while rejecting non-admin access")
            request.state.db_connection = None
        raise HTTPException(status_code=403, detail="Administrator access is required")
    return principal


def set_user_role(cur, user_id: int, role: str):
    """Update a user's role and invalidate every existing session."""
    if role not in {"user", "admin"}:
        raise ValueError("role must be user or admin")
    cur.execute(
        "UPDATE users SET role = %s, token_version = token_version + 1 WHERE id = %s RETURNING id",
        (role, user_id),
    )
    return cur.fetchone()


def set_user_active_status(cur, user_id: int, is_active: bool):
    """Update account activation and invalidate every existing session."""
    if not isinstance(is_active, bool):
        raise ValueError("is_active must be a boolean")
    cur.execute(
        "UPDATE users SET is_active = %s, token_version = token_version + 1 WHERE id = %s RETURNING id",
        (is_active, user_id),
    )
    return cur.fetchone()


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
    name = user.name.strip()
    email = normalize_email(user.email)
    validate_required_text(name, "Name", min_length=2)
    validate_required_text(user.password, "Password", min_length=6)

    try:
        hashed_pw = hash_password(user.password)
        cur.execute(
            """
            INSERT INTO users (name, email, password_hash)
            VALUES (%s, %s, %s) RETURNING id
            """,
            (name, email, hashed_pw),
        )
        user_id = cur.fetchone()[0]
        conn.commit()

        logger.info("New user signed up: %s (id=%s)", email, user_id)
        return {"status": "success", "user_id": user_id}

    except HTTPException:
        raise

    except Exception as exc:
        conn.rollback()
        if getattr(exc, "pgcode", None) == "23505":
            raise HTTPException(status_code=409, detail="Email already registered")
        logger.exception("Signup failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/login", response_model=LoginResponse)
def login(user: LoginRequest, db=Depends(get_db)):
    conn, cur = db
    email = normalize_email(user.email)
    validate_required_text(user.password, "Password")

    try:
        cur.execute(
            "SELECT id, name, password_hash, is_active, token_version FROM users WHERE email = %s",
            (email,),
        )
        row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=401, detail="Invalid email or password")

        user_id, name, stored_hash, is_active, token_version = row

        if is_active is not True:
            raise HTTPException(status_code=401, detail="Invalid email or password")

        if not verify_password(user.password, stored_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")

        logger.info("User logged in: %s", email)
        access_token = create_access_token(user_id, token_version)
        return {"status": "success", "user_id": user_id, "name": name, "access_token": access_token}

    except HTTPException:
        raise

    except Exception:
        logger.exception("Login failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/logout", response_model=StatusMessage)
def logout(user_id: int = Depends(get_current_user_id), db=Depends(get_db)):
    conn, cur = db
    try:
        cur.execute(
            """
            UPDATE users
            SET token_version = token_version + 1
            WHERE id = %s
            RETURNING id
            """,
            (user_id,),
        )
        if not cur.fetchone():
            conn.rollback()
            raise HTTPException(status_code=401, detail="User no longer exists")

        conn.commit()
        return {"message": "Logged out; server-side sessions have been revoked."}
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        logger.exception("Server-side logout failed for user %s", user_id)
        raise HTTPException(
            status_code=500,
            detail="Server-side logout failed; the token may remain valid.",
        )


@app.get("/me", response_model=MeOut)
def get_me(
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    _db=Depends(get_db),
):
    return {
        "id": principal.user_id,
        "name": principal.name,
        "email": principal.email,
        "role": principal.role,
        "is_active": principal.is_active,
    }


@app.get("/admin/overview", response_model=AdminOverviewOut)
def admin_overview(
    _principal: AuthenticatedPrincipal = Depends(require_admin),
    db=Depends(get_db),
):
    _conn, cur = db
    try:
        cur.execute(
            """
            SELECT
                (SELECT count(*) FROM users),
                (SELECT count(*) FROM users WHERE is_active = TRUE),
                (SELECT count(*) FROM users WHERE is_active = FALSE),
                (SELECT count(*) FROM users WHERE role = 'admin'),
                (SELECT count(*) FROM transactions)
            """
        )
        total_users, active_users, inactive_users, admin_users, total_transactions = cur.fetchone()
        return {
            "total_users": total_users,
            "active_users": active_users,
            "inactive_users": inactive_users,
            "admin_users": admin_users,
            "total_transactions": total_transactions,
        }
    except Exception:
        logger.exception("Admin overview aggregation failed")
        raise HTTPException(status_code=500, detail="Admin overview could not be loaded")


@app.get("/admin/users", response_model=AdminUsersOut)
def admin_users(
    search: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    _principal: AuthenticatedPrincipal = Depends(require_admin),
    db=Depends(get_db),
):
    _conn, cur = db
    try:
        where = ""
        params = ()
        if search and search.strip():
            where = " WHERE name ILIKE %s OR email ILIKE %s"
            pattern = f"%{search.strip()}%"
            params = (pattern, pattern)

        cur.execute("SELECT count(*) FROM users" + where, params)
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT id, name, email, role, is_active FROM users"
            + where
            + " ORDER BY id ASC LIMIT %s OFFSET %s",
            params + (limit, offset),
        )
        users = [
            {"id": row[0], "name": row[1], "email": row[2], "role": row[3], "is_active": row[4]}
            for row in cur.fetchall()
        ]
        return {"users": users, "total": total, "limit": limit, "offset": offset}
    except Exception:
        logger.exception("Admin user listing failed")
        raise HTTPException(status_code=500, detail="Admin user listing could not be loaded")

# -------------------------------
# Password Reset
# -------------------------------
@app.post("/forgot-password", response_model=StatusMessage)
def forgot_password(
    request: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db=Depends(get_db),
):
    conn, cur = db
    email = normalize_email(request.email)

    try:
        cur.execute(
            "SELECT id FROM users WHERE email = %s",
            (email,),
        )
        row = cur.fetchone()

        if not row:
            return {"message": PASSWORD_RESET_GENERIC_MESSAGE}

        user_id = row[0]

        # Serialize reset requests for this user without taking the users row
        # lock: reset_password locks the token row before updating users.
        cur.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            (PASSWORD_RESET_LOCK_NAMESPACE, user_id),
        )

        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM password_reset_tokens
                WHERE user_id = %s
                  AND (
                      created_at > NOW() - (%s * INTERVAL '1 second')
                      OR (used_at IS NULL AND expires_at > NOW())
                  )
            )
            """,
            (user_id, PASSWORD_RESET_COOLDOWN_SECONDS),
        )
        recent_request = cur.fetchone()
        if recent_request and recent_request[0]:
            # Commit to release the transaction-scoped advisory lock.
            # In particular, leave any previously delivered reset link valid.
            conn.commit()
            return {"message": PASSWORD_RESET_GENERIC_MESSAGE}

        raw_token, token_hash = generate_password_reset_token()

        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)

        cur.execute(
            """
            INSERT INTO password_reset_tokens (
                user_id,
                token_hash,
                expires_at
            )
            VALUES (%s, %s, %s)
            """,
            (user_id, token_hash, expires_at),
        )
        # Commit the attempt before delivery so provider failures cannot roll
        # back the per-account cooldown. The raw token is never stored.
        conn.commit()
        background_tasks.add_task(
            deliver_password_reset_email,
            email,
            raw_token,
            user_id,
            token_hash,
        )

        return {"message": PASSWORD_RESET_GENERIC_MESSAGE}

    except Exception:
        conn.rollback()
        logger.exception("Forgot-password request failed")
        raise HTTPException(
            status_code=500,
            detail="Internal server error",
        )

# -------------------------------
# Reset Password
# -------------------------------
@app.post("/reset-password", response_model=StatusMessage)
def reset_password(
    request: ResetPasswordRequest,
    db=Depends(get_db),
):
    conn, cur = db

    try:
        validate_required_text(
            request.token,
            "Reset token",
        )

        validate_required_text(
            request.new_password,
            "Password",
            min_length=8,
        )

        token_hash = hashlib.sha256(
            request.token.encode("utf-8")
        ).hexdigest()

        # Claim and consume in one statement. PostgreSQL row locking ensures
        # concurrent requests cannot both claim the same unused token.
        cur.execute(
            """
            UPDATE password_reset_tokens
            SET used_at = NOW()
            WHERE token_hash = %s
              AND used_at IS NULL
              AND expires_at > NOW()
            RETURNING id, user_id
            """,
            (token_hash,),
        )

        row = cur.fetchone()

        if not row:
            raise HTTPException(
                status_code=400,
                detail="Invalid or expired password reset token",
            )

        _reset_token_id, user_id = row

        new_password_hash = hash_password(
            request.new_password
        )

        cur.execute(
            """
            UPDATE users
            SET password_hash = %s,
                token_version = token_version + 1
            WHERE id = %s
            """,
            (new_password_hash, user_id),
        )

        conn.commit()

        logger.info(
            "Password reset completed for user_id=%s",
            user_id,
        )

        return {
            "message": "Password has been reset successfully."
        }

    except HTTPException:
        conn.rollback()
        raise

    except Exception:
        conn.rollback()
        logger.exception("Password reset failed")
        raise HTTPException(
            status_code=500,
            detail="Internal server error",
        )

# -------------------------------
# File Upload
# -------------------------------
@app.post("/upload", response_model=UploadResponse)
async def upload_file(
    user_id: int = Depends(get_current_user_id),
    file: UploadFile = File(...),
    db=Depends(get_db),
):
    conn, cur = db
    logger.info("Upload request for authenticated user %s", user_id)

    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="Uploaded file must have a name")

        filename = file.filename.lower()

        upload_bytes = await file.read(MAX_UPLOAD_SIZE_BYTES + 1)
        if len(upload_bytes) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(status_code=413, detail="File exceeds the configured upload size limit")

        if filename.endswith(".csv"):
            try:
                df = pd.read_csv(BytesIO(upload_bytes))
            except Exception:
                raise HTTPException(status_code=400, detail="CSV could not be read; check its format and encoding")
            df.columns = df.columns.str.strip().str.lower()
            if df.columns.duplicated().any():
                raise HTTPException(
                    status_code=400,
                    detail="CSV contains duplicate column names after trimming spaces and ignoring case.",
                )

            required_cols = ["date", "description", "amount"]
            for col in required_cols:
                if col not in df.columns:
                    raise HTTPException(status_code=400, detail=f"Missing column: {col}")

            if len(df) > MAX_UPLOAD_ROWS:
                raise HTTPException(status_code=413, detail=f"CSV exceeds the {MAX_UPLOAD_ROWS}-row limit")
            source_rows = len(df)
            df["description"] = df["description"].fillna("").astype(str).str.strip()
            df["date"] = pd.to_datetime(df["date"], errors="coerce", format="%Y-%m-%d")
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
            df = df[df["amount"].map(lambda amount: pd.notna(amount) and isfinite(float(amount)))]
            df = df.dropna(subset=["date", "amount"])
            df = df[df["description"].str.len() > 0]
            rows_skipped = source_rows - len(df)
            if df.empty:
                raise HTTPException(status_code=422, detail="No valid transactions were found in this CSV")

            records = []
            date_index = df.columns.get_loc("date")
            description_index = df.columns.get_loc("description")
            amount_index = df.columns.get_loc("amount")
            for row in df.itertuples(index=False, name=None):
                txn_date = row[date_index]
                if isinstance(txn_date, pd.Timestamp):
                    txn_date = txn_date.date()
                description = str(row[description_index])
                records.append(
                    (
                        user_id,
                        txn_date,
                        description,
                        float(row[amount_index]),
                        categorize(description),
                    )
                )

            cur.execute("DELETE FROM transactions WHERE user_id = %s", (user_id,))
            bulk_insert_transactions(cur, records)

            conn.commit()
            logger.info("CSV uploaded for user %s: %s rows", user_id, len(df))
            return {"status": "success", "rows_loaded": len(df), "rows_skipped": rows_skipped}

        if filename.endswith(".pdf"):
            extracted_text, transactions = extract_pdf_transactions(upload_bytes)

            if not transactions:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "No transactions were found in this PDF. "
                        "Please upload a text-based statement or CSV file."
                    ),
                )
            parsed_count = len(transactions)
            normalized_transactions = []
            for txn in transactions:
                parsed_date = pd.to_datetime(txn["date"], dayfirst=True, errors="coerce")
                description = str(txn["description"] or "").strip()
                if pd.isna(parsed_date) or not description:
                    continue
                txn["date"] = parsed_date.date()
                txn["description"] = description
                normalized_transactions.append(txn)
            transactions = normalized_transactions
            rows_skipped = parsed_count - len(transactions)
            if not transactions:
                raise HTTPException(status_code=422, detail="No transactions with valid dates and descriptions were found in this PDF")
            if len(transactions) > MAX_UPLOAD_ROWS:
                raise HTTPException(status_code=413, detail=f"PDF exceeds the {MAX_UPLOAD_ROWS}-transaction limit")

            records = [
                (
                    user_id,
                    txn["date"],
                    txn["description"],
                    float(txn["amount"]),
                    categorize(txn["description"]),
                )
                for txn in transactions
            ]

            cur.execute("DELETE FROM transactions WHERE user_id = %s", (user_id,))
            bulk_insert_transactions(cur, records)

            conn.commit()
            logger.info(
                "PDF uploaded for user %s: %s transactions",
                user_id,
                len(transactions),
            )
            return {
                "status": "success",
                "rows_loaded": len(transactions),
                "rows_skipped": rows_skipped,
                "preview": "text extracted" if extracted_text.strip() else "",
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
def delete_transactions(user_id: int = Depends(get_current_user_id), db=Depends(get_db)):
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
def delete_account(user_id: int = Depends(get_current_user_id), db=Depends(get_db)):
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
def get_summary(
    range: str = "all",
    user_id: int = Depends(get_current_user_id),
    db=Depends(get_db),
):
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
def get_transactions(
    response: Response,
    user_id: int = Depends(get_current_user_id),
    db=Depends(get_db),
    search: Optional[str] = None,
    category: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: Annotated[Optional[int], Query(ge=1, le=1000)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    conn, cur = db

    try:
        if date_from and date_to and date_from > date_to:
            raise HTTPException(status_code=400, detail="Start date must be on or before end date")

        where = ["user_id = %s"]
        params = [user_id]
        if search and search.strip():
            where.append("description ILIKE %s")
            params.append(f"%{search.strip()}%")
        if category:
            where.append("category = %s")
            params.append(category)
        if date_from:
            where.append("date >= %s")
            params.append(date_from)
        if date_to:
            where.append("date <= %s")
            params.append(date_to)

        sql = (
            "SELECT date, description, amount, category FROM transactions WHERE "
            + " AND ".join(where)
            + " ORDER BY date DESC, id DESC"
        )
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params.extend((limit + 1, offset))
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        if limit is not None:
            has_more = len(rows) > limit
            rows = rows[:limit]
            response.headers["X-Has-More"] = "true" if has_more else "false"

        return [
            {
                "date": str(row[0]),
                "description": row[1],
                "amount": row[2],
                "category": row[3],
            }
            for row in rows
        ]

    except HTTPException:
        raise

    except Exception:
        logger.exception("Transactions fetch failed")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/category-breakdown", response_model=List[CategoryOut])
def category_breakdown(
    range: str = "all",
    user_id: int = Depends(get_current_user_id),
    db=Depends(get_db),
):
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
def monthly_trend(
    range: str = "all",
    user_id: int = Depends(get_current_user_id),
    db=Depends(get_db),
):
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
def get_insights(
    range: str = "all",
    user_id: int = Depends(get_current_user_id),
    db=Depends(get_db),
):
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
                    f"You saved {savings:,.0f} amount units this period ({ratio:.1f}% of income)"
                )
            else:
                insights.append(f"Warning: Overspent by {abs(savings):,.0f} amount units")

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
            insights.append(f"Highest expense category is {row[0]} ({row[1]:,.0f} amount units)")

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
            insights.append(f"Largest single expense: {row[0]} ({row[1]:,.0f} amount units)")

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
            insights.append(f"Average monthly spend: {avg_month:,.0f} amount units")

        if not insights:
            insights.append("No insights available for selected period")

        return {"insights": insights}

    except Exception:
        logger.exception("Insights generation failed")
        raise HTTPException(status_code=500, detail="Internal server error")


def _date_value(value):
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def build_dashboard_overview(rows, range_value="all"):
    """Build existing dashboard sections from one user-scoped transaction read."""
    start_date = get_date_filter(range_value)
    filtered_rows = []
    for row in rows:
        row_date = _date_value(row[0])
        if start_date is None or (row_date is not None and row_date >= start_date):
            filtered_rows.append(row)

    total = sum(safe_number(row[2]) for row in filtered_rows)
    total_expense = sum(abs(safe_number(row[2])) for row in filtered_rows if row[2] is not None and row[2] < 0)
    total_income = sum(safe_number(row[2]) for row in filtered_rows if row[2] is not None and row[2] > 0)
    summary = {
        "total": total,
        "total_expense": total_expense,
        "total_income": total_income,
        "savings_percent": round(total / total_income * 100, 1) if total_income > 0 else 0,
    }

    category_totals = defaultdict(float)
    monthly_totals = defaultdict(float)
    monthly_income = defaultdict(float)
    monthly_expenses = defaultdict(float)
    description_totals = defaultdict(float)
    weekend_expense = 0.0
    for row in filtered_rows:
        row_date, description, raw_amount, category = row
        amount = safe_number(raw_amount)
        row_date = _date_value(row_date)
        if row_date is not None:
            monthly_totals[(row_date.year, row_date.month)] += amount
            if amount > 0:
                monthly_income[(row_date.year, row_date.month)] += amount
        if amount >= 0:
            continue
        expense = abs(amount)
        category_totals[category] += expense
        description_totals[description] += expense
        if row_date is not None:
            monthly_expenses[(row_date.year, row_date.month)] += expense
            if row_date.weekday() in (5, 6):
                weekend_expense += expense

    categories = [
        {"category": category, "total": amount}
        for category, amount in sorted(category_totals.items(), key=lambda item: item[1], reverse=True)
    ]
    trend = [
        {
            "month": date(year, month, 1).strftime("%b-%y"),
            "total": amount,
            "income": monthly_income[(year, month)],
            "expenses": monthly_expenses[(year, month)],
        }
        for (year, month), amount in sorted(monthly_totals.items())
    ]

    insights = []
    if total_income > 0:
        savings = total_income - total_expense
        ratio = savings / total_income * 100
        if savings >= 0:
            insights.append(f"You saved {savings:,.0f} amount units this period ({ratio:.1f}% of income)")
        else:
            insights.append(f"Warning: Overspent by {abs(savings):,.0f} amount units")
        if total_expense / total_income * 100 > 80:
            insights.append("Warning: You are spending more than 80% of income")

    if category_totals:
        category, amount = max(category_totals.items(), key=lambda item: item[1])
        insights.append(f"Highest expense category is {category} ({amount:,.0f} amount units)")
    if description_totals:
        top_descriptions = sorted(description_totals.items(), key=lambda item: item[1], reverse=True)[:3]
        insights.append(f"Top spending merchants: {', '.join(str(item[0]) for item in top_descriptions)}")

    largest = max(
        (row for row in filtered_rows if row[2] is not None and row[2] < 0),
        key=lambda row: abs(row[2]),
        default=None,
    )
    if largest:
        insights.append(f"Largest single expense: {largest[1]} ({abs(largest[2]):,.0f} amount units)")
    if total_expense > 0 and weekend_expense / total_expense * 100 > 35:
        insights.append(f"Weekend spending is high ({weekend_expense / total_expense * 100:.1f}% of expenses)")
    if monthly_expenses:
        average_month = sum(monthly_expenses.values()) / len(monthly_expenses)
        if average_month > 0:
            insights.append(f"Average monthly spend: {average_month:,.0f} amount units")
    if not insights:
        insights.append("No insights available for selected period")

    transaction_rows = [
        {
            "date": str(row[0]),
            "description": row[1],
            "amount": safe_number(row[2]),
            "category": row[3],
        }
        for row in rows
    ]
    forecast = monthly_spending_forecast(
        [(row[0], row[2]) for row in rows if row[2] is not None and row[2] < 0]
    )
    anomalies = detect_unusual_spending(
        [(row[0], row[1], row[3], row[2]) for row in rows if row[2] is not None and row[2] < 0]
    )
    return {
        "summary": summary,
        "categories": categories,
        "trend": trend,
        "insights": {"insights": insights},
        "transactions": transaction_rows[:10],
        "transactions_total": len(transaction_rows),
        "available_categories": sorted({str(row[3] or "Others") for row in rows}),
        "forecast": forecast,
        "anomalies": anomalies,
        "research_evaluation": get_research_evaluation_payload(),
    }


@app.get("/dashboard-overview", response_model=DashboardOverviewOut)
def dashboard_overview(
    range: str = "all",
    user_id: int = Depends(get_current_user_id),
    db=Depends(get_db),
):
    _conn, cur = db
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
        return build_dashboard_overview(cur.fetchall(), range)
    except Exception:
        logger.exception("Dashboard overview failed")
        raise HTTPException(status_code=500, detail="Dashboard overview could not be loaded")


@app.get("/forecast")
def expense_forecast(user_id: int = Depends(get_current_user_id), db=Depends(get_db)):
    conn, cur = db
    try:
        cur.execute("SELECT date, amount FROM transactions WHERE user_id = %s AND amount < 0 ORDER BY date", (user_id,))
        return monthly_spending_forecast(cur.fetchall())
    except Exception:
        logger.exception("Forecast calculation failed")
        raise HTTPException(status_code=500, detail="Forecast could not be calculated")


@app.get("/anomalies")
def unusual_spending(user_id: int = Depends(get_current_user_id), db=Depends(get_db)):
    conn, cur = db
    try:
        cur.execute("SELECT date, description, category, amount FROM transactions WHERE user_id = %s AND amount < 0 ORDER BY date", (user_id,))
        return detect_unusual_spending(cur.fetchall())
    except Exception:
        logger.exception("Anomaly analysis failed")
        raise HTTPException(status_code=500, detail="Unusual spending analysis could not be calculated")


@lru_cache(maxsize=1)
def get_research_evaluation_payload():
    """Compute fixed, user-independent synthetic metrics once per process."""
    return {
        "categorization": evaluate_categorization(categorize),
        "anomaly_detection": evaluate_anomaly_detection(),
        "data_scope": "Synthetic evaluation examples only; no user transactions are scored.",
    }


@app.get("/research-evaluation")
def research_evaluation(user_id: int = Depends(get_current_user_id), db=Depends(get_db)):
    """Return reproducible synthetic-data evaluation results for the demo."""
    return get_research_evaluation_payload()
