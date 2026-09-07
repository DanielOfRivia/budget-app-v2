import pandas as pd
import streamlit as st

from budget_app.db.accounts import list_accounts
from budget_app.db.transactions import list_transactions_with_lending
from budget_app.transactions.categories import CATEGORIES
from budget_app.transactions.table import render_transactions_table

st.title("🧾 All Transactions")

owner_email = st.session_state.get("user_email")
if not owner_email:
    st.info("Sign in to see your transactions.")
    st.stop()

if "txn_filters_generation" not in st.session_state:
    st.session_state["txn_filters_generation"] = 0
gen = st.session_state["txn_filters_generation"]

filter_row1 = st.columns([1, 1, 2, 2])
with filter_row1[0]:
    # Left empty (None), a date range filters nothing — same "defaults to
    # everything visible" rule the other filters follow.
    start_date = st.date_input("From", value=None, format="YYYY-MM-DD", key=f"filter_start_{gen}")
with filter_row1[1]:
    end_date = st.date_input("To", value=None, format="YYYY-MM-DD", key=f"filter_end_{gen}")
with filter_row1[2]:
    selected_categories = st.multiselect(
        "Categories", CATEGORIES, placeholder="All categories", key=f"filter_categories_{gen}"
    )
with filter_row1[3]:
    account_names = list_accounts(owner_email)["name"].to_list()
    selected_accounts = st.multiselect(
        "Accounts", account_names, placeholder="All accounts", key=f"filter_accounts_{gen}"
    )

filter_row2 = st.columns([2, 2, 1, 1])
with filter_row2[0]:
    merchant_search = st.text_input("Merchant contains", placeholder="e.g. uber", key=f"filter_merchant_{gen}")
with filter_row2[1]:
    refund_label = st.selectbox(
        "Refunds", ["Any", "Linked to a refund", "Not linked"], key=f"filter_refund_{gen}"
    )
with filter_row2[2]:
    unsettled_only = st.checkbox("Unsettled lending only", value=False, key=f"filter_unsettled_{gen}")
with filter_row2[3]:
    # Widgets above are keyed by `gen` rather than session_state directly —
    # like the transactions grid's own key bump, that's what lets this reset
    # them: a widget's value can't be cleared by writing to its key while it's
    # in use, but a fresh key with no stored value renders at its default.
    st.write("")
    if st.button("Clear filters", width="stretch"):
        st.session_state["txn_filters_generation"] += 1
        st.rerun()

refund_state = {"Any": None, "Linked to a refund": "linked", "Not linked": "unlinked"}[refund_label]

df = list_transactions_with_lending(
    owner_email,
    unsettled_only=unsettled_only,
    start_date=start_date,
    end_date=end_date,
    categories=selected_categories or None,
    accounts=selected_accounts or None,
    merchant_search=merchant_search or None,
    refund_state=refund_state,
)

filters_active = bool(
    start_date
    or end_date
    or selected_categories
    or selected_accounts
    or merchant_search
    or refund_state
    or unsettled_only
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

# st.metric rather than a $-in-markdown caption: st.caption runs its text
# through markdown, where a $...$ pair is parsed as inline math — it silently
# swallowed the dollar sign and rendered the number in a different font than
# the surrounding text. st.metric also reads bigger, which is the point here:
# this count and these two totals are worth seeing at a glance.
count_col, actual_col, adjusted_col = st.columns(3)
count_col.metric("Transactions", f"{len(df):,}")
actual_col.metric("Actual", f"${df['amount'].sum():,.2f}")
adjusted_col.metric("Adjusted", f"${df['adjusted_amount'].sum():,.2f}")
st.caption("Click anywhere in a row to edit its category, comments, lending, or refund link.")

render_transactions_table(owner_email, df, key_prefix="all_txn")
