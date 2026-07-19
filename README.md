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
ALLOWED_ORIGINS=*
LOG_LEVEL=INFO
MAX_UPLOAD_SIZE_MB=10
EXPENSE_API_BASE_URL=https://expense-intelligence-platform.onrender.com
```

`ALLOWED_ORIGINS` accepts a comma-separated list. `MAX_UPLOAD_SIZE_MB` limits PDF uploads before parsing. `EXPENSE_API_BASE_URL` lets the dashboard target either a local or deployed backend without code changes.

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

PDF extraction supports text-based statements. Scanned or image-only PDFs need OCR before they can be imported reliably. Uploading a new statement replaces the current user's transaction data.

## Data Management

- Deleting transactions removes only the current user's transaction data.
- Deleting an account removes the account and its associated transactions.
- The backend ensures the required database tables and transaction index exist at startup.
