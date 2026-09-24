# Engineering Audit

Date: 2026-09-13

Scope reviewed: `main.py`, `dashboard.py`, `db.py`, `README.md`, `requirements.txt`, `.env.example`, `.gitignore`, and `.devcontainer/devcontainer.json`.

This audit is forensic only. It does not propose a rewrite and does not implement fixes.

## Executive Summary

The application is a compact FastAPI + Streamlit + PostgreSQL personal finance platform with signup/login, statement upload, categorization, summary endpoints, insight generation, and account/data deletion. The repository is small and understandable, but it currently relies on client-held `user_id` values instead of authenticated backend sessions or tokens. That creates the most serious risk: any caller who can reach the API can read, replace, or delete another user's financial data by changing the `user_id` query parameter.

The next most important issues are database integrity, upload validation, local environment reproducibility, and missing automated tests around parsing, auth, deletion, and analytics.

## Critical

### C-1: API authorization is based only on caller-supplied `user_id`

- File: `main.py`, `dashboard.py`
- Function/area: all user-scoped endpoints: `upload_file`, `delete_transactions`, `delete_account`, `get_summary`, `get_transactions`, `category_breakdown`, `monthly_trend`, `get_insights`; dashboard API calls using `st.session_state.user_id`
- Current behavior: login returns `user_id`, and subsequent requests send that ID as a query parameter. The backend accepts any positive integer `user_id` and does not verify that the caller is authenticated as that user.
- Why it is a problem: a caller can change `user_id` to access summaries, transactions, insights, uploads, or destructive deletion for another account.
- Recommended fix: introduce server-validated authentication, such as signed JWT access tokens or secure session cookies, and derive the current user from the token/session instead of query parameters. Require auth on every user-scoped endpoint.
- Fix risks changing existing behavior: High. Frontend/backend request contracts must change, but this is necessary before treating the app as multi-user safe.

## High

### H-1: Destructive endpoints require no reauthentication or backend ownership proof

- File: `main.py`, `dashboard.py`
- Function/area: `delete_transactions`, `delete_account`; Settings tab delete buttons
- Current behavior: the dashboard asks for a checkbox confirmation, then calls delete endpoints with `user_id`. The backend deletes matching rows without checking password, active session, token, or ownership.
- Why it is a problem: the checkbox is only UI friction. Direct API calls can delete any user's data if the attacker knows or guesses an ID.
- Recommended fix: after implementing backend auth, require authenticated identity for deletes. Consider password confirmation or a fresh-auth requirement for account deletion.
- Fix risks changing existing behavior: Medium. Users may need one extra confirmation step for account deletion.

### H-2: Transactions table has no foreign key to users

- File: `main.py`
- Function/area: `startup_db` schema creation
- Current behavior: `transactions.user_id` is an unconstrained integer. The app manually deletes transactions before deleting a user.
- Why it is a problem: orphan transactions can exist if users are removed outside this code path, if a bug inserts an invalid `user_id`, or if concurrent/manual maintenance bypasses the app.
- Recommended fix: add `NOT NULL` and a foreign key from `transactions.user_id` to `users.id`, ideally with `ON DELETE CASCADE`.
- Fix risks changing existing behavior: Medium. Existing orphan rows, if any, must be cleaned before migration.

### H-3: Signup has a race condition around duplicate email creation

- File: `main.py`
- Function/area: `signup`
- Current behavior: the endpoint checks for an existing email, then inserts. If two requests race, one may hit the database unique constraint and fall into the generic `500 Internal server error` path.
- Why it is a problem: duplicate signup races produce misleading server errors and noisy logs instead of a clean `409 Email already registered`.
- Recommended fix: rely on the unique constraint and catch the database integrity error, rolling back and returning `409`.
- Fix risks changing existing behavior: Low. The intended duplicate-email behavior stays the same.

### H-4: CSV uploads have no size or row-count limit

