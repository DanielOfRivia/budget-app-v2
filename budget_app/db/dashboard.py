import pandas as pd

from budget_app.db.engine import get_connection


def get_dashboard_transactions(owner_email: str, start_date=None, end_date=None) -> pd.DataFrame:
    """Owner-scoped transactions with adjusted (lend-excluded) amounts, for
    dashboard aggregation. Reads from the transactions_full view so adjusted
    spend automatically reflects any splits once the lending feature lands."""
    conn = get_connection()
    sql = """
        SELECT date, merchant, category, account_name, amount, adjusted_amount, lent_total
        FROM transactions_full
        WHERE owner_email = :owner
    """
    params = {"owner": owner_email}
    if start_date is not None:
        sql += " AND date >= :start_date"
        params["start_date"] = start_date
    if end_date is not None:
        sql += " AND date <= :end_date"
        params["end_date"] = end_date
    sql += " ORDER BY date"

    return conn.query(sql, params=params, ttl=0)
