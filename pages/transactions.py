import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder

from budget_app.db.accounts import list_accounts
from budget_app.db.transactions import (
    link_refund,
    get_transaction,
    list_transactions_with_lending,
    search_refund_candidates,
    set_category,
    set_lending_settled,
    set_lent_amount,
    unlink_refund,
)
from budget_app.transactions.categories import CATEGORIES

st.title("🧾 All Transactions")

owner_email = st.session_state.get("user_email")
if not owner_email:
    st.info("Sign in to see your transactions.")
    st.stop()

RANGE_OPTIONS = ["All time", "Last 3 months", "Last 6 months", "Last 12 months", "Year to date"]

filter_row1 = st.columns([1, 2, 2])
with filter_row1[0]:
    # Same preset vocabulary as the dashboard's time range, for consistency.
    # Defaults to All time so enabling filters never silently hides rows that
    # were visible before.
    range_label = st.selectbox("Time range", RANGE_OPTIONS, index=0)
with filter_row1[1]:
    selected_categories = st.multiselect("Categories", CATEGORIES, placeholder="All categories")
with filter_row1[2]:
    account_names = list_accounts(owner_email)["name"].to_list()
    selected_accounts = st.multiselect("Accounts", account_names, placeholder="All accounts")

filter_row2 = st.columns([2, 2, 1])
with filter_row2[0]:
    merchant_search = st.text_input("Merchant contains", placeholder="e.g. uber")
with filter_row2[1]:
    refund_label = st.selectbox("Refunds", ["Any", "Linked to a refund", "Not linked"])
with filter_row2[2]:
    unsettled_only = st.checkbox("Unsettled lending only", value=False)

today = pd.Timestamp.today().normalize()
if range_label == "All time":
    start_date = None
elif range_label == "Year to date":
    start_date = pd.Timestamp(today.year, 1, 1).date()
else:
    start_date = (today - pd.DateOffset(months=int(range_label.split()[1]))).date()

refund_state = {"Any": None, "Linked to a refund": "linked", "Not linked": "unlinked"}[refund_label]

df = list_transactions_with_lending(
    owner_email,
    unsettled_only=unsettled_only,
    start_date=start_date,
    categories=selected_categories or None,
    accounts=selected_accounts or None,
    merchant_search=merchant_search or None,
    refund_state=refund_state,
)

filters_active = bool(
    start_date or selected_categories or selected_accounts or merchant_search or refund_state or unsettled_only
)

if df.empty:
    # Distinguish "you have no data" from "your filters excluded everything" —
    # otherwise a narrow filter reads as an empty account.
    st.info(
        "No transactions match these filters."
        if filters_active
        else "No transactions yet. Head to **Upload & Categorize** to add some."
    )
    st.stop()

# The app shows the date a charge actually happened (Plaid's authorized_date,
# falling back to the posted date for rows that don't have one — CSV imports,
# and very recent Plaid rows it hasn't filled in yet). The posted date is a
# bank bookkeeping artifact that differs on ~83% of rows; it stays stored for
# statement reconciliation but is never displayed.
df["date"] = pd.to_datetime(df["occurred_on"])


def _status(row):
    if row["lent_total"] == 0:
        return "—"
    return "Settled" if row["lent_settled"] else "Unsettled"


def _refund_note(row):
    """Show the link from both sides — otherwise it's only visible by
    opening the dialog on the refund row, and the purchase side shows
    nothing at all."""
    if pd.notna(row.get("refund_of_transaction_id")):
        return f"↩ refund of {row['refund_of_merchant']}"
    if pd.notna(row.get("refunded_by_amount")):
        refunded_on = pd.to_datetime(row["refunded_by_date"]).strftime("%Y-%m-%d")
        return f"↩ refunded ${abs(float(row['refunded_by_amount'])):,.2f} on {refunded_on}"
    return ""


display_df = df.copy()
display_df["Status"] = display_df.apply(_status, axis=1)
display_df["Refund"] = display_df.apply(_refund_note, axis=1)
display_df["date"] = display_df["date"].dt.strftime("%Y-%m-%d")
display_df = display_df.rename(
    columns={
        "date": "Date",
        "merchant": "Merchant",
        "account_name": "Account",
        "category": "Category",
        "amount": "Actual",
        "lent_total": "Lent",
        "adjusted_amount": "Adjusted",
    }
)
table_columns = ["id", "Date", "Merchant", "Account", "Category", "Actual", "Lent", "Adjusted", "Status", "Refund"]

