import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder

from budget_app.db.lending import list_transactions_with_lending, set_lending_settled, set_lent_amount

st.title("🤝 Lending")

owner_email = st.session_state.get("user_email")
if not owner_email:
    st.info("Sign in to see lending.")
    st.stop()

unsettled_only = st.checkbox("Show unsettled only", value=False)

df = list_transactions_with_lending(owner_email, unsettled_only=unsettled_only)

if df.empty:
    st.info(
        "No unsettled lending."
        if unsettled_only
        else "No transactions yet. Head to **Upload & Categorize** to add some."
    )
    st.stop()

df["date"] = pd.to_datetime(df["date"])


def _status(row):
    if row["lent_total"] == 0:
        return "—"
    return "Settled" if row["lent_settled"] else "Unsettled"


display_df = df.copy()
display_df["Status"] = display_df.apply(_status, axis=1)
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
table_columns = ["id", "Date", "Merchant", "Account", "Category", "Actual", "Lent", "Adjusted", "Status"]

# st.dataframe's row selection only fires on its dedicated checkbox column —
# clicking elsewhere in the row just focuses that cell. AG Grid's default
# row-click behavior (no checkbox needed) selects the whole row from any
# cell, which is what was actually wanted here.
gb = GridOptionsBuilder.from_dataframe(display_df[table_columns])
gb.configure_column("id", hide=True)
gb.configure_selection(selection_mode="single", use_checkbox=False, suppressRowClickSelection=False)
gb.configure_default_column(resizable=True, flex=1)
grid_options = gb.build()

st.caption("Click anywhere in a row to edit its lent amount or settle it.")
if "lending_grid_generation" not in st.session_state:
    st.session_state["lending_grid_generation"] = 0

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
    key=f"lending_grid_{st.session_state['lending_grid_generation']}",
)

selected_data = grid_response.selected_data


def _close_dialog():
    st.session_state["lending_grid_generation"] += 1


@st.dialog("Edit lending")
def _edit_lending_dialog(row):
    transaction_id = int(row["id"])
    st.write(f"**{row['merchant']}** — {row['date'].strftime('%Y-%m-%d')}")

    m1, m2 = st.columns(2)
    m1.metric("Actual amount", f"${row['amount']:,.2f}")
    m2.metric("Adjusted amount", f"${row['adjusted_amount']:,.2f}")

    # Percentage of a negative/zero amount (a refund) isn't meaningful, so
    # that mode is only offered for ordinary positive-amount transactions.
    allow_percentage = row["amount"] > 0
    input_mode = (
        st.radio("Enter as", ["Dollar amount", "Percentage"], horizontal=True, key=f"lend_mode_{transaction_id}")
        if allow_percentage
        else "Dollar amount"
    )

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
        st.caption(f"= ${new_lent_amount:,.2f}")
    else:
        max_value = float(row["amount"]) if row["amount"] > 0 else None
        new_lent_amount = st.number_input(
            "Lent amount ($)",
            min_value=0.0,
            max_value=max_value,
            value=float(row["lent_total"]),
            step=5.0,
            key=f"lend_amount_{transaction_id}",
        )

    settled = st.checkbox("Settled", value=bool(row["lent_settled"]), key=f"settled_checkbox_{transaction_id}")

    if st.button("Save", key=f"save_lend_{transaction_id}", width="stretch"):
        set_lent_amount(owner_email, transaction_id, new_lent_amount)
        if settled != bool(row["lent_settled"]):
            set_lending_settled(owner_email, transaction_id, settled)
        _close_dialog()
        st.rerun()


if selected_data is not None and not selected_data.empty:
    selected_id = int(selected_data.iloc[0]["id"])
    matched = df[df["id"] == selected_id]
    if not matched.empty:
        _edit_lending_dialog(matched.iloc[0])
