import io
import streamlit as st
import pandas as pd
from googleapiclient.http import MediaIoBaseUpload
from budget_app.google.clients import get_drive_service


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
    folder_id = folder.get('id')
    st.info(f"Created Google Drive folder '{folder_name}'.")
    return folder_id


def save_file_to_drive(df: pd.DataFrame, filename=None):
    drive_service = get_drive_service()
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

    return root_folder_id


def split_and_save_to_drive(df: pd.DataFrame):
    if 'source_file' in df.columns:
        source_files = df['source_file'].astype(str).fillna('unknown_source').unique()
        drive_folder_id = None
        for source_name in source_files:
            group_df = df[df['source_file'].astype(str) == source_name]
            filename = source_name if source_name else 'no_name'
            if not filename.lower().endswith('.csv'):
                filename = f"{filename}.csv"
            drive_folder_id = save_file_to_drive(group_df, filename=filename)
        return drive_folder_id

    return save_file_to_drive(df)