import streamlit as st
import pandas as pd
from budget_app.transactions.normalize import build_transaction_frame, detect_source
from budget_app.db.transactions import insert_transactions, list_transactions
from budget_app.db.plaid import list_plaid_items
from budget_app.plaid.sync import complete_hosted_link, create_hosted_link, sync_transactions
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


def save_to_database(df):
    owner_email = st.session_state.get("user_email")
    if not owner_email:
        st.error("You must be signed in to save transactions.")
        return
    try:
        result = insert_transactions(owner_email, df)
        message = f"✅ Saved {result['inserted']} new transaction(s) to the database."
        if result["skipped"]:
            message += f" Skipped {result['skipped']} duplicate(s) already on file."
        st.success(message)
    except Exception as e:
        st.error(f"Error saving data: {e}")


st.title("Upload & Categorize")

st.subheader("Connect & sync bank accounts")
owner_email = st.session_state.get("user_email")

if not owner_email:
    st.info("Sign in to connect a bank account.")
else:
    linked_items = list_plaid_items(owner_email)

    if not linked_items.empty:
        for _, item in linked_items.iterrows():
            item_col, sync_col = st.columns([3, 1])
            with item_col:
                st.write(f"**{item['institution_name'] or 'Linked account'}**")
                st.caption("Synced before" if pd.notna(item["cursor"]) else "Never synced yet")
            with sync_col:
                if st.button("Sync now", key=f"sync_{item['item_id']}"):
                    try:
                        with st.spinner("Syncing transactions…"):
                            result = sync_transactions(owner_email, item["item_id"])
                        message = f"✅ Synced: {result['inserted']} new transaction(s)."
                        if result["skipped"]:
                            message += f" {result['skipped']} already on file."
                        if result.get("filtered"):
                            message += f" Ignored {result['filtered']} card payment/transfer(s)."
                        if result.get("removed"):
                            message += f" Removed {result['removed']} transaction(s) deleted at the bank."
                        st.success(message)
                    except Exception as e:
                        st.error(f"Sync failed: {e}")

    with st.expander("Connect a new bank account", expanded=linked_items.empty):
        st.caption(
            "Opens Plaid in a new tab. Once you've finished there, come back "
            "and click **I've finished linking**."
        )

        if st.button("Start linking", key="start_plaid_link"):
            try:
                link_token, hosted_url = create_hosted_link(owner_email)
                st.session_state["plaid_link_token"] = link_token
                st.session_state["plaid_hosted_url"] = hosted_url
            except Exception as e:
                st.error(f"Could not start Plaid Link: {e}")

        hosted_url = st.session_state.get("plaid_hosted_url")
        if hosted_url:
            st.link_button("Open Plaid ↗", hosted_url)
            if st.button("I've finished linking", key="finish_plaid_link"):
                try:
                    with st.spinner("Checking with Plaid…"):
                        item_id = complete_hosted_link(owner_email, st.session_state["plaid_link_token"])
                    if item_id:
                        st.session_state.pop("plaid_link_token", None)
                        st.session_state.pop("plaid_hosted_url", None)
                        st.success("✅ Bank account linked! Use **Sync now** above to pull in transactions.")
                        st.rerun()
                    else:
                        st.warning("Plaid hasn't recorded a completed link yet. Finish the flow in the Plaid tab, then try again.")
                except Exception as e:
                    st.error(f"Failed to complete linking: {e}")

st.divider()

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

    account_overrides = {}

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

            account_overrides[file_name] = account_override

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


    # Only build/categorize the dataframe once per upload.
    if st.session_state.get("main_df") is None:
        standardized = pd.DataFrame()

        for df, file_name in raw_dfs:
            source_type = detect_source(df)

            standardized = pd.concat(
                [standardized, build_transaction_frame(df, file_name, source_type)],
                ignore_index=True,
            )

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


    # Account changes update the existing dataframe
    for file_name, selected_account in account_overrides.items():
        if selected_account:
            update_main_df_account_for_file(file_name, selected_account)

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
        "Save to Database",
        key=f"save_to_db_button",
        on_click=save_to_database,
        args=(st.session_state.main_df,)  # Passes the expense frame to the callback function
    )

    with st.expander("Recently saved transactions"):
        owner_email = st.session_state.get("user_email")
        if owner_email:
            st.dataframe(list_transactions(owner_email, limit=25))

else:
    st.info("Please upload a CSV file to preview it.")
    if "main_df" in st.session_state:
        del st.session_state.main_df