- File: `main.py`, `dashboard.py`, `README.md`
- Function/area: `upload_file` CSV branch; Upload tab copy
- Current behavior: PDF uploads are capped by `MAX_UPLOAD_SIZE_MB`, but CSV uploads are read directly with `pd.read_csv(file.file)`. The dashboard says "Maximum 5,000 rows recommended", but this is not enforced.
- Why it is a problem: a large CSV can consume memory/CPU and tie up the server. The documented recommendation is not a guardrail.
- Recommended fix: enforce a byte limit for all uploads and a row limit or streaming/chunked import path for CSV.
- Fix risks changing existing behavior: Medium. Very large CSV uploads that currently work would be rejected or need a batch import path.

### H-5: Local Python environment is not reproducible on this machine

- File: `.venv` local environment, `README.md`, `requirements.txt`
- Function/area: local setup and verification
- Current behavior: the repository contains a `.venv` folder, but its interpreter points at a missing Windows Store Python installation. `python` and `py` are not available in the current shell, so even `py_compile` cannot run.
- Why it is a problem: developers cannot reliably run, compile, or test the app locally from the current checkout.
- Recommended fix: document a clean environment reset path and recreate `.venv` from an installed Python distribution. Keep `.venv` untracked, as already configured.
- Fix risks changing existing behavior: Low. This is environment repair, not application behavior.

## Medium

### M-1: Pydantic models perform minimal validation

- File: `main.py`
- Function/area: `SignupRequest`, `LoginRequest`, manual validation helpers
- Current behavior: models use plain `str` fields. Email and length validation happen manually in endpoint functions.
- Why it is a problem: validation logic is duplicated between frontend and backend, type constraints are not self-documenting, and future endpoints can accidentally bypass the helper pattern.
- Recommended fix: move basic validation into Pydantic model constraints and/or shared helper functions, including trimmed name/email, password length, and allowed date ranges.
- Fix risks changing existing behavior: Low to Medium. Some currently accepted malformed inputs may become rejected earlier.

### M-2: Date range values are silently accepted when invalid

- File: `main.py`, `dashboard.py`
- Function/area: `get_date_filter`, analytics endpoints
- Current behavior: unsupported `range` values fall through to `None`, which means "all time".
- Why it is a problem: a typo or malicious value silently broadens the query rather than returning a validation error.
- Recommended fix: validate `range` against `all`, `30d`, `90d`, and `ytd`, returning `400` for unsupported values.
- Fix risks changing existing behavior: Low. Only invalid callers are affected.

### M-3: PDF parsing supports a narrow transaction format

- File: `main.py`
- Function/area: `PDF_LINE_TRANSACTION_RE`, `parse_dr_cr_text_line`, `parse_pdf_text_transactions`, `parse_pdf_table_transactions`
- Current behavior: text parsing expects dates in `dd/mm/yyyy` or `dd-mm-yyyy` plus `amount(Dr|Cr)` and balance columns. Table parsing assumes withdrawal at index 4 and deposit at index 5 when the regex path does not match.
- Why it is a problem: many bank PDFs use different column order, date formats, debit/credit labels, or separate amount signs. Valid text-based statements can fail or partially import without a clear explanation.
- Recommended fix: add parser unit tests with representative statement formats, return structured parse diagnostics, and isolate bank-specific parsing rules.
- Fix risks changing existing behavior: Medium. Parser changes can alter imported transaction counts/categories.

### M-4: CSV date parsing and PDF date parsing are inconsistent

- File: `main.py`
- Function/area: CSV branch in `upload_file`; PDF insertion in `upload_file`
- Current behavior: CSV uses `pd.to_datetime(..., errors="coerce")` with default date interpretation. PDF insertion uses `pd.to_datetime(..., dayfirst=True)`.
- Why it is a problem: the same date string, such as `01/02/2026`, can be interpreted differently depending on file type.
- Recommended fix: document and enforce a single expected date format, or expose explicit parsing choices with validation errors for ambiguous dates.
- Fix risks changing existing behavior: Medium. Existing ambiguous CSV imports may shift dates if normalized.

### M-5: Upload replaces all existing transactions for a user

- File: `main.py`, `README.md`
- Function/area: `upload_file`
- Current behavior: both CSV and PDF uploads execute `DELETE FROM transactions WHERE user_id = %s` before inserting new rows. README documents this behavior.
- Why it is a problem: a failed assumption by the user can wipe historical transaction data when they intended to append another statement. The delete and insert are transactional, so failed inserts roll back, but successful uploads still replace everything.
- Recommended fix: keep current replace behavior for compatibility, but make it explicit in the UI before processing. Later, add an append/import mode with duplicate detection.
- Fix risks changing existing behavior: Low if only UI copy/confirmation is added; High if import semantics are changed.

