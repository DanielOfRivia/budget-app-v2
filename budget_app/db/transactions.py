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


def _insert_transaction_row(session, account_id, row) -> bool:
    """Insert one transaction row against an already-resolved account_id.
    Returns True if inserted, False if skipped as a duplicate."""
    result = session.execute(
        text(
            """
            INSERT INTO transactions
                (date, authorized_date, merchant, amount, account_id, category, source_file, notes, external_id, source)
            VALUES
                (:date, :authorized_date, :merchant, :amount, :account_id, :category, :source_file, :notes, :external_id, :source)
            ON CONFLICT (account_id, external_id) DO NOTHING
            """
        ),
        {
            "date": row["date"],
            # CSV rows have no authorization date; NULL falls back to `date`
            # via the occurred_on column in transactions_full.
            "authorized_date": row.get("authorized_date"),
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
    return bool(result.rowcount)


def set_category(owner_email: str, transaction_id: int, category: str) -> None:
    """Correct a transaction's category after it's already been saved —
    ownership verified via the account join, same pattern as the lending
    setters below. Gemini/CSV categorization only ever runs pre-save; this is
    the only path to fix a category afterwards."""
    conn = get_connection()
    with conn.session as session:
        session.execute(
            text(
                """
                UPDATE transactions t
                SET category = :category, updated_at = now()
                FROM accounts a
                WHERE t.account_id = a.id
                  AND a.owner_email = :owner
                  AND t.id = :transaction_id
                """
            ),
            {"category": category, "owner": owner_email, "transaction_id": transaction_id},
        )
        session.commit()


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


def insert_transactions(owner_email: str, df: pd.DataFrame) -> dict:
    """Insert a batch of CSV-sourced transactions for owner_email, resolving
    each row's account by name, skipping any that already exist (same
    account + external_id). Returns {"inserted": n, "skipped": n}."""
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
            if _insert_transaction_row(session, account_id, row):
                inserted += 1
            else:
                skipped += 1
        session.commit()

    return {"inserted": inserted, "skipped": skipped}


def insert_transactions_by_account_id(rows: list) -> dict:
    """Insert pre-resolved rows (each a dict with an account_id already
    looked up, plus date/merchant/amount/external_id/etc.) — for sources
    like Plaid that resolve accounts themselves and already have a stable,
    Plaid-issued external_id rather than a computed CSV dedup hash."""
    if not rows:
        return {"inserted": 0, "skipped": 0}

    conn = get_connection()
    inserted = 0
    skipped = 0
    with conn.session as session:
        for row in rows:
            if _insert_transaction_row(session, row["account_id"], row):
                inserted += 1
            else:
                skipped += 1
        session.commit()

    return {"inserted": inserted, "skipped": skipped}


def delete_transactions_by_external_ids(owner_email: str, external_ids: list) -> int:
    """Delete this owner's transactions by external_id. Used for Plaid's
    `removed` list — a transaction Plaid has deleted on its side (commonly a
    pending charge that never posted, or a reversal) must be removed here too
    or it lingers forever and quietly skews totals. Scoped through the
    accounts join so one owner can never delete another's rows."""
    if not external_ids:
        return 0

    query = text(
        """
        DELETE FROM transactions t
        USING accounts a
        WHERE t.account_id = a.id
          AND a.owner_email = :owner
          AND t.external_id IN :external_ids
        """
    ).bindparams(bindparam("external_ids", expanding=True))

    conn = get_connection()
    with conn.session as session:
        result = session.execute(query, {"owner": owner_email, "external_ids": external_ids})
        session.commit()
        return result.rowcount or 0


def link_refund(owner_email: str, refund_transaction_id: int, original_transaction_id: int) -> None:
    """Mark refund_transaction_id (a negative-amount row) as a refund of
    original_transaction_id (a positive-amount row), same account only.
    Fetch-then-validate rather than baking the checks into the UPDATE's
    WHERE clause, so a bad link fails with a clear reason instead of a
    silent no-op update. Ownership is fully covered by the same-account
    check: an account belongs to exactly one owner, so if the original
    shares the refund's account, it's already provably the same owner's."""
    conn = get_connection()
    with conn.session as session:
        row = session.execute(
            text(
                """
                SELECT refund.account_id AS refund_account_id, refund.amount AS refund_amount,
                       orig.account_id AS orig_account_id, orig.amount AS orig_amount
                FROM transactions refund
                JOIN accounts a ON a.id = refund.account_id
                JOIN transactions orig ON orig.id = :original_id
                WHERE refund.id = :refund_id AND a.owner_email = :owner
                """
            ),
            {"refund_id": refund_transaction_id, "original_id": original_transaction_id, "owner": owner_email},
        ).fetchone()

        if row is None:
            raise ValueError("Transaction not found for this owner")
        if row.refund_amount >= 0:
            raise ValueError("Only a negative-amount transaction can be linked as a refund")
        if row.orig_account_id != row.refund_account_id:
            raise ValueError("A refund can only be linked to a purchase on the same account")
        if row.orig_amount <= 0:
            raise ValueError("A refund must be linked to a positive-amount purchase")

        session.execute(
            text(
                "UPDATE transactions SET refund_of_transaction_id = :original_id, updated_at = now() WHERE id = :refund_id"
            ),
            {"original_id": original_transaction_id, "refund_id": refund_transaction_id},
        )
        session.commit()


def unlink_refund(owner_email: str, refund_transaction_id: int) -> None:
    conn = get_connection()
    with conn.session as session:
        session.execute(
            text(
                """
                UPDATE transactions t
                SET refund_of_transaction_id = NULL, updated_at = now()
                FROM accounts a
                WHERE t.account_id = a.id AND a.owner_email = :owner AND t.id = :transaction_id
                """
            ),
            {"owner": owner_email, "transaction_id": refund_transaction_id},
        )
        session.commit()


def search_refund_candidates(owner_email: str, account_id: int, query: str, limit: int = 20) -> pd.DataFrame:
    """Positive-amount transactions on the given account matching a text
    search, for the refund-link picker — searches full history, not just
    whatever date range the dashboard happens to be filtered to."""
    conn = get_connection()
    return conn.query(
        """
        SELECT t.id, t.date, t.merchant, t.amount
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE a.owner_email = :owner
          AND t.account_id = :account_id
          AND t.amount > 0
          AND t.merchant ILIKE :query
        ORDER BY t.date DESC
        LIMIT :limit
        """,
        params={"owner": owner_email, "account_id": account_id, "query": f"%{query}%", "limit": limit},
        ttl=0,
    )


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


def list_transactions_with_lending(
    owner_email: str,
    unsettled_only: bool = False,
    start_date=None,
    end_date=None,
    categories: list = None,
    accounts: list = None,
    merchant_search: str = None,
    refund_state: str = None,
) -> pd.DataFrame:
    """refund_state: None (any), "linked" (is a refund or has one), or
    "unlinked" (neither side of a refund link).

    Filtered and ordered by occurred_on (when the charge actually happened),
    not the posted date — that's the date the UI shows, so a date-range filter
    has to agree with it or the visible rows won't match the range."""
    conn = get_connection()
    sql = """
        SELECT id, account_id, date, merchant, account_name, category, amount, lent_total,
               adjusted_amount, lent_settled, lent_settled_date, has_unsettled_lend,
               refund_of_transaction_id, refund_of_merchant, refund_of_date,
               refunded_by_amount, refunded_by_date, refunded_by_transaction_id, refunded_by_merchant, occurred_on
        FROM transactions_full
        WHERE owner_email = :owner
    """
    params = {"owner": owner_email}
    if unsettled_only:
        sql += " AND has_unsettled_lend = true"
    if start_date is not None:
        sql += " AND occurred_on >= :start_date"
        params["start_date"] = start_date
    if end_date is not None:
        sql += " AND occurred_on <= :end_date"
        params["end_date"] = end_date
    if categories:
        sql += " AND category = ANY(:categories)"
        params["categories"] = list(categories)
    if accounts:
        sql += " AND account_name = ANY(:accounts)"
        params["accounts"] = list(accounts)
    if merchant_search:
        sql += " AND merchant ILIKE :merchant_search"
        params["merchant_search"] = f"%{merchant_search}%"
    if refund_state == "linked":
        sql += " AND (refund_of_transaction_id IS NOT NULL OR refunded_by_amount IS NOT NULL)"
    elif refund_state == "unlinked":
        sql += " AND refund_of_transaction_id IS NULL AND refunded_by_amount IS NULL"
    sql += " ORDER BY occurred_on DESC"

    return conn.query(sql, params=params, ttl=0)


def get_transaction(owner_email: str, transaction_id: int):
    """Fetch one transaction by id, ignoring any list filters — used when
    jumping to a linked refund/purchase that the current filters might
    exclude. Returns a Series, or None if not found for this owner."""
    conn = get_connection()
    df = conn.query(
        """
        SELECT id, account_id, date, merchant, account_name, category, amount, lent_total,
               adjusted_amount, lent_settled, lent_settled_date, has_unsettled_lend,
               refund_of_transaction_id, refund_of_merchant, refund_of_date,
               refunded_by_amount, refunded_by_date, refunded_by_transaction_id, refunded_by_merchant, occurred_on
        FROM transactions_full
        WHERE owner_email = :owner AND id = :transaction_id
        """,
        params={"owner": owner_email, "transaction_id": transaction_id},
        ttl=0,
    )
    if df.empty:
        return None
    row = df.iloc[0].copy()
    row["date"] = pd.to_datetime(row["occurred_on"])
    return row


def list_transactions_for_period(owner_email: str, month_start, month_end, category: str = None) -> pd.DataFrame:
    """Transactions behind a single point on a dashboard chart.

    Filtered on effective_date, not date, to match how the charts bucket
    months — otherwise a refund linked to an earlier purchase would be
    missing from the very list you opened to explain that month's number."""
    conn = get_connection()
    sql = """
        SELECT occurred_on, merchant, category, account_name, amount, lent_total, adjusted_amount,
               refund_of_transaction_id, refunded_by_amount
        FROM transactions_full
        WHERE owner_email = :owner
          AND effective_date >= :month_start
          AND effective_date <= :month_end
    """
    params = {"owner": owner_email, "month_start": month_start, "month_end": month_end}
    if category is not None:
        sql += " AND category = :category"
        params["category"] = category
    sql += " ORDER BY adjusted_amount DESC, occurred_on DESC"

    return conn.query(sql, params=params, ttl=0)


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
