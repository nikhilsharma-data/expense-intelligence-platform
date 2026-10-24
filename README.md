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
DB_NAME=your_database_name
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
JWT_SECRET_KEY=generate_a_private_random_secret
JWT_ALGORITHM=HS256
JWT_EXPIRATION_MINUTES=60
```

`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_PORT` are required by the backend. Set a strong private `JWT_SECRET_KEY`, `JWT_ALGORITHM`, and `JWT_EXPIRATION_MINUTES` for authentication. `MAX_UPLOAD_SIZE_MB` and `MAX_UPLOAD_ROWS` limit CSV and PDF imports. Set `ALLOWED_ORIGINS` to the exact dashboard origin(s); the local example covers Streamlit's default local addresses. Set `EXPENSE_API_BASE_URL` in the dashboard environment to the backend address. Production deployments must supply these values through their secret/configuration manager rather than committing credentials.

## Local Setup

```bash
git clone https://github.com/nikhilsharma-data/expense-intelligence-platform.git
cd expense-intelligence-platform
python -m venv .venv
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

To recreate the generated virtual environment, restore test dependencies, and run the full suite on Windows PowerShell:

```powershell
py -3.11 -m venv --clear .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
$env:JWT_SECRET_KEY = "test-only-local-secret-change-before-use"
python -m pytest -q
```

The tests use synthetic analytics data and mocked authentication database reads; they do not require production credentials or a hosted database. For application startup and endpoint checks, configure a separate local `.env` with a test database before starting the services.

On macOS or Linux:

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
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

## API Endpoints

```text
GET    /
POST   /signup
POST   /login
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

Login issues a signed, expiring HS256 bearer token. Protected requests derive the account ID from the token; client-supplied account IDs are not used for authorization. Password reset delivery is not configured, so the forgot-password endpoint currently cannot deliver a usable reset link.

The dashboard includes an explainable one-month expense forecast. It aggregates negative transaction amounts by calendar month, fills missing months with zero, and forecasts with the mean of up to the latest three months. The last-observed-month baseline is shown alongside chronological rolling-origin MAE/RMSE for both methods; every backtest prediction uses only months before its target. A forecast needs at least three calendar months; evaluation metrics require at least two held-out months and are withheld otherwise. The database does not store currency, so outputs are labeled in imported amount units. This small-sample estimate has no confidence interval or accuracy guarantee.

Possible unusual transactions are flagged against the same user's earlier category transactions using a robust median/MAD rule. A category needs at least three earlier transactions; explanations report the sample size, median, MAD, and the threshold used. Flags are indicators for review, not fraud findings. Pure analytics tests use synthetic examples in `test/test_analytics.py` and require no hosted credentials.

The authenticated Research tab preserves the original 57-description comparison as a rule-vocabulary coverage check and adds a separately authored synthetic challenge set of 38 labeled descriptions (two per supported category) whose wording avoids the current rule terms. Both are compared with a standard-library multinomial Naive Bayes comparator trained on the fixed 19-example training set. The anomaly evaluation uses 26 synthetic transactions and 10 manually labeled targets; 8 targets have sufficient history for precision/recall/F1, while 2 sparse-history cases are reported as unassessable. The anomaly cases include zero/nonzero MAD, borderline and moderate amounts, and a labeled false alarm. These synthetic results do not estimate performance on real statements. The `/research-evaluation` endpoint returns synthetic results only and does not score account transactions. Focused tests are in `test/test_research_evaluation.py`.

To reproduce the rolling-origin comparison on a local export with `date` and signed `amount` columns, run `python scripts/evaluate_forecast.py path/to/transactions.csv`. Invalid rows are skipped; the script emits the next-month estimate and held-out model/baseline MAE and RMSE as JSON.
