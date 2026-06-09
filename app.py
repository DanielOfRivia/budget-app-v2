import streamlit as st
import pandas as pd
from utils.normalize import build_transaction_frame, detect_source
from utils.google_api import save_to_gdrive_and_sheets
from google.api_core.exceptions import DeadlineExceeded
from utils.gemini_category import categorize_merchants

def toggle_table_visibility(session_state_key):
    st.session_state[session_state_key] = not st.session_state[session_state_key]

# The callback function to handle updates
def handle_editor_change(editor_key, filtered_df):
    """Maps edits from a filtered data editor back to the main dataframe."""
    state_changes = st.session_state.get(editor_key)

    if state_changes and state_changes["edited_rows"]:
        # get changes made in this specific editor
        relative_row_idx, changes = next(iter(state_changes["edited_rows"].items()))  # Assuming only one row is edited at a time
        actual_global_idx = filtered_df.iloc[relative_row_idx]["index"]  # Get the global index from the filtered DataFrame
        col_name, new_value = next(iter(changes.items()))  # Assuming only one cell is edited at a time
        st.session_state.main_df.at[actual_global_idx, col_name] = (new_value)

standardized = pd.DataFrame()  # Initialize an empty DataFrame to hold the combined processed data

st.set_page_config(page_title="CSV Preview App", layout="wide")
st.title("Budget monthly update")
# st.markdown(
#     "Upload a CSV file and preview it. The app will identify if it's an AMEX or RBC export and normalize transactions."
# )

uploaded_files = st.file_uploader(
    "Upload transaction CSV", type=["csv"], key="csv", accept_multiple_files=True, 
    on_change=lambda: st.session_state.update({"show_processed": False, "transaction_groups": True, "main_df": None})
)

if uploaded_files:
    # Process each uploaded file and combine them into a single DataFrame
    raw_dfs = []

    for i, uploaded_file in enumerate(uploaded_files):
        try:
            df = pd.read_csv(uploaded_file)
        except Exception as e:
            st.error(f"Unable to read CSV file: {e}")
            continue
        raw_dfs.append((df, uploaded_file.name or f"uploaded_file_{i}.csv"))

    # Combine all processed DataFrames
    for df, file_name in raw_dfs:
        source_type = detect_source(df)
        standardized = pd.concat([standardized, build_transaction_frame(df, file_name, source_type)], ignore_index=True)

    # Store the combined processed DataFrame in session state for later use
    if "main_df" not in st.session_state or st.session_state.main_df is None:
        #run gemini categorization on the combined merchant list to get categories for all transactions
        try:
            with st.spinner("Assigning categories to transactions…"):
                category_series = pd.Series(
                    categorize_merchants(tuple(standardized["merchant"])),
                    dtype="string"
                )
                standardized["category"] = category_series
            st.session_state.main_df = standardized.copy().reset_index()

        except DeadlineExceeded:
            st.error("⏱️ AI Generation Timed Out. Please try again.")
            st.session_state.csv = None  # Reset the file uploader to allow re-uploading

        except Exception as e:
            st.error(f"An error occurred during AI categorization: {e}")
            st.session_state.csv = None  # Reset the file uploader to allow re-uploading
    
    # Show the original table
    for i in range(len(raw_dfs)):
        if f"show_original_{i}" not in st.session_state:
            st.session_state[f"show_original_{i}"] = False
        df = raw_dfs[i][0]
        file_name = raw_dfs[i][1]
        source_type = detect_source(df)
        card_label = f"{source_type} card ({file_name})"
        original_button_label = (f"Hide original table for {card_label}" 
                                if st.session_state[f"show_original_{i}"] 
                                else f"Show original table for {card_label}")
        st.button(
            original_button_label, 
            key=f"show_original_button_{i}", 
            on_click=toggle_table_visibility, 
            args=(f"show_original_{i}",)  # Passes the session state key to the callback function
        )

        if st.session_state[f"show_original_{i}"]:
            st.dataframe(df,)

    # Show the processed table with editable category column
    if f"show_processed" not in st.session_state:
        st.session_state[f"show_processed"] = False

    processed_button_label = "Hide processed table" if st.session_state[f"show_processed"] else "Show processed table"

    st.button(
        processed_button_label, 
        key=f"show_processed_button", 
        on_click=toggle_table_visibility, 
        args=(f"show_processed",)  # Passes the session state key to the callback function
    )

    edited_df = None
    category_column_configuration = {
            "date": st.column_config.DateColumn("date"),
            "category": st.column_config.SelectboxColumn(
                "category", # The label displayed at the top of the column
                help="Select the category for this item",
                options=["Groceries", "Transport", "Eating out", "Health & Wellness", "Fun stuff", "Gifts", "Travel", "Clothes", "Charity", "Other"],
                required=True, # Prevents users from leaving it empty
            ),
            "index": None,  # Hide the index column
        }
    if st.session_state[f"show_processed"]:
        st.data_editor(st.session_state.main_df, width="content", column_config=category_column_configuration, 
                                   disabled=["date", "merchant", "amount", "account", "source_file"],
                                   on_change=handle_editor_change, key="processed_editor",
                                   args=("processed_editor", st.session_state.main_df))  # Pass the editor key and the filtered DataFrame to the callback
    #save edited dataframe to a variable that can be used for further actions

    #group transactions by category
    if f"transaction_groups" not in st.session_state:
        st.session_state[f"transaction_groups"] = False

    transaction_groups_button_label = "Hide transaction groups" if st.session_state[f"transaction_groups"] else "Show transaction groups"

    st.button(
        transaction_groups_button_label,
        key=f"show_transaction_groups_button",
        on_click=toggle_table_visibility,
        args=(f"transaction_groups",)
    )

    if st.session_state[f"transaction_groups"]:
        st.subheader("Transaction groups by category")
        cat_tables = []
        for category, group in st.session_state.main_df.groupby("category"):
            group_name = category if category else "Uncategorized"
            top_transactions = (
                group.sort_values("amount", ascending=False)
                [["date", "merchant", "account", "amount", "category", "index"]]
            )
            total_amount = top_transactions["amount"].sum()
            label = f"{group_name} — Total: ${total_amount:,.2f}"
            cat_tables.append((label, top_transactions.reset_index(drop=True)))

        # Render the category tables in rows of 3 columns
        for row_idx in range(0, len(cat_tables), 3):
            cols = st.columns(3)
            for j, (label, table) in enumerate(cat_tables[row_idx:row_idx+3]):
                with cols[j]:
                    st.markdown(f"**{label}**")
                    st.data_editor(table, disabled=["date", "merchant", "amount", "account"],
                                   column_config=category_column_configuration,
                                   on_change=handle_editor_change, key=f"group_editor_{label}",
                                   args=(f"group_editor_{label}", table))
    
    st.button(
        "Save to Google Drive & Sheets", 
        key=f"save_combined_button", 
        on_click=save_to_gdrive_and_sheets, 
        args=(st.session_state.main_df,)  # Passes the expense frame to the callback function
    )
        
else:
    st.info("Please upload a CSV file to preview it.")
    if "main_df" in st.session_state:
        del st.session_state.main_df