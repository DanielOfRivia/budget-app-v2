from datetime import date, datetime
import pandas as pd
from budget_app.google.clients import get_sheets_service
from budget_app.google.spreadsheet_template import ensure_personal_budget_spreadsheet

def _coerce_sheet_value(value):
    if pd.isna(value):
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime('%Y-%m-%d')
    return value


def save_to_google_sheets(df: pd.DataFrame):
    spreadsheet_id = ensure_personal_budget_spreadsheet(df)
    sheets_service = get_sheets_service()

    df_clean = df.drop(columns=['index'], errors='ignore')
    for col in df_clean.select_dtypes(include=['datetime64', 'datetime']):
        df_clean[col] = df_clean[col].dt.strftime('%Y-%m-%d')

    rows = []
    for row in df_clean.to_dict('records'):
        rows.append([
            _coerce_sheet_value(row.get('date') or row.get('Date') or ''),
            _coerce_sheet_value(row.get('merchant') or row.get('Transaction') or row.get('transaction') or ''),
            _coerce_sheet_value(row.get('amount') or row.get('Amount (CAD)') or ''),
            _coerce_sheet_value(row.get('account') or row.get('Account') or ''),
            _coerce_sheet_value(row.get('category') or row.get('Category') or ''),
            _coerce_sheet_value(row.get('source_file') or row.get('File_name') or row.get('file_name') or ''),
            _coerce_sheet_value(row.get('notes') or row.get('Notes') or ''),
        ])

    if rows:
        sheets_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range='CAD_Log!A:G',
            valueInputOption='USER_ENTERED',
            body={'values': rows}
        ).execute()

    return spreadsheet_id