### M-6: Per-row database inserts will scale poorly

- File: `main.py`
- Function/area: CSV and PDF insert loops in `upload_file`
- Current behavior: each transaction is inserted with an individual `cur.execute`.
- Why it is a problem: large statements require many round trips to PostgreSQL and can be slow under hosted database latency.
- Recommended fix: use `psycopg2.extras.execute_values` or batched inserts after validation.
- Fix risks changing existing behavior: Low if transaction order and values remain the same.

### M-7: Dashboard makes multiple sequential API calls on every rerun

- File: `dashboard.py`
- Function/area: data fetching block
- Current behavior: Streamlit reruns fetch summary, category breakdown, trend, insights, and transactions sequentially every time the page refreshes.
- Why it is a problem: dashboard interactions can generate repeated API/database load and slow UI response.
- Recommended fix: cache stable reads with suitable invalidation after upload/delete, or add a combined dashboard endpoint.
- Fix risks changing existing behavior: Medium. Caching must be invalidated carefully after data changes.

### M-8: `get_transactions` is unpaginated

- File: `main.py`, `dashboard.py`
- Function/area: `get_transactions`; Transaction History table
- Current behavior: the backend returns every transaction for the user, and the dashboard loads all of them.
- Why it is a problem: response size and frontend memory grow with account history.
- Recommended fix: add pagination, limit/offset or cursor support, and optionally server-side search/filtering.
- Fix risks changing existing behavior: Medium. The frontend export flow must preserve access to full exports.

### M-9: Password policy is minimal

- File: `main.py`, `dashboard.py`, `README.md`
- Function/area: signup validation
- Current behavior: passwords only need six characters.
- Why it is a problem: bcrypt is good for storage, but weak passwords remain easy to guess if login is exposed.
- Recommended fix: increase minimum length and consider breach/common-password checks and rate limiting.
- Fix risks changing existing behavior: Medium. Existing users should not be forced to reset immediately unless policy requires it.

### M-10: Login has no rate limiting or lockout

- File: `main.py`
- Function/area: `login`
- Current behavior: repeated password attempts are not throttled.
- Why it is a problem: exposed login endpoints are vulnerable to credential stuffing and brute-force attempts.
- Recommended fix: add IP/user-based rate limiting at the app or reverse-proxy layer.
- Fix risks changing existing behavior: Low to Medium. Legitimate rapid retries may be throttled.

### M-11: Devcontainer starts only the Streamlit frontend

- File: `.devcontainer/devcontainer.json`
- Function/area: `postAttachCommand`
- Current behavior: Codespaces/devcontainer starts `streamlit run dashboard.py`, but not the FastAPI backend. The dashboard defaults to the deployed backend unless `EXPENSE_API_BASE_URL` is set.
- Why it is a problem: local development can accidentally use production/deployed API data and cannot validate backend changes automatically.
- Recommended fix: update dev setup to start or document both services, and set the dashboard API base URL to the local backend in dev.
- Fix risks changing existing behavior: Low.

## Low

### L-1: `MAX_UPLOAD_SIZE_MB` can crash app startup if malformed

- File: `main.py`
- Function/area: configuration constants
- Current behavior: `int(os.getenv("MAX_UPLOAD_SIZE_MB", "10"))` runs at import time.
- Why it is a problem: a non-integer environment value prevents the application from starting with a raw exception.
- Recommended fix: parse configuration through a small settings layer that validates values and emits clear startup errors.
- Fix risks changing existing behavior: Low.

### L-2: CORS defaults to wildcard origins with credentials enabled

- File: `main.py`, `.env.example`, `README.md`
- Function/area: CORS middleware configuration
- Current behavior: `ALLOWED_ORIGINS` defaults to `*`, and `allow_credentials=True` is enabled.
- Why it is a problem: this is an unsafe default for an authenticated application and may behave unexpectedly because credentialed CORS does not pair cleanly with wildcard origins.
- Recommended fix: require explicit origins in production and use permissive CORS only in local development.
- Fix risks changing existing behavior: Medium. Deployments relying on wildcard origins will need configuration.

