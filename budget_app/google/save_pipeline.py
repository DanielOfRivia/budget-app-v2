import pandas as pd
import streamlit as st
from budget_app.google.sheets import save_to_google_sheets
from budget_app.google.drive import split_and_save_to_drive

def save_to_gdrive_and_sheets(df: pd.DataFrame):
    try:
        save_to_google_sheets(df)
        split_and_save_to_drive(df)
        st.success("✅ Data saved to Google Sheets and CSV uploaded to Google Drive!")
    except Exception as e:
        st.error(f"Error saving data: {e}")
