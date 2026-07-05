# 📊 Monthly Budget Automation App

A Streamlit app for turning raw credit card CSV statements into a clean monthly budgeting workflow.

The app lets you upload one or more transaction CSV files, normalize them into a common transaction schema, categorize expenses with Google Gemini, review and correct the results in Streamlit, and then save the finalized data to Google Drive and Google Sheets.

It is designed for a personal finance workflow where the main source of truth is a Google Sheet called `Personal_Budget`, backed by saved CSV copies in Google Drive.

---

## What the app does

This project automates the repetitive parts of monthly budgeting:

1. Sign in with a Google account.
2. Upload one or more credit card statement CSV files.
3. Detect the statement source.
4. Normalize all uploaded files into one consistent transaction table.
5. Use Gemini to assign budget categories from a fixed category list.
6. Let the user review and manually fix categories before saving.
7. Append finalized transactions to a Google Sheet.
8. Save finalized CSV copies into organized Google Drive folders.

The goal is not just to preview transactions. The app creates and maintains a lightweight budgeting system using Google Drive and Google Sheets.

---

## Main features

### Multi-file CSV upload

The app accepts multiple `.csv` files at once through the Streamlit uploader. Each file is read into a Pandas dataframe and processed into a shared normalized format.

For each uploaded file, the UI shows an account selector.

If `Other` is selected, the user can enter a custom account name. This account name is applied to all rows from that source file.

### AI-powered categorization

The app sends merchant names to Gemini and asks it to classify each merchant into one of these categories:

- `Groceries`
- `Transport`
- `Eating out`
- `Health & Wellness`
- `Fun stuff`
- `Gifts`
- `Travel`
- `Clothes`
- `Charity`
- `Other`

The Gemini prompt is built as a batch prompt. Duplicate merchant names are deduplicated before the request, then mapped back to the original transaction rows.

Gemini request and response logs are written to the local `logs/` directory:

- `logs/prompt.txt`
- `logs/response.txt`
- `logs/raw_response.txt`

### Review and manual correction

After categorization, the app stores the processed dataframe in Streamlit session state and gives the user multiple review views:

- Original uploaded table for each file.
- Full processed transaction table.
- Grouped transaction tables by category.

The `category` column is editable through Streamlit `st.data_editor`. Most structural columns are locked so the user does not accidentally change the date, merchant, amount, account, or generated file name.

When a category is edited inside a filtered category table, the app maps the change back to the main dataframe using the original row index.

### Google Drive backup

When the user clicks **Save to Google Drive & Sheets**, the app saves finalized CSV copies to Google Drive.

Drive structure:

```text
Budget app/
├── AMEX statements/
│   └── AMEX_YYYYMMDD_YYYYMMDD.csv
├── RBC statements/
│   └── RBC_YYYYMMDD_YYYYMMDD.csv
└── CUSTOM_ACCOUNT statements/
    └── CUSTOM_ACCOUNT_YYYYMMDD_YYYYMMDD.csv
```

The generated file name follows this pattern:

```text
{ACCOUNT}_{latest_transaction_date}_{earliest_transaction_date}.csv
```

Example:

```text
AMEX_20260701_20260601.csv
```

If multiple source files are uploaded, the app splits the finalized dataframe by `source_file` and saves each group as a separate CSV file.

### Google Sheets budgeting system

The app creates or reuses a Google Sheet named:

```text
Personal_Budget
```

The spreadsheet is stored inside the `Budget app` Google Drive folder.

The app manages three tabs:

| Tab | Purpose |
|---|---|
| `CAD_Log` | Raw transaction log. New finalized rows are appended here. |
| `Dashboard` | Monthly spending summary by budget category. |
| `Accounts` | Monthly spending summary by account. |

---

## Important behaviour to know

### Extra tabs are deleted from `Personal_Budget`

The spreadsheet template code keeps only these tabs:

- `Dashboard`
- `CAD_Log`
- `Accounts`

Any other tabs in the `Personal_Budget` spreadsheet are deleted when the template is enforced.

Do not manually add extra tabs to this spreadsheet unless you also update the code.

### Re-uploading the same statement can create duplicates

The app appends rows to `CAD_Log`. It does not currently check whether the same transaction or same statement has already been saved.

If you upload and save the same CSV twice, duplicate rows may be added.

### Merchant data is sent to Gemini

The app sends merchant names or transaction descriptions to Gemini for categorization. Do not use this app with financial data you are not comfortable sending to the configured Gemini API project.

---

## Tech stack

| Area | Tools |
|---|---|
| Web app | Streamlit |
| Data processing | Pandas |
| AI categorization | Google Gemini API via `google-genai` |
| Authentication | Google OAuth 2.0 via `requests-oauthlib` |
| Google Drive | Google Drive API v3 via `googleapiclient` |
| Google Sheets | Google Sheets API v4 via `googleapiclient` |
| App state | Streamlit session state |

---

## Project structure

The source code is organized around app UI, transaction normalization, AI categorization, and Google integrations.

```text
.
├── app.py
├── budget_app/
│   ├── ai/
│   │   └── gemini_category.py
│   ├── google/
│   │   ├── auth.py
│   │   ├── clients.py
│   │   ├── drive.py
│   │   ├── save_pipeline.py
│   │   ├── sheets.py
│   │   └── spreadsheet_template.py
│   └── transactions/
│       └── normalize.py
├── logs/
├── README.md
└── .streamlit/
    └── secrets.toml
```

### Key modules

| File | Responsibility |
|---|---|
| `app.py` | Main Streamlit UI, upload flow, category editing, grouped views, save button. |
| `budget_app/transactions/normalize.py` | Detects source files and converts raw CSVs into the standard transaction schema. |
| `budget_app/ai/gemini_category.py` | Builds Gemini prompts, calls Gemini, extracts response text, caches categorization calls, and writes logs. |
| `budget_app/google/auth.py` | Handles Google OAuth login, callback, session token storage, and logout. |
| `budget_app/google/clients.py` | Creates Google Drive and Sheets service clients. |
| `budget_app/google/drive.py` | Creates Drive folders and saves finalized CSV files. |
| `budget_app/google/sheets.py` | Appends finalized transaction rows to `CAD_Log`. |
| `budget_app/google/spreadsheet_template.py` | Creates or updates the `Personal_Budget` spreadsheet, tabs, formulas, and formatting. |
| `budget_app/google/save_pipeline.py` | Coordinates the full save process across Sheets and Drive. |

---

## How to use

1. Start the app.
2. Sign in with Google.
3. Upload one or more transaction CSV files.
4. Confirm the account label for each file.
5. Optional: open the original table to verify the raw upload.
6. Wait for Gemini to categorize merchants.
7. Review the processed table.
8. Correct categories where needed.
9. Review grouped category tables and totals.
10. Click **Save to Google Drive & Sheets**.
11. Open the generated Google Sheet or Drive folder from the success message.

---

## Category list

The app currently uses a fixed category list:

```text
Groceries
Transport
Eating out
Health & Wellness
Fun stuff
Gifts
Travel
Clothes
Charity
Other
```

To change the list, update it in two places:

1. The Gemini prompt in `gemini_category.py`.
2. The Streamlit selectbox column options in `app.py`.

If the spreadsheet dashboard should also reflect the new categories, update the dashboard headers and formulas in `spreadsheet_template.py`.

---


## Current limitations

- Does not currently prevent duplicate transaction uploads.
- Uses a fixed category list.
- Sends merchant names to Gemini for categorization.
- Stores Gemini prompts and responses in local log files.