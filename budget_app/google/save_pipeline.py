import pandas as pd
import streamlit as st
from budget_app.google.sheets import save_to_google_sheets
from budget_app.google.drive import split_and_save_to_drive

def save_to_gdrive_and_sheets(df: pd.DataFrame):
    try:
        spreadsheet_id = save_to_google_sheets(df)
        drive_folder_id = split_and_save_to_drive(df)

        spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
        drive_url = f"https://drive.google.com/drive/folders/{drive_folder_id}" if drive_folder_id else "https://drive.google.com"

        st.success(
            "✅ Save completed. "
            f"Open the [spreadsheet]({spreadsheet_url}) or the [Google Drive folder]({drive_url})."
        )
    except Exception as e:
        st.error(f"Error saving data: {e}")
