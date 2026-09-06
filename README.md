# 📊 Budget App

A Streamlit app for personal budgeting backed by Postgres, with automatic bank-transaction sync via Plaid and AI-assisted expense categorization via Google Gemini.

---

## What the app does

1. Sign in with a Google account (restricted to an allowlist of emails, if configured).
2. Connect bank accounts through Plaid Link and sync transactions automatically, or upload transaction CSV files by hand.
3. Categorize transactions with Gemini, using a fixed category list, with manual override.
4. Review, edit, and manage all transactions — categories, notes, lending status, and refund links.
5. Browse spending trends and category breakdowns on the dashboard.

All data lives in Postgres; there is no Google Sheets or Drive integration.

---

## Main features

### Google sign-in with an access allowlist

The app requires Google OAuth login. If `access.allowed_emails` is set in secrets, anyone who signs in but isn't on the list is stopped before any page content, Plaid call, or Gemini call runs.

### Bank sync via Plaid

From **Upload & Categorize**, a user can link a bank account through Plaid's hosted Link flow and sync transactions on demand. Access tokens are encrypted at rest. Syncing is incremental (cursor-based), filters out card payments/transfers, and removes transactions that were deleted at the bank.

### Manual CSV upload

Users can also upload one or more transaction CSV files. Each file is normalized into a common schema (source detection, date/amount parsing) before being merged with the rest.

### AI-powered categorization

Merchant names are sent to Gemini and classified into one of a fixed set of categories (see `budget_app/transactions/categories.py`). Requests are batched and deduplicated. Known merchant-to-category mappings are cached in the database so repeat merchants skip the Gemini call. Gemini request/response logs are written to the local `logs/` directory.

### Transaction management

The **All Transactions** page supports editing categories and notes, marking transactions as lending (money owed back) and settling them, and linking refunds back to the original purchase.

### Dashboard

The **Dashboard** page shows spend-over-time and spend-by-category charts, with drill-down into the underlying transactions for a selected period/category.

---

## Tech stack

| Area | Tools |
|---|---|
| Web app | Streamlit (multi-page) |
| Data storage | Postgres via SQLAlchemy |
| Bank data | Plaid |
| AI categorization | Google Gemini API via `google-genai` |
| Authentication | Google OAuth 2.0 via `requests-oauthlib` |
| Data processing | Pandas |
| Grids | `streamlit-aggrid` |
| Charts | Altair |

---

## Project structure

```text
.
├── app.py                              # Entry point: login, allowlist gate, page nav
├── pages/
│   ├── dashboard.py                    # Spend charts and drill-down
│   ├── upload.py                       # Plaid link/sync + manual CSV upload + categorization
│   └── transactions.py                 # Browse/edit all transactions, lending, refunds
├── budget_app/
│   ├── ai/
│   │   └── gemini_category.py          # Gemini prompts, batching, caching, logging
│   ├── db/
│   │   ├── engine.py                   # Postgres connection
│   │   ├── accounts.py                 # Account lookup/creation
│   │   ├── transactions.py             # Transaction CRUD, dedup, lending, refunds
│   │   ├── dashboard.py                # Dashboard query helpers
│   │   └── plaid.py                    # Plaid item storage, token encryption
│   ├── google/
│   │   └── auth.py                     # Google OAuth login/logout
│   ├── plaid/
│   │   ├── client.py                   # Plaid API client setup
│   │   └── sync.py                     # Hosted Link + transaction sync
│   └── transactions/
│       ├── categories.py               # Fixed category list
│       └── normalize.py                # CSV source detection + schema normalization
├── logs/                               # Local Gemini request/response logs (gitignored)
└── .streamlit/secrets.toml             # Local secrets (gitignored)
```

---

## Configuration

Secrets are read via `st.secrets` (`.streamlit/secrets.toml` locally, or the deployment's secrets store):

```toml
GEMINI_API_KEY = "..."
GEMINI_MODEL = "..."

[google_oauth]
client_id = "..."
client_secret = "..."
redirect_uri = "http://localhost:8501/"   # optional, defaults shown

[connections.postgres]
url = "postgresql://..."

[access]
allowed_emails = ["you@example.com"]      # optional; omit to allow anyone who signs in

[plaid]
client_id = "..."
secret = "..."
env = "sandbox"                            # optional, defaults to "sandbox"
token_encryption_key = "..."
```

---

## Category list

Categories are defined in `budget_app/transactions/categories.py`. The dashboard assigns chart colors by list position, so reordering the list reassigns colors — append new categories at the end rather than reordering. To change the list, also update the Gemini prompt in `gemini_category.py`.

---

## Current limitations

- Uses a fixed category list.
- Sends merchant names to Gemini for categorization.
- Stores Gemini prompts and responses in local log files.
