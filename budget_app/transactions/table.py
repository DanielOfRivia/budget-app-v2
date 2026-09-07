import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder

from budget_app.db.transactions import (
    get_transaction,
    link_refund,
    search_refund_candidates,
    set_category,
    set_lending_settled,
    set_lent_amount,
    set_notes,
    unlink_refund,
)
from budget_app.transactions.categories import CATEGORIES


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


def render_transactions_table(owner_email: str, df: pd.DataFrame, key_prefix: str, height: int = 350) -> None:
    """AG Grid over `df` where clicking any row opens the same edit dialog
    used everywhere in the app — category, comments, lending, and refund
    linking all save through this one code path, so behavior can't drift
    between whichever page's table was clicked.

    `df` must carry every column transactions_full provides, plus a `date`
    column already resolved to whatever date the caller wants shown (the
    app-wide convention is occurred_on, not the raw posted date).

    `key_prefix` namespaces this table's session_state and component keys —
    Streamlit multipage apps share session_state across pages, so two
    tables (e.g. All Transactions and the dashboard drill-down) need
    separate grid selection/dialog state or they'd stomp on each other.
    """
    grid_gen_key = f"{key_prefix}_grid_generation"
    open_key = f"{key_prefix}_open_txn_id"
    dismissed_key = f"{key_prefix}_dismissed_txn_id"

    display_df = df.copy()
    display_df["Status"] = display_df.apply(_status, axis=1)
    display_df["Refund"] = display_df.apply(_refund_note, axis=1)
    display_df["date"] = pd.to_datetime(display_df["date"]).dt.strftime("%Y-%m-%d")
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

    if grid_gen_key not in st.session_state:
        st.session_state[grid_gen_key] = 0

    grid_response = AgGrid(
        display_df[table_columns],
        gridOptions=grid_options,
        update_on=["selectionChanged"],
        theme="streamlit",
        height=height,
        # AG Grid persists its selection client-side, keyed by this component
        # key — it's not something session_state can just be cleared to reset
        # like a native widget. The key only bumps for a jump-to-linked-row
        # navigation (see _jump_button); a plain Save leaves it alone so the
        # table's scroll position, sort, and column widths survive editing a
        # transaction — see _close_dialog for how the dialog still closes.
        key=f"{key_prefix}_grid_{st.session_state[grid_gen_key]}",
    )

    selected_data = grid_response.selected_data

    def _close_dialog(transaction_id):
        # Deliberately doesn't bump grid_gen_key: that would remount the
        # whole grid to clear its selection, losing scroll position, sort,
        # and column widths on every single save. Instead, remember which
        # row was just saved so the dispatch logic below skips reopening it
        # while it's still the (unchanged) selection — re-editing it again
        # needs a different row clicked first, same as the jump-navigation
        # path already requires.
        st.session_state[dismissed_key] = transaction_id
        st.session_state.pop(open_key, None)

    def _jump_button(label, target_id, current_id):
        """Streamlit can't render a real hyperlink that opens another row's
        dialog, so this is the practical equivalent: record which transaction
        to open, clear the grid's own selection so it doesn't reopen the row
        we're leaving, and rerun."""
        if st.button(label, key=f"jump_{current_id}_{target_id}", width="stretch"):
            st.session_state[open_key] = target_id
            st.session_state[grid_gen_key] += 1
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

        # A NULL notes column comes back from the DB as pandas NaN (a float),
        # and NaN is truthy in Python (`nan or ""` returns nan, not "") —
        # pd.isna is the actual null check needed here, or the widget renders
        # the string "nan" for every transaction that has no comment.
        current_notes = row.get("notes")
        current_notes = "" if pd.isna(current_notes) else str(current_notes)
        new_notes = st.text_input(
            "Comments", value=current_notes, key=f"notes_{transaction_id}", placeholder="Add a note…"
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
            st.divider()
            st.subheader("Lending")

            amount_key = f"lend_amount_{transaction_id}"
            mode_key = f"lend_mode_{transaction_id}"

            # Half/Full cover the two splits actually used in practice — set the
            # dollar field directly rather than making every lend go through the
            # percentage field just to hit 50 or 100. Setting session_state here,
            # before the radio/number_input below are instantiated, is what makes
            # the click take effect on this same rerun.
            half_col, full_col = st.columns(2)
            if half_col.button("½ Half", key=f"lend_half_{transaction_id}", width="stretch"):
                st.session_state[mode_key] = "Dollar amount"
                st.session_state[amount_key] = round(float(row["amount"]) / 2, 2)
            if full_col.button("Full", key=f"lend_full_{transaction_id}", width="stretch"):
                st.session_state[mode_key] = "Dollar amount"
                st.session_state[amount_key] = float(row["amount"])

            mode_col, amount_col = st.columns(2)
            with mode_col:
                input_mode = st.radio("Enter as", ["Dollar amount", "Percentage"], horizontal=True, key=mode_key)

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
                        key=amount_key,
                    )

            if input_mode == "Percentage":
                st.caption(f"= ${new_lent_amount:,.2f}")

            settled = st.checkbox(
                "Settled", value=bool(row["lent_settled"]), key=f"settled_checkbox_{transaction_id}"
            )

        if st.button("Save", key=f"save_lend_{transaction_id}", width="stretch"):
            if new_lent_amount is not None:
                set_lent_amount(owner_email, transaction_id, new_lent_amount)
            if new_category != row["category"]:
                set_category(owner_email, transaction_id, new_category)
            if new_notes != current_notes:
                set_notes(owner_email, transaction_id, new_notes)
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
            _close_dialog(transaction_id)
            st.rerun()

    jump_id = st.session_state.get(open_key)
    dismissed_id = st.session_state.get(dismissed_key)

    # A live grid selection always wins over a pending jump target. AG Grid's
    # selection persists client-side across reruns (same key), but open_key
    # only gets cleared on Save — dismissing the dialog with the X leaves it
    # set, and without this ordering every later row click would keep
    # reopening the jumped-to transaction instead of the one just clicked.
    if selected_data is not None and not selected_data.empty:
        selected_id = int(selected_data.iloc[0]["id"])
        if selected_id == dismissed_id:
            # Same row still selected right after its own Save — the grid
            # wasn't remounted, so this isn't a new click, it's the stale
            # selection.
            pass
        else:
            st.session_state.pop(dismissed_key, None)
            matched = df[df["id"] == selected_id]
            if not matched.empty:
                st.session_state.pop(open_key, None)
                _edit_transaction_dialog(matched.iloc[0])
    elif jump_id is not None:
        # Fetched by id rather than looked up in df, so jumping to a linked
        # transaction works even when the current filters/period exclude it.
        jumped_row = get_transaction(owner_email, jump_id)
        if jumped_row is not None:
            _edit_transaction_dialog(jumped_row)
        else:
            st.session_state.pop(open_key, None)
