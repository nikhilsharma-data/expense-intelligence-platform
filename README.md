# Expense Intelligence Platform

A full-stack expense analytics app for uploading bank statements, tracking spending, and viewing actionable personal finance insights.

## Features

- Streamlit dashboard with a polished dark UI
- Login and signup with bcrypt password hashing
- CSV and PDF bank statement upload
- Plotly charts for category breakdown, expense distribution, and monthly cashflow
- KPI cards for balance, income, and expenses
- Date filters for all time, 30 days, 90 days, and year to date
- Smart insights for savings, high spending, top categories, merchants, weekend spending, and average monthly spend
- Transaction search and CSV export
- Settings tab for deleting transactions or deleting an account
- Upload flow uses an explicit `Process statement` button to avoid repeated uploads during Streamlit reruns

## Tech Stack

- Frontend: Streamlit
- Backend: FastAPI
- Database: PostgreSQL
- Data processing: pandas, pdfplumber
- Visualization: Plotly
- Auth: bcrypt

## Project Structure

```text
.
├── dashboard.py       # Streamlit frontend
├── main.py            # FastAPI backend
├── db.py              # PostgreSQL connection helper
├── requirements.txt   # Python dependencies
└── README.md
```

## Environment Variables

Create a `.env` file or configure these variables in your deployment environment:

```text
DB_NAME=your_database_name
DB_USER=your_database_user
DB_PASSWORD=your_database_password
DB_HOST=your_database_host
DB_PORT=5432
ALLOWED_ORIGINS=*
LOG_LEVEL=INFO
```

`ALLOWED_ORIGINS` can be a comma-separated list for production.

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

On macOS/Linux:

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Run Locally

Start the FastAPI backend:

```bash
uvicorn main:app --reload
```

Start the Streamlit dashboard in another terminal:

```bash
streamlit run dashboard.py
```

By default, `dashboard.py` points to:

```text
https://expense-intelligence-platform.onrender.com
```

For local backend testing, change `BASE_URL` in `dashboard.py` to:

```python
BASE_URL = "http://127.0.0.1:8000"
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

CSV files should include:

```text
date,description,amount
```

Example:

```csv
Date,Description,Amount
2026-01-01,Salary,60000
2026-01-02,Rent,-18000
2026-01-03,Swiggy,-450
```

PDF uploads are parsed with `pdfplumber`. Scanned/image-only PDFs may not extract readable text or tables.

## Notes

- The backend creates required `users` and `transactions` tables on startup.
- Uploading a new statement replaces the current user's existing transaction data.
- Deleting transactions removes only transaction rows for the current user.
- Deleting an account removes both the user and their transactions.
