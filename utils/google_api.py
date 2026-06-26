import io
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

def save_to_google_sheets(df: pd.DataFrame):
    gc = gspread.authorize(get_oauth_credentials())
    sh = gc.open_by_key("11aSQaoDYVL9dWae9m864JbrwMvF1sQ4THFsBv-5ImbM")
    worksheet = sh.get_worksheet(1)  # You can specify the worksheet if needed
    df_clean = df.drop(columns=['index'], errors='ignore')
    for col in df_clean.select_dtypes(include=['datetime64', 'datetime']): # Convert datetime columns to string format for Google Sheets
        df_clean[col] = df_clean[col].dt.strftime('%Y-%m-%d')
    new_data = df_clean.values.tolist()  # Convert all data to string to avoid type issues
    worksheet.append_rows(new_data, value_input_option=gspread.utils.ValueInputOption.user_entered)  # Append data to the sheet

def get_or_create_folder(drive_service, folder_name, parent_id=None):
    """Get folder ID by name, or create it if it doesn't exist."""
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    
    results = drive_service.files().list(q=query, spaces='drive', fields='files(id, name)', pageSize=1).execute()
    files = results.get('files', [])
    
    if files:
        return files[0]['id']
    else:
        # Create the folder
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]
        folder = drive_service.files().create(body=file_metadata, fields='id').execute()
        return folder.get('id')


def save_file_to_drive(df: pd.DataFrame, filename=None):
    drive_service = build('drive', 'v3', credentials=get_oauth_credentials())
    account_name = df['account'].iloc[0] if 'account' in df.columns else 'unknown_account'
    if filename is None:
        filename = df['source_file'].iloc[0] if 'source_file' in df.columns else "no_name.csv"

    # Get or create "Budget app" root folder
    root_folder_id = get_or_create_folder(drive_service, "Budget app")
    
    # Get or create account folder inside "Budget app"
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
        st.write(REDIRECT_URI)
        
        # Open login window
        st.link_button("🔑 Sign In With Google", authorization_url, use_container_width=True)
        st.stop()