# st.dataframe's row selection only fires on its dedicated checkbox column —
# clicking elsewhere in the row just focuses that cell. AG Grid's default
# row-click behavior (no checkbox needed) selects the whole row from any
# cell, which is what was actually wanted here.
gb = GridOptionsBuilder.from_dataframe(display_df[table_columns])
gb.configure_column("id", hide=True)
gb.configure_selection(selection_mode="single", use_checkbox=False, suppressRowClickSelection=False)
gb.configure_default_column(resizable=True, flex=1)
grid_options = gb.build()

st.caption(
    f"**{len(df)}** transactions · actual ${df['amount'].sum():,.2f} · adjusted ${df['adjusted_amount'].sum():,.2f}"
    "  —  click anywhere in a row to edit its category, lent amount, or settle it."
)
if "transactions_grid_generation" not in st.session_state:
    st.session_state["transactions_grid_generation"] = 0

grid_response = AgGrid(
    display_df[table_columns],
    gridOptions=grid_options,
    update_on=["selectionChanged"],
    theme="streamlit",
    height=350,
    # AG Grid persists its selection client-side, keyed by this component
    # key — it's not something session_state can just be cleared to reset
    # like a native widget. Bumping the key mounts a fresh grid instance
    # with no selection, which is how the dialog "closes" after Save.
    key=f"transactions_grid_{st.session_state['transactions_grid_generation']}",
)

selected_data = grid_response.selected_data


def _close_dialog():
    st.session_state["transactions_grid_generation"] += 1
    st.session_state.pop("_open_txn_id", None)


def _jump_button(label, target_id, current_id):
    """Streamlit can't render a real hyperlink that opens another row's
    dialog, so this is the practical equivalent: record which transaction to
    open, clear the grid's own selection so it doesn't reopen the row we're
    leaving, and rerun."""
    if st.button(label, key=f"jump_{current_id}_{target_id}", width="stretch"):
        st.session_state["_open_txn_id"] = target_id
        st.session_state["transactions_grid_generation"] += 1
        st.rerun()


@st.dialog("Edit transaction")
def _edit_transaction_dialog(row):
    transaction_id = int(row["id"])
    st.write(f"**{row['merchant']}** — {row['date'].strftime('%Y-%m-%d')}")

    m1, m2 = st.columns(2)
    m1.metric("Actual amount", f"${row['amount']:,.2f}")
    m2.metric("Adjusted amount", f"${row['adjusted_amount']:,.2f}")

    # Gemini/CSV categorization only ever runs pre-save — this is the only
    # way to fix a category after the fact. Falls back to "Other" if the
    # stored value somehow isn't one of the current options.
    current_category = row["category"] if row["category"] in CATEGORIES else "Other"
    new_category = st.selectbox(
        "Category", CATEGORIES, index=CATEGORIES.index(current_category), key=f"category_{transaction_id}"
    )

    # Opt-in refund linking, only offered for negative-amount rows — most
    # negative amounts aren't refunds of a specific purchase at all (cashback,
    # welcome bonuses), so this is never required, just available.
    unlink_requested = False
    unlink_target_id = transaction_id
    selected_original_id = None

    # The purchase side of an existing link: show which refund is attached,
    # and let it be undone from here too rather than only from the refund row.
    if row["amount"] > 0 and pd.notna(row.get("refunded_by_transaction_id")):
        st.divider()
        refunded_on = pd.to_datetime(row["refunded_by_date"]).strftime("%Y-%m-%d")
        refunded_amt = abs(float(row["refunded_by_amount"]))
        st.caption(f"↩️ Refunded **${refunded_amt:,.2f}** on {refunded_on}")
        _jump_button(
            f"↗ Open refund: {row['refunded_by_merchant']} (${refunded_amt:,.2f})",
            int(row["refunded_by_transaction_id"]),
            transaction_id,
        )
        unlink_requested = st.checkbox("Remove this link", key=f"unlink_from_purchase_{transaction_id}")
        unlink_target_id = int(row["refunded_by_transaction_id"])

    if row["amount"] < 0:
        st.divider()
        if pd.notna(row.get("refund_of_transaction_id")):
            refund_date = pd.to_datetime(row["refund_of_date"]).strftime("%Y-%m-%d")
            st.caption(f"↩️ Linked as a refund of a purchase on {refund_date}")
            _jump_button(
                f"↗ Open purchase: {row['refund_of_merchant']}",
                int(row["refund_of_transaction_id"]),
                transaction_id,
            )
            unlink_requested = st.checkbox("Remove this link", key=f"unlink_refund_{transaction_id}")
        else:
            with st.expander("Link as refund of a purchase (optional)"):
                search_query = st.text_input(
                    "Search purchases on this account",
                    value=row["merchant"],
                    key=f"refund_search_{transaction_id}",
                )
                candidates = (
                    search_refund_candidates(owner_email, int(row["account_id"]), search_query)
                    if search_query
                    else pd.DataFrame()
                )
                if candidates.empty:
                    st.caption("No matching purchases found." if search_query else "Type to search.")
                else:
                    candidate_labels = [
                        f"{r['date']} — {r['merchant']} — ${r['amount']:,.2f}"
                        for _, r in candidates.iterrows()
                    ]
                    choice = st.selectbox(
                        "Matching purchases",
                        ["— none —"] + candidate_labels,
                        key=f"refund_choice_{transaction_id}",
                    )
                    if choice != "— none —":
                        selected_original_id = int(candidates.iloc[candidate_labels.index(choice)]["id"])

    # Lending is only meaningful for actual spending — you can't lend someone
    # part of a refund. Negative rows skip this section entirely; None means
    # "not applicable", so Save leaves these fields untouched.
    new_lent_amount = None
    settled = None
    if row["amount"] > 0:
        mode_col, amount_col = st.columns(2)
        with mode_col:
            input_mode = st.radio(
                "Enter as", ["Dollar amount", "Percentage"], horizontal=True, key=f"lend_mode_{transaction_id}"
            )

        with amount_col:
            if input_mode == "Percentage":
                current_pct = round(float(row["lent_total"]) / float(row["amount"]) * 100, 1)
                pct = st.number_input(
                    "Lent (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=current_pct,
                    step=1.0,
                    key=f"lend_pct_{transaction_id}",
                )
                new_lent_amount = round(float(row["amount"]) * pct / 100, 2)
            else:
                new_lent_amount = st.number_input(
                    "Lent amount ($)",
                    min_value=0.0,
                    max_value=float(row["amount"]),
                    value=float(row["lent_total"]),
                    step=5.0,
                    key=f"lend_amount_{transaction_id}",
                )

        if input_mode == "Percentage":
            st.caption(f"= ${new_lent_amount:,.2f}")

        settled = st.checkbox("Settled", value=bool(row["lent_settled"]), key=f"settled_checkbox_{transaction_id}")

    if st.button("Save", key=f"save_lend_{transaction_id}", width="stretch"):
        if new_lent_amount is not None:
            set_lent_amount(owner_email, transaction_id, new_lent_amount)
        if new_category != row["category"]:
            set_category(owner_email, transaction_id, new_category)
        if settled is not None and settled != bool(row["lent_settled"]):
            set_lending_settled(owner_email, transaction_id, settled)
        if unlink_requested:
            # unlink always targets the refund row, which is this row when
            # editing the refund, or the linked refund when editing the purchase.
            unlink_refund(owner_email, unlink_target_id)
        elif selected_original_id is not None:
            try:
                link_refund(owner_email, transaction_id, selected_original_id)
            except ValueError as e:
                st.error(str(e))
                st.stop()
        _close_dialog()
        st.rerun()


jump_id = st.session_state.get("_open_txn_id")
if jump_id is not None:
    # Fetched by id rather than looked up in df, so jumping to a linked
    # transaction works even when the current filters exclude it.
    jumped_row = get_transaction(owner_email, jump_id)
    if jumped_row is not None:
        _edit_transaction_dialog(jumped_row)
    else:
        st.session_state.pop("_open_txn_id", None)
elif selected_data is not None and not selected_data.empty:
    selected_id = int(selected_data.iloc[0]["id"])
    matched = df[df["id"] == selected_id]
    if not matched.empty:
        _edit_transaction_dialog(matched.iloc[0])
