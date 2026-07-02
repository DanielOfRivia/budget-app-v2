from datetime import date
import streamlit as st
import pandas as pd
from budget_app.google.clients import get_drive_service, get_sheets_service
from budget_app.google.drive import get_or_create_folder


def ensure_personal_budget_spreadsheet(df: pd.DataFrame):
    drive_service = get_drive_service()
    sheets_service = get_sheets_service()

    root_folder_id = get_or_create_folder(drive_service, "Budget app")
    query = (
        f"name='Personal_Budget' and mimeType='application/vnd.google-apps.spreadsheet' "
        f"and trashed=false and '{root_folder_id}' in parents"
    )
    results = drive_service.files().list(q=query, spaces='drive', fields='files(id, name)', pageSize=1).execute()
    files = results.get('files', [])

    if files:
        spreadsheet_id = files[0]['id']
    else:
        spreadsheet = drive_service.files().create(
            body={
                'name': 'Personal_Budget',
                'mimeType': 'application/vnd.google-apps.spreadsheet',
                'parents': [root_folder_id],
            },
            fields='id',
            supportsAllDrives=True,
        ).execute()
        spreadsheet_id = spreadsheet.get('id')
        st.info("Created Google Sheet 'Personal_Budget'.")

    spreadsheet = sheets_service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields='sheets.properties'
    ).execute()
    sheets = spreadsheet.get('sheets', [])
    existing_sheet_map = {sheet['properties']['title']: sheet['properties']['sheetId'] for sheet in sheets}

    requests = []
    required_tabs = ['Dashboard', 'CAD_Log', 'Accounts']

    if 'Dashboard' not in existing_sheet_map:
        if 'Sheet1' in existing_sheet_map:
            requests.append({
                'updateSheetProperties': {
                    'properties': {'sheetId': existing_sheet_map['Sheet1'], 'title': 'Dashboard'},
                    'fields': 'title',
                }
            })
            existing_sheet_map['Dashboard'] = existing_sheet_map.pop('Sheet1')
        else:
            requests.append({'addSheet': {'properties': {'title': 'Dashboard'}}})

    for tab_name in ['CAD_Log', 'Accounts']:
        if tab_name not in existing_sheet_map:
            requests.append({'addSheet': {'properties': {'title': tab_name}}})

    for tab_name, sheet_id in list(existing_sheet_map.items()):
        if tab_name not in required_tabs:
            requests.append({'deleteSheet': {'sheetId': sheet_id}})

    if requests:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={'requests': requests}
        ).execute()

    spreadsheet = sheets_service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields='sheets.properties'
    ).execute()
    sheet_map = {sheet['properties']['title']: sheet['properties']['sheetId'] for sheet in spreadsheet.get('sheets', [])}
    locale = spreadsheet.get('properties', {}).get('locale', 'en_US')
    formula_separator = ',' if locale.startswith('en_') else ';'
    today = date.today().replace(day=1)
    months = []
    start_month = today.replace(year=today.year - 1)
    end_month = today.replace(year=today.year + 1)
    current = start_month
    while current <= end_month:
        months.append(current)
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)

    dashboard_headers = [
        'Month-Year', 'Fixed expenses', 'Groceries', 'Transport', 'Eating out',
        'Health & Wellness', 'Fun stuff', 'Gifts', 'Travel', 'Clothes', 'Charity', 'Other'
    ]
    dashboard_rows = []
    for row_idx, month_start in enumerate(months, start=2):
        row = [month_start.strftime('%Y-%m'), '0']
        for col_idx, col_letter in enumerate(['C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L'], start=2):
            formula = (
                f'=SUMIFS(CAD_Log!$C:$C{formula_separator} CAD_Log!$E:$E{formula_separator} {col_letter}$1, '
                f'CAD_Log!$A:$A{formula_separator} ">="&$A{row_idx}{formula_separator} CAD_Log!$A:$A{formula_separator} "<"&EOMONTH($A{row_idx}{formula_separator}0)+1)'
            )
            row.append(formula)
        dashboard_rows.append(row)

    account_names = []
    if 'account' in df.columns:
        account_names = [
            str(account).strip()
            for account in pd.unique(df['account'].dropna())
            if str(account).strip()
        ]

    # Fetch existing account names from CAD_Log tab
    try:
        existing_cad_log = sheets_service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range='CAD_Log!D:D'  # Account column is D (index 3)
        ).execute()
        existing_values = existing_cad_log.get('values', [])
        if existing_values and len(existing_values) > 1:  # Skip header
            existing_accounts = [
                str(val[0]).strip()  # Extract first element from row
                for val in existing_values[1:]  # Skip header row
                if val and len(val) > 0 and str(val[0]).strip()
            ]
            # Combine with current account names, preserving order and removing duplicates
            all_accounts = []
            seen = set()
            for account in existing_accounts + account_names:
                if account not in seen:
                    all_accounts.append(account)
                    seen.add(account)
            account_names = all_accounts
    except Exception:
        # If unable to fetch existing data, just use current account names
        pass

    accounts_headers = ['Month-Year'] + account_names + ['Fixed expenses', 'Total expenses']

    accounts_rows = []
    for row_idx, month_start in enumerate(months, start=2):
        row = [month_start.strftime('%Y-%m')]

        for account_index, account_name in enumerate(account_names, start=1):
            formula = (
                f'=SUMIFS(CAD_Log!$C:$C{formula_separator} CAD_Log!$F:$F{formula_separator} "{account_name}_*", '
                f'CAD_Log!$A:$A{formula_separator} ">="&$A{row_idx}{formula_separator} CAD_Log!$A:$A{formula_separator} "<"&EOMONTH($A{row_idx}{formula_separator}0)+1)'
            )
            row.append(formula)

        fixed_col_letter = chr(ord('A') + len(account_names) + 1)
        row.append('0')
        row.append(f'=SUM(B{row_idx}:{fixed_col_letter}{row_idx})')
        accounts_rows.append(row)

    values_updates = [
        ('Dashboard!A1', [dashboard_headers] + dashboard_rows),
        ('CAD_Log!A1', [['Date', 'Transaction', 'Amount (CAD)', 'Account', 'Category', 'File_name', 'Notes']]),
        ('Accounts!A1', [accounts_headers] + accounts_rows),
    ]

    for range_name, values in values_updates:
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption='USER_ENTERED',
            body={'values': values}
        ).execute()

    format_requests = []
    for tab_name, sheet_id in sheet_map.items():
        if tab_name == 'Dashboard':
            format_requests.extend([
                {
                    'repeatCell': {
                        'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': 1},
                        'cell': {'userEnteredFormat': {'textFormat': {'bold': True}}},
                        'fields': 'userEnteredFormat.textFormat.bold',
                    }
                },
                {
                    'repeatCell': {
                        'range': {'sheetId': sheet_id, 'startColumnIndex': 0, 'endColumnIndex': 1},
                        'cell': {'userEnteredFormat': {'numberFormat': {'type': 'DATE', 'pattern': 'yyyy-mm'}}},
                        'fields': 'userEnteredFormat.numberFormat',
                    }
                },
                {
                    'repeatCell': {
                        'range': {'sheetId': sheet_id, 'startColumnIndex': 1, 'endColumnIndex': 12},
                        'cell': {'userEnteredFormat': {'numberFormat': {'type': 'CURRENCY', 'pattern': '$#,##0.00'}}},
                        'fields': 'userEnteredFormat.numberFormat',
                    }
                },
            ])
        elif tab_name == 'CAD_Log':
            format_requests.extend([
                {
                    'repeatCell': {
                        'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': 1},
                        'cell': {'userEnteredFormat': {'textFormat': {'bold': True}}},
                        'fields': 'userEnteredFormat.textFormat.bold',
                    }
                },
                {
                    'repeatCell': {
                        'range': {'sheetId': sheet_id, 'startColumnIndex': 0, 'endColumnIndex': 1},
                        'cell': {'userEnteredFormat': {'numberFormat': {'type': 'DATE', 'pattern': 'yyyy-mm-dd'}}},
                        'fields': 'userEnteredFormat.numberFormat',
                    }
                },
                {
                    'repeatCell': {
                        'range': {'sheetId': sheet_id, 'startColumnIndex': 2, 'endColumnIndex': 3},
                        'cell': {'userEnteredFormat': {'numberFormat': {'type': 'CURRENCY', 'pattern': '$#,##0.00'}}},
                        'fields': 'userEnteredFormat.numberFormat',
                    }
                },
            ])
        elif tab_name == 'Accounts':
            format_requests.extend([
                {
                    'repeatCell': {
                        'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': 1},
                        'cell': {'userEnteredFormat': {'textFormat': {'bold': True}}},
                        'fields': 'userEnteredFormat.textFormat.bold',
                    }
                },
                {
                    'repeatCell': {
                        'range': {'sheetId': sheet_id, 'startColumnIndex': 0, 'endColumnIndex': 1},
                        'cell': {'userEnteredFormat': {'numberFormat': {'type': 'DATE', 'pattern': 'yyyy-mm'}}},
                        'fields': 'userEnteredFormat.numberFormat',
                    }
                },
                {
                    'repeatCell': {
                        'range': {'sheetId': sheet_id, 'startColumnIndex': 1, 'endColumnIndex': 1 + len(account_names) + 3},
                        'cell': {'userEnteredFormat': {'numberFormat': {'type': 'CURRENCY', 'pattern': '$#,##0.00'}}},
                        'fields': 'userEnteredFormat.numberFormat',
                    }
                },
            ])

    if format_requests:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={'requests': format_requests}
        ).execute()

    return spreadsheet_id