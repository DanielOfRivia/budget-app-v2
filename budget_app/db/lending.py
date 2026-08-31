import pandas as pd
from sqlalchemy import text

from budget_app.db.engine import get_connection


def list_transactions_with_lending(owner_email: str, unsettled_only: bool = False, start_date=None) -> pd.DataFrame:
    conn = get_connection()
    sql = """
        SELECT id, date, merchant, account_name, category, amount, lent_total,
               adjusted_amount, lent_settled, lent_settled_date, has_unsettled_lend
        FROM transactions_full
        WHERE owner_email = :owner
    """
    params = {"owner": owner_email}
    if unsettled_only:
        sql += " AND has_unsettled_lend = true"
    if start_date is not None:
        sql += " AND date >= :start_date"
        params["start_date"] = start_date
    sql += " ORDER BY date DESC"

    return conn.query(sql, params=params, ttl=0)


def set_lent_amount(owner_email: str, transaction_id: int, lent_amount: float) -> None:
    """Set the lent amount for a transaction (ownership verified via the
    account join). Does not touch settled status — that's a separate action."""
    conn = get_connection()
    with conn.session as session:
        session.execute(
            text(
                """
                UPDATE transactions t
                SET lent_amount = :lent_amount, updated_at = now()
                FROM accounts a
                WHERE t.account_id = a.id
                  AND a.owner_email = :owner
                  AND t.id = :transaction_id
                """
            ),
            {"lent_amount": lent_amount, "owner": owner_email, "transaction_id": transaction_id},
        )
        session.commit()


def set_lending_settled(owner_email: str, transaction_id: int, settled: bool) -> None:
    conn = get_connection()
    with conn.session as session:
        session.execute(
            text(
                """
                UPDATE transactions t
                SET lent_settled = :settled,
                    lent_settled_date = CASE WHEN :settled THEN CURRENT_DATE ELSE NULL END,
                    updated_at = now()
                FROM accounts a
                WHERE t.account_id = a.id
                  AND a.owner_email = :owner
                  AND t.id = :transaction_id
                """
            ),
            {"settled": settled, "owner": owner_email, "transaction_id": transaction_id},
        )
        session.commit()
