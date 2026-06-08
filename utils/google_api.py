import io
import streamlit as st
import pandas as pd
import gspread
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2.credentials import Credentials


@st.cache_resource
def get_oauth_credentials():
    # Authenticate as a real human user using OAuth secrets
    return Credentials(
        token=None,  # The library will auto-request an active token using the refresh token
        refresh_token=st.secrets["google_oauth"]["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=st.secrets["google_oauth"]["client_id"],
        client_secret=st.secrets["google_oauth"]["client_secret"]
    )

def save_to_google_sheets(df: pd.DataFrame):
    gc = gspread.authorize(get_oauth_credentials())
    sh = gc.open_by_key("11aSQaoDYVL9dWae9m864JbrwMvF1sQ4THFsBv-5ImbM")
    worksheet = sh.get_worksheet(1)  # You can specify the worksheet if needed

    new_data = df.drop(columns=['index'], errors='ignore').astype(str).values.tolist()  # Convert all data to string to avoid type issues
    worksheet.append_rows(new_data)  # Append data to the sheet

def save_file_to_drive(df: pd.DataFrame, filename=None):
    drive_service = build('drive', 'v3', credentials=get_oauth_credentials())
    account_name = df['account'].iloc[0] if 'account' in df.columns else 'unknown_account'
    if filename is None:
        filename = df['source_file'].iloc[0] if 'source_file' in df.columns else "no_name.csv"

    file_metadata = {
        'name': filename,
        'parents': ['15zXBMZeWQbjt2O5O4ZN-eVy6WuxfVepl'] if account_name.lower() == 'rbc'
        else ['1E4TFy0u0-TS15MatlamfCxncFZb4Dx9z']
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