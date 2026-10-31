# Expense Intelligence Platform

A full-stack personal-finance application for uploading bank statements, tracking spending, and generating practical insights.

## Features

- Secure signup and login with bcrypt password hashing
- CSV and text-based PDF statement upload
- Automatic transaction categorization for common banking merchants and payment types
- Dashboard KPIs for balance, income, and expenses
- Plotly category, distribution, and monthly cashflow charts
- Date, category, and transaction-description filters
- Insights for savings, spending ratio, major categories, merchants, weekend spending, and monthly averages
- CSV exports and account-management controls

## Technology

- Frontend: Streamlit
- Backend: FastAPI
- Database: PostgreSQL
- Data processing: pandas and pdfplumber
- Visualization: Plotly

## Project Structure

```text
.
|-- dashboard.py       # Streamlit frontend
|-- main.py            # FastAPI backend
|-- db.py              # PostgreSQL connection helper
|-- requirements.txt   # Python dependencies
|-- .env.example       # Environment variable template
`-- README.md
```

## Configuration

Copy `.env.example` to `.env` and fill in the database values:

```text
DB_NAME=expense_intelligence_dev
DB_USER=your_database_user
DB_PASSWORD=your_database_password
DB_HOST=your_database_host
DB_PORT=5432
DB_SSLMODE=require
ALLOWED_ORIGINS=http://localhost:8501,http://127.0.0.1:8501
LOG_LEVEL=INFO
MAX_UPLOAD_SIZE_MB=10
MAX_UPLOAD_ROWS=5000
EXPENSE_API_BASE_URL=http://127.0.0.1:8000
EXPENSE_DEPLOYMENT_ENV=development
JWT_SECRET_KEY=generate_a_private_random_secret
JWT_ALGORITHM=HS256
JWT_EXPIRATION_MINUTES=60
RESEND_API_KEY=your_resend_api_key
RESEND_FROM_EMAIL=your_verified_sender@example.com
PASSWORD_RESET_URL=http://localhost:8501
```

`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_PORT` are required by the backend. Use a separate local database (for example, `expense_intelligence_dev`) and a dedicated local PostgreSQL role; do not point local development at production. Set a strong private `JWT_SECRET_KEY`, `JWT_ALGORITHM`, and `JWT_EXPIRATION_MINUTES` for application use. `MAX_UPLOAD_SIZE_MB` and `MAX_UPLOAD_ROWS` limit CSV and PDF imports. Set `ALLOWED_ORIGINS` to the exact dashboard origin(s). The dashboard uses `EXPENSE_API_BASE_URL` from the environment first, then Streamlit secrets. In development, a missing URL safely defaults to `http://127.0.0.1:8000`; it never falls back to a hosted API. For production, set `EXPENSE_DEPLOYMENT_ENV=production` and explicitly configure `EXPENSE_API_BASE_URL` through the deployment environment or Streamlit secrets. Production startup fails closed if the API URL is absent. For local password recovery, set `RESEND_API_KEY`, an allowed `RESEND_FROM_EMAIL` (the Resend test sender is `onboarding@resend.dev`), and `PASSWORD_RESET_URL=http://localhost:8501`. Production deployments must set `PASSWORD_RESET_URL` to the deployed Streamlit URL, use an appropriate verified sender domain, and supply the API key through the deployment secret manager. Never commit API keys or other credentials.

## Local Setup

Python 3.12 is the project's setup target. Confirm dependency compatibility with the full test suite before a release.

On Windows PowerShell:

```powershell
py -3.12 -m venv .venv-phase1
.\.venv-phase1\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

This uses a separate `.venv-phase1` and does not overwrite an existing `.venv`. Install `requirements-dev.txt` for development and testing; it includes runtime requirements.

Run the non-PostgreSQL tests with a temporary test-only JWT key. Never reuse this value as an application or deployment secret:

```powershell
$env:JWT_SECRET_KEY = "local-test-only-not-for-production"
Remove-Item Env:RUN_POSTGRES_INTEGRATION -ErrorAction SilentlyContinue
Remove-Item Env:ALLOW_TEST_DB_DESTRUCTIVE_SETUP -ErrorAction SilentlyContinue
python -m pytest -q
```

The tests use synthetic analytics data and mocked authentication database reads; they do not require production credentials or a hosted database. For application startup and endpoint checks, configure a separate local `.env` with a test database before starting the services.

On macOS or Linux:

```bash
python3.12 -m venv .venv-phase1
source .venv-phase1/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

## Running the Application

Start the FastAPI backend:

```bash
uvicorn main:app --reload
```

Start the Streamlit dashboard in a second terminal:

```bash
streamlit run dashboard.py
```

For a local backend, configure the dashboard before starting Streamlit:

```powershell
$env:EXPENSE_API_BASE_URL = "http://127.0.0.1:8000"
streamlit run dashboard.py
```

Configure the backend's `.env` separately from production with a dedicated local database, for example `DB_NAME=expense_intelligence_dev`, `DB_HOST=127.0.0.1`, and credentials for a local PostgreSQL role. Keep `.env` out of source control. The schema migration uses only an explicitly supplied `MIGRATION_DATABASE_URL`; it does not read application `DB_*` settings or `.env`.

### Transaction ownership migration

For a fresh database, startup creates `transactions.user_id` as a non-null integer with a foreign key to `users.id` and `ON DELETE CASCADE`. `CREATE TABLE IF NOT EXISTS` does not retrofit existing tables. Startup checks the current schema and refuses to serve traffic when those ownership constraints are missing; it never applies this data migration automatically.

For a legacy local database, verify the exact target and preserve a backup before proceeding. Set `MIGRATION_DATABASE_URL` explicitly to that separate local development database, then run the versioned migration:

```powershell
$env:MIGRATION_DATABASE_URL = "<explicit URL for the dedicated local development database>"
python -m scripts.migrations.001_transaction_ownership --schema public
```

The migration aborts if it finds null owners, orphaned user IDs, or a conflicting foreign key. After it reports success, start the API. Its startup readiness check verifies the non-null integer column and validated cascading foreign key. Do not run the command with production credentials or an ambiguous target.

### User-role migration and first administrator

Fresh schemas created by startup include `users.role` (`TEXT NOT NULL DEFAULT 'user'` with a `user`/`admin` check) and `users.is_active` (`BOOLEAN NOT NULL DEFAULT TRUE`). Existing schemas must receive the separately invoked role migration before the upgraded API can start; startup never applies it automatically. The migration backfills missing role/status values to ordinary active users, rejects unsupported roles or conflicting constraints, and preserves transaction ownership constraints.

After verifying the exact database and schema and preserving a backup, run the migration explicitly:

```powershell
$env:MIGRATION_DATABASE_URL = "<explicit reviewed database URL>"
python -m scripts.migrations.002_user_roles --schema public
```

The initial administrator is created only by an operator running the bootstrap CLI after the migration. It requires a separately explicit `ADMIN_BOOTSTRAP_DATABASE_URL`, prompts interactively for name/email and twice for a 12+ character password, and refuses to create an account when an active administrator already exists. No default account or password exists; the command is not part of startup or signup.

`GET /me` returns the current user's safe profile and database-backed role. `GET /admin/overview` returns aggregate counts only, and `GET /admin/users` provides a searchable, paginated account listing. Both `/admin/*` endpoints enforce administrator authorization in the API. The Admin Portal is read-only in this phase; role and activation mutations are not exposed through HTTP.

## API Endpoints

```text
GET    /
POST   /signup
POST   /login
POST   /logout
GET    /me
GET    /admin/overview
GET    /admin/users
POST   /upload
DELETE /delete-transactions
DELETE /delete-account
GET    /summary
GET    /transactions
GET    /category-breakdown
GET    /monthly-trend
GET    /insights
GET    /forecast
GET    /anomalies
GET    /research-evaluation
```

## Upload Format

CSV files must include the following columns:

```text
date,description,amount
```

Example:

```csv
Date,Description,Amount
2026-01-01,Salary,60000
2026-01-02,Rent,-18000
2026-01-03,Groceries,-450
```

PDF extraction supports text-based statements. Scanned or image-only PDFs need OCR before they can be imported reliably. Each successful upload replaces the current user's transaction data; the dashboard states this before processing. Invalid CSV rows and recognized PDF rows with invalid dates are skipped and counted in the result. Empty or fully invalid uploads are rejected before existing data is replaced.

## Data Management

- Deleting transactions removes only the current user's transaction data.
- Deleting an account removes the account and its associated transactions.
- The backend ensures the required database tables and transaction index exist at startup.

## Authentication and research analytics

Login issues a signed, expiring HS256 bearer token. Protected requests derive the account ID from the token; client-supplied account IDs are not used for authorization. Password recovery uses Resend to send a 15-minute reset link. The forgot-password response is the same for existing and unknown email addresses, and each reset attempt is committed before email delivery so a provider failure still enforces the 15-minute cooldown. If delivery fails, the attempted token is marked unusable while any previously valid link is preserved. Reset tokens are stored as SHA-256 hashes and claimed atomically; successful resets hash the new password and increment `token_version`, invalidating existing sessions. Configure `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, and `PASSWORD_RESET_URL` on the backend. Local links use `http://localhost:8501`; production must use the deployed Streamlit URL over HTTPS and a sender from a verified Resend domain.

The dashboard includes an explainable one-month expense forecast. It aggregates negative transaction amounts by calendar month, fills missing months with zero, and forecasts with the mean of up to the latest three months. The last-observed-month baseline is shown alongside chronological rolling-origin MAE/RMSE for both methods; every backtest prediction uses only months before its target. A forecast needs at least three calendar months; evaluation metrics require at least two held-out months and are withheld otherwise. The database does not store currency, so outputs are labeled in imported amount units. This small-sample estimate has no confidence interval or accuracy guarantee.

Possible unusual transactions are flagged against the same user's earlier category transactions using a robust median/MAD rule. A category needs at least three earlier transactions; explanations report the sample size, median, MAD, and the threshold used. Flags are indicators for review, not fraud findings. Pure analytics tests use synthetic examples in `test/test_analytics.py` and require no hosted credentials.

The authenticated Research tab preserves the original 57-description comparison as a rule-vocabulary coverage check and adds a separately authored synthetic challenge set of 38 labeled descriptions (two per supported category) whose wording avoids the current rule terms. Both are compared with a standard-library multinomial Naive Bayes comparator trained on the fixed 19-example training set. The anomaly evaluation uses 26 synthetic transactions and 10 manually labeled targets; 8 targets have sufficient history for precision/recall/F1, while 2 sparse-history cases are reported as unassessable. The anomaly cases include zero/nonzero MAD, borderline and moderate amounts, and a labeled false alarm. These synthetic results do not estimate performance on real statements. The `/research-evaluation` endpoint returns synthetic results only and does not score account transactions. Focused tests are in `test/test_research_evaluation.py`.

To reproduce the rolling-origin comparison on a local export with `date` and signed `amount` columns, run `python scripts/evaluate_forecast.py path/to/transactions.csv`. Invalid rows are skipped; the script emits the next-month estimate and held-out model/baseline MAE and RMSE as JSON.