### L-3: Database connection handling opens one connection per request

- File: `db.py`, `main.py`
- Function/area: `get_db_connection`, `get_db`
- Current behavior: each request opens a new psycopg2 connection and closes it after the request.
- Why it is a problem: under load this is slower and can exhaust database connection limits.
- Recommended fix: use a connection pool appropriate for FastAPI deployment, or move to SQLAlchemy/asyncpg only if broader persistence refactoring is justified.
- Fix risks changing existing behavior: Medium. Pool lifecycle must be handled carefully.

### L-4: Startup schema creation is not a real migration system

- File: `main.py`
- Function/area: `startup_db`
- Current behavior: tables and one index are created with `CREATE TABLE IF NOT EXISTS`.
- Why it is a problem: future schema changes cannot be safely applied, versioned, or rolled back.
- Recommended fix: introduce lightweight migrations, such as Alembic or checked SQL migration files.
- Fix risks changing existing behavior: Medium during first migration if existing databases differ from expected schema.

### L-5: Analytics queries build SQL strings with f-strings

- File: `main.py`
- Function/area: `get_summary`, `category_breakdown`, `monthly_trend`, `get_insights`
- Current behavior: SQL fragments are assembled with f-strings, but the dynamic fragment comes only from internal `build_date_clause`, not direct user input.
- Why it is a problem: current injection risk is low, but the pattern is fragile if future dynamic filters are added.
- Recommended fix: keep parameters separate and centralize query construction; avoid extending this pattern with user-controlled SQL fragments.
- Fix risks changing existing behavior: Low.

### L-6: User existence is not checked before uploads or analytics

- File: `main.py`
- Function/area: user-scoped endpoints
- Current behavior: endpoints accept any positive `user_id`; uploads can create transactions for a nonexistent user because there is no foreign key.
- Why it is a problem: bad clients or manual calls can create unreachable data and misleading empty responses.
- Recommended fix: once auth is in place, derive identity from auth. If query IDs remain temporarily, validate that the user exists.
- Fix risks changing existing behavior: Low to Medium. Calls with invalid IDs will start failing.

### L-7: HTML is rendered with unsanitized user-controlled name

- File: `dashboard.py`
- Function/area: sidebar user block
- Current behavior: `st.session_state.user_name` is interpolated into `unsafe_allow_html=True` markup.
- Why it is a problem: a stored name containing HTML can be rendered by Streamlit. Streamlit's execution model reduces some browser risks, but unsafe HTML should not receive unsanitized user input.
- Recommended fix: escape the name before interpolation or render it with normal Streamlit text APIs.
- Fix risks changing existing behavior: Low. Names containing HTML will display as text.

### L-8: Dashboard search treats user input as a regex

- File: `dashboard.py`
- Function/area: transaction search
- Current behavior: `str.contains(search, case=False, na=False)` uses regex semantics by default.
- Why it is a problem: searches containing regex metacharacters can fail or match unexpectedly.
- Recommended fix: pass `regex=False` for literal search unless regex search is intended.
- Fix risks changing existing behavior: Low. Only regex-style searches change.

### L-9: Dashboard network errors are repeated for each endpoint

- File: `dashboard.py`
- Function/area: `safe_get` and data fetching block
- Current behavior: if the backend is unavailable, each of the five data calls can emit its own connection error.
- Why it is a problem: users see noisy repeated errors instead of one clear backend availability message.
- Recommended fix: add a lightweight health check or shared request wrapper that suppresses duplicate connection errors per rerun.
- Fix risks changing existing behavior: Low.

### L-10: `transactions.date`, `description`, `category`, and `amount` allow NULL

- File: `main.py`
- Function/area: `startup_db` schema creation
- Current behavior: transaction fields are nullable in the database schema.
- Why it is a problem: current insert paths usually provide values, but schema allows invalid rows from bugs or manual imports. Analytics assume usable dates and amounts.
- Recommended fix: add `NOT NULL` constraints after cleaning existing data.
- Fix risks changing existing behavior: Medium if existing invalid rows are present.

