import hashlib

import pandas as pd
from sqlalchemy import bindparam, text

from budget_app.db.accounts import _get_or_create_account
from budget_app.db.engine import get_connection


def _compute_external_ids(df: pd.DataFrame) -> pd.Series:
    """Deterministic dedup key for CSV-sourced rows: date+merchant+amount+account,
    disambiguated by occurrence order so re-uploading the same statement produces
    the same sequence of ids (and therefore dedups on ON CONFLICT)."""
    date_str = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    amount_str = df["amount"].astype(float).map(lambda v: f"{v:.2f}")
    merchant_str = df["merchant"].astype(str)
    account_str = df["account"].astype(str)

    occurrence = (
        pd.DataFrame({"d": date_str, "m": merchant_str, "a": amount_str, "acc": account_str})
        .groupby(["d", "m", "a", "acc"])
        .cumcount()
        .astype(str)
    )

    raw = date_str + "|" + merchant_str + "|" + amount_str + "|" + account_str + "|" + occurrence
    return raw.map(lambda s: hashlib.sha256(s.encode()).hexdigest())


def insert_transactions(owner_email: str, df: pd.DataFrame) -> dict:
    """Insert a batch of transactions for owner_email, skipping any that already
    exist (same account + external_id). Returns {"inserted": n, "skipped": n}."""
    if df.empty:
        return {"inserted": 0, "skipped": 0}

    working = df.copy()
    if "source" not in working.columns:
        working["source"] = "csv"
    if "external_id" not in working.columns:
        working["external_id"] = _compute_external_ids(working)

    conn = get_connection()
    inserted = 0
    skipped = 0
    with conn.session as session:
        for row in working.to_dict("records"):
            account_id = _get_or_create_account(session, owner_email, str(row["account"]))
            result = session.execute(
                text(
                    """
                    INSERT INTO transactions
                        (date, merchant, amount, account_id, category, source_file, notes, external_id, source)
                    VALUES
                        (:date, :merchant, :amount, :account_id, :category, :source_file, :notes, :external_id, :source)
                    ON CONFLICT (account_id, external_id) DO NOTHING
                    """
                ),
                {
                    "date": row["date"],
                    "merchant": row["merchant"],
                    "amount": row["amount"],
                    "account_id": account_id,
                    "category": row.get("category") or None,
                    "source_file": row.get("source_file"),
                    "notes": row.get("notes") or None,
                    "external_id": row["external_id"],
                    "source": row.get("source", "csv"),
                },
            )
            if result.rowcount:
                inserted += 1
            else:
                skipped += 1
        session.commit()

    return {"inserted": inserted, "skipped": skipped}


def list_transactions(owner_email: str, limit: int = 50) -> pd.DataFrame:
    conn = get_connection()
    return conn.query(
        """
        SELECT t.date, t.merchant, t.amount, a.name AS account, t.category,
               t.source_file, t.notes, t.source, t.created_at
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE a.owner_email = :owner
        ORDER BY t.created_at DESC
        LIMIT :limit
        """,
        params={"owner": owner_email, "limit": limit},
        ttl=0,
    )


def get_known_categories(merchants: list) -> dict:
    """Categories already assigned to these merchant names in past transactions
    (any owner), used to avoid re-asking Gemini for merchants we've already seen."""
    if not merchants:
        return {}

    conn = get_connection()
    query = text(
        """
        SELECT DISTINCT ON (merchant) merchant, category
        FROM transactions
        WHERE category IS NOT NULL AND category <> '' AND merchant IN :merchants
        ORDER BY merchant, created_at DESC
        """
    ).bindparams(bindparam("merchants", expanding=True))

    with conn.session as session:
        rows = session.execute(query, {"merchants": merchants}).fetchall()

    return {row.merchant: row.category for row in rows}
