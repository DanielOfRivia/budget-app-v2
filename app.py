import streamlit as st
import pandas as pd
from budget_app.transactions.normalize import build_transaction_frame, detect_source
from budget_app.google.save_pipeline import save_to_gdrive_and_sheets
from budget_app.google.auth import login_to_google, logout_google
from google.api_core.exceptions import DeadlineExceeded
from budget_app.ai.gemini_category import categorize_merchants

def toggle_table_visibility(session_state_key):
    st.session_state[session_state_key] = not st.session_state[session_state_key]


def set_original_table(index):
    current = st.session_state.get("current_original_table")
    st.session_state["current_original_table"] = None if current == index else index


def update_main_df_account_for_file(file_name, account_value):
    main_df = st.session_state.get("main_df")
    if main_df is None or "old_source_file" not in main_df.columns:
        return

    matches = main_df["old_source_file"].astype(str) == file_name
    if matches.any():
        main_df.loc[matches, "account"] = account_value
        
        # Update source_file with new account name and date range from matched rows
        matched_rows = main_df.loc[matches]
        date_min = pd.to_datetime(matched_rows["date"]).min()
        date_max = pd.to_datetime(matched_rows["date"]).max()
        new_source_file = f"{account_value}_{date_max.strftime('%Y%m%d')}_{date_min.strftime('%Y%m%d')}.csv"
        main_df.loc[matches, "source_file"] = new_source_file


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

login_to_google()  # Call the login function to handle OAuth flow

st.sidebar.markdown("### Account")
if st.session_state.get("user_name"):
    st.sidebar.success(f"✅ Welcome, {st.session_state.user_name}!")
    st.sidebar.caption(f"Signed in as: **{st.session_state.user_email}**")
else:
    st.sidebar.success(f"✅ Signed in as: **{st.session_state.user_email}**")
if st.sidebar.button("Log Out"):
    logout_google()

#file uploader for CSV files
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

    # Show account selection for each uploaded file and combine all processed DataFrames
    account_scheme = ["AMEX", "RBC", "Other"]
    file_cols = st.columns(len(raw_dfs))
    for i, (df, file_name) in enumerate(raw_dfs):
        source_type = detect_source(df)
        account_key = f"account_select_{i}"
        custom_account_key = f"account_custom_{i}"

        if account_key not in st.session_state:
            st.session_state[account_key] = source_type
        if custom_account_key not in st.session_state:
            st.session_state[custom_account_key] = ""

        with file_cols[i]:
            st.markdown(f"**{file_name}**")
            selected_account = st.selectbox(
                "Account",
                options=account_scheme,
                key=account_key,
            )

            if selected_account == "Other":
                custom_value = st.text_input(
                    "Custom account name",
                    key=custom_account_key,
                    help="Input new account name and hit enter",
                ).strip()
                account_override = custom_value or source_type
            else:
                account_override = selected_account

            current_original = st.session_state.get("current_original_table")
            show_original = current_original == i
            original_button_label = (
                f"Hide original table for {file_name}"
                if show_original
                else f"Show original table for {file_name}"
            )
            st.button(
                original_button_label,
                key=f"show_original_button_{i}",
                on_click=set_original_table,
                args=(i,),
            )

        standardized = pd.concat(
            [standardized, build_transaction_frame(df, file_name, source_type)],
            ignore_index=True,
        )

        st.session_state[f"selected_account_for_{file_name}"] = account_override

    # Store the combined processed DataFrame in session state for later use

    try:
        with st.spinner("Assigning categories to transactions…"):
            category_series = pd.Series(
                categorize_merchants(tuple(standardized["merchant"])),
                dtype="string"
            )
            standardized["category"] = category_series
        st.session_state.main_df = standardized.copy().reset_index()

        for file_name in [name for _, name in raw_dfs]:
            selected_account = st.session_state.get(f"selected_account_for_{file_name}")
            if selected_account:
                update_main_df_account_for_file(file_name, selected_account)

    except DeadlineExceeded:
        st.error("⏱️ AI Generation Timed Out. Please try again.")
        st.session_state.csv = None  # Reset the file uploader to allow re-uploading

    except Exception as e:
        st.error(f"An error occurred during AI categorization: {e}")
        st.session_state.csv = None  # Reset the file uploader to allow re-uploading
    
    # Show the original table below the file selectors
    current_original = st.session_state.get("current_original_table")
    if current_original is not None and 0 <= current_original < len(raw_dfs):
        df = raw_dfs[current_original][0]
        file_name = raw_dfs[current_original][1]
        source_type = detect_source(df)
        card_label = f"{source_type} card ({file_name})"
        st.subheader(card_label)
        st.dataframe(df)

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