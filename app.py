import os
# from dotenv import load_dotenv
import streamlit as st
import pandas as pd
from normalize import build_transaction_frame, detect_source
import gspread

# load_dotenv()
st.set_page_config(page_title="CSV Preview App", layout="wide")

st.title("Budget monthly update")
st.markdown(
    "Upload a CSV file and preview it. The app will identify if it's an AMEX or RBC export and normalize transactions."
)

uploaded_files = st.file_uploader(
    "Upload transaction CSV", type=["csv"], key="csv", accept_multiple_files=True
)

def toggle_table_visibility(session_state_key):
    st.session_state[session_state_key] = not st.session_state[session_state_key]

if uploaded_files:
    for i, uploaded_file in enumerate(uploaded_files):
        try:
            df = pd.read_csv(uploaded_file)
        except Exception as e:
            st.error(f"Unable to read CSV file: {e}")
            continue

        source_type = detect_source(df)
        standardized = build_transaction_frame(df, uploaded_file.name or "uploaded_file.csv", source_type)

        st.subheader(f"{source_type} card ({uploaded_file.name or 'uploaded_file.csv'})")


        if f"show_original_{i}" not in st.session_state:
            st.session_state[f"show_original_{i}"] = False

        original_button_label = "Hide original table" if st.session_state[f"show_original_{i}"] else "Show original table"

        st.button(
            original_button_label, 
            key=f"show_original_button_{i}", 
            on_click=toggle_table_visibility, 
            args=(f"show_original_{i}",)  # Passes the session state key to the callback function
        )

        if st.session_state[f"show_original_{i}"]:
            st.dataframe(df)


        if f"show_processed_{i}" not in st.session_state:
            st.session_state[f"show_processed_{i}"] = False

        processed_button_label = "Hide processed table" if st.session_state[f"show_processed_{i}"] else "Show processed table"

        st.button(
            processed_button_label, 
            key=f"show_processed_button_{i}", 
            on_click=toggle_table_visibility, 
            args=(f"show_processed_{i}",)  # Passes the session state key to the callback function
        )

        edited_df = None
        if st.session_state[f"show_processed_{i}"]:
            column_configuration = {
                "category": st.column_config.SelectboxColumn(
                    "category", # The label displayed at the top of the column
                    help="Select the category for this item",
                    options=["Groceries", "Transport", "Eating out", "Health & Wellness", "Fun stuff", "Gifts", "Travel", "Clothes", "Charity", "Other"],
                    required=True, # Prevents users from leaving it empty
                )
            }

            edited_df = st.data_editor(standardized, column_config=column_configuration)

        expense_frame = edited_df.copy() if edited_df is not None else standardized.copy()
        if not expense_frame.empty:
            st.subheader("Top 5 transactions for each category")
            cat_tables = []
            for category, group in expense_frame.groupby("category"):
                label = category if category else "Uncategorized"
                top_transactions = (
                    group.sort_values("amount", ascending=False)
                    .head(5)
                    [["date", "merchant", "amount"]]
                )
                cat_tables.append((label, top_transactions.reset_index(drop=True)))

            # Render the category tables in rows of 3 columns
            for i in range(0, len(cat_tables), 3):
                cols = st.columns(3)
                for j, (label, table) in enumerate(cat_tables[i:i+3]):
                    with cols[j]:
                        st.markdown(f"**{label}**")
                        st.dataframe(table)
        
else:
    st.info("Please upload a CSV file to preview it.")

def save_to_google_sheets(df):
    gc = gspread.service_account(filename='service_account.json')
    sh = gc.open("Your Google Sheet Name")
    worksheet = sh.get_worksheet(0)  # You can specify the worksheet if needed

st.button(
            "Save to Google Sheets", 
            key="save_gdrive_button", 
            on_click=save_to_google_sheets, 
            args=(expense_frame,)  # Passes the expense frame to the callback function
        )