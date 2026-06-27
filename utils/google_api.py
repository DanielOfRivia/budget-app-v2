import io
import json
import base64
from datetime import date, datetime

import streamlit as st
import pandas as pd
import gspread
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2.credentials import Credentials
from requests_oauthlib import OAuth2Session


@st.cache_resource
def get_oauth_credentials():
    token_info = st.session_state.oauth_token
    return Credentials(
        token=token_info['access_token'],
        refresh_token=token_info.get('refresh_token'),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=st.secrets["google_oauth"]["client_id"],
        client_secret=st.secrets["google_oauth"]["client_secret"]
    )


def _get_drive_service():
    return build('drive', 'v3', credentials=get_oauth_credentials())


def _get_sheets_service():
    return build('sheets', 'v4', credentials=get_oauth_credentials())


def _coerce_sheet_value(value):
    if pd.isna(value):
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime('%Y-%m-%d')
    return value


def save_to_google_sheets(df: pd.DataFrame):
    spreadsheet_id = ensure_personal_budget_spreadsheet()
    sheets_service = _get_sheets_service()

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


def get_or_create_folder(drive_service, folder_name, parent_id=None):
    """Get folder ID by name, or create it if it doesn't exist."""
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = drive_service.files().list(q=query, spaces='drive', fields='files(id, name)', pageSize=1).execute()
    files = results.get('files', [])

    if files:
        return files[0]['id']

    file_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    if parent_id:
        file_metadata['parents'] = [parent_id]
    folder = drive_service.files().create(body=file_metadata, fields='id').execute()
    return folder.get('id')


def ensure_personal_budget_spreadsheet():
    drive_service = _get_drive_service()
    sheets_service = _get_sheets_service()

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
                f'=SUMIFS(CAD_Log!$C:$C, CAD_Log!$E:$E, {col_letter}$1, '
                f'CAD_Log!$A:$A, ">="&$A{row_idx}, CAD_Log!$A:$A, "<"&EOMONTH($A{row_idx},0)+1)'
            )
            row.append(formula)
        dashboard_rows.append(row)

    accounts_headers = ['Month-Year', 'Amex credit card', 'Rbc credit card', 'Fixed expenses', 'Total expenses']
    accounts_rows = []
    for row_idx, month_start in enumerate(months, start=2):
        row = [
            month_start.strftime('%Y-%m'),
            f'=SUMIFS(CAD_Log!$C:$C, CAD_Log!$F:$F, "AMEX_*", CAD_Log!$A:$A, ">="&$A{row_idx}, CAD_Log!$A:$A, "<"&EOMONTH($A{row_idx},0)+1)',
            f'=SUMIFS(CAD_Log!$C:$C, CAD_Log!$F:$F, "RBC_*", CAD_Log!$A:$A, ">="&$A{row_idx}, CAD_Log!$A:$A, "<"&EOMONTH($A{row_idx},0)+1)',
            '0',
            f'=SUM(B{row_idx}:D{row_idx})',
        ]
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
                        'range': {'sheetId': sheet_id, 'startColumnIndex': 1, 'endColumnIndex': 5},
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


def save_file_to_drive(df: pd.DataFrame, filename=None):
    drive_service = build('drive', 'v3', credentials=get_oauth_credentials())
    account_name = df['account'].iloc[0] if 'account' in df.columns else 'unknown_account'
    if filename is None:
        filename = df['source_file'].iloc[0] if 'source_file' in df.columns else "no_name.csv"

    root_folder_id = get_or_create_folder(drive_service, "Budget app")
    account_folder_id = get_or_create_folder(drive_service, f"{account_name.upper()} statements", root_folder_id)

    file_metadata = {
        'name': filename,
        'parents': [account_folder_id]
    }
    csv_bytes = io.BytesIO(df.to_csv(index=False).encode('utf-8'))
    media = MediaIoBaseUpload(csv_bytes, mimetype='text/csv')
    drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id',
        supportsAllDrives=True).execute()


def split_and_save_to_drive(df: pd.DataFrame):
    if 'source_file' in df.columns:
        source_files = df['source_file'].astype(str).fillna('unknown_source').unique()
        for source_name in source_files:
            group_df = df[df['source_file'].astype(str) == source_name]
            filename = source_name if source_name else 'no_name'
            if not filename.lower().endswith('.csv'):
                filename = f"{filename}.csv"
            save_file_to_drive(group_df, filename=filename)
    else:
        save_file_to_drive(df)


def save_to_gdrive_and_sheets(df: pd.DataFrame):
    try:
        save_to_google_sheets(df)
        split_and_save_to_drive(df)
        st.success("✅ Data saved to Google Sheets and CSV uploaded to Google Drive!")
    except Exception as e:
        st.error(f"Error saving data: {e}")


def login_to_google():
    CLIENT_ID = st.secrets["google_oauth"]["client_id"]
    CLIENT_SECRET = st.secrets["google_oauth"]["client_secret"]
    SCOPES = ['openid',
            'https://www.googleapis.com/auth/drive.file',
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/userinfo.profile', # To get user's name
            'https://www.googleapis.com/auth/userinfo.email'] # To get user's email
    
    REDIRECT_URI = "https://danylo-budget-app.streamlit.app/"  if st.config.get_option("server.headless") else "http://localhost:8501/"

    google = OAuth2Session(CLIENT_ID, scope=SCOPES, redirect_uri=REDIRECT_URI)
    
    # Check if returning from Google with an auth code in the URL parameters
    query_params = st.query_params
    if "code" in query_params:
        try:
            # Exchange authorization code for access tokens
            token = google.fetch_token(
                'https://oauth2.googleapis.com/token',
                client_secret=CLIENT_SECRET,
                code=query_params["code"]
            )
            st.session_state.oauth_token = token
            # 2. Extract and decode the id_token
            if 'id_token' in token:
                # JWTs are split by dots; the middle part is the data payload
                payload = token['id_token'].split('.')[1]
                # Add base64 padding to avoid decoding errors
                payload += '=' * (-len(payload) % 4)
                
                # Decode the JSON
                user_info = json.loads(base64.b64decode(payload).decode('utf-8'))
                
                # Instantly save to session state!
                st.session_state.user_email = user_info.get("email", "Unknown Email")
                st.session_state.user_name = user_info.get("name", "")
            # Clear URL parameters to clean up the workspace
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Authentication failed: {e}")

    # If no token exists in session, display the Login UI
    if "oauth_token" not in st.session_state:
        authorization_url, state = google.authorization_url(
            'https://accounts.google.com/o/oauth2/auth',
            access_type="offline", 
            prompt="select_account"
        )
        st.title("📊 Budget Automation App")
        st.write("Please sign in with your Google Account to process statements and update your budget.")
        
        # Open login window
        st.link_button("🔑 Sign In With Google", authorization_url, use_container_width=True)
        st.stop()