## Improvements

### I-1: No automated tests exist

- File: repository-wide
- Function/area: tests
- Current behavior: no test files were found.
- Why it is a problem: parser behavior, categorization rules, auth flows, deletion, and analytics can regress silently.
- Recommended fix: add focused pytest coverage for pure functions first, then FastAPI endpoint tests with a test database or mocked DB layer.
- Fix risks changing existing behavior: Low. Tests may expose behavior that needs deliberate decisions.

### I-2: Categorization rules are embedded in code

- File: `main.py`
- Function/area: `categorize`
- Current behavior: category labels and keyword tuples are hard-coded inside the function.
- Why it is a problem: adding categories requires code edits, and rule ordering can create subtle misclassification.
- Recommended fix: move rules to a module-level constant or data file and add tests for representative merchants.
- Fix risks changing existing behavior: Medium. Reordering or changing rules affects categories.

### I-3: Backend and frontend duplicate validation and formatting logic

- File: `main.py`, `dashboard.py`
- Function/area: signup validation, safe numeric conversion, currency/date display
- Current behavior: both layers contain similar but not identical validation and conversion logic.
- Why it is a problem: validation messages and accepted inputs can drift.
- Recommended fix: keep backend authoritative, simplify frontend validation to user-friendly prechecks, and test backend contracts.
- Fix risks changing existing behavior: Low.

### I-4: README does not include troubleshooting for Python or database setup

- File: `README.md`
- Function/area: Local Setup, Configuration
- Current behavior: setup assumes `python` is available and that PostgreSQL credentials are already known.
- Why it is a problem: on this machine, Python is not available through `python` or `py`, and `.venv` is broken.
- Recommended fix: add troubleshooting for installing Python, recreating `.venv`, validating `python --version`, and checking DB connectivity.
- Fix risks changing existing behavior: Low.

### I-5: Dependency management has no lock file

- File: `requirements.txt`
- Function/area: dependency versions
- Current behavior: most packages are pinned, but `bcrypt` is unpinned and there is no lock file.
- Why it is a problem: environments can drift over time, especially for transitive dependencies.
- Recommended fix: pin `bcrypt` and consider generating a lock/constraints file for deployment reproducibility.
- Fix risks changing existing behavior: Low to Medium. Pinning may require selecting a currently compatible version.

### I-6: Upload responses expose parsed PDF preview text

- File: `main.py`, `dashboard.py`
- Function/area: PDF upload response
- Current behavior: the backend returns the first 5,000 characters of extracted PDF text, but the dashboard only uses this to detect an empty preview.
- Why it is a problem: statement text can include sensitive financial details. Returning it expands the amount of sensitive data moving through API responses.
- Recommended fix: return a boolean or diagnostics summary instead of raw extracted text unless a deliberate preview feature is added.
- Fix risks changing existing behavior: Low. The current UI does not display the preview.

## Prioritized Implementation Plan

1. Repair local development environment: install a working Python, recreate `.venv`, install requirements, and confirm `python -m py_compile main.py dashboard.py db.py` succeeds.
2. Add a minimal pytest setup for pure functions: `parse_money`, PDF line parsing, categorization, date filtering, and summary math expectations.
3. Implement real backend authentication: issue signed tokens or secure sessions on login and require authenticated identity on all user-scoped endpoints.
4. Remove client-controlled authorization: stop accepting `user_id` from the dashboard for protected actions; derive it server-side from auth.
5. Harden destructive actions: require authenticated identity and consider password confirmation for account deletion.
6. Add database integrity: foreign key from transactions to users, `NOT NULL` constraints, useful indexes, and a migration path for existing databases.
7. Enforce upload limits for CSV and PDF: byte limits, row limits, clear errors, and tests for oversized/invalid files.
8. Stabilize parsing: add fixture-based parser tests, document supported formats, and improve diagnostics before broadening PDF support.
9. Improve dashboard reliability: reduce repeated API errors, sanitize rendered user names, use literal transaction search, and invalidate cached data after upload/delete if caching is added.
10. Improve deployment/developer configuration: explicit production CORS origins, local API base URL guidance, devcontainer backend startup, and dependency pinning/lock strategy.
