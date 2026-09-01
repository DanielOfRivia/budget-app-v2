import pandas as pd
from sqlalchemy import text

from budget_app.db.engine import get_connection
from budget_app.plaid.client import decrypt_token, encrypt_token


def save_plaid_item(owner_email: str, item_id: str, access_token: str, institution_name: str | None = None) -> None:
    """Store a newly-linked item. access_token is encrypted before it ever
    touches the database — it's the standing key to the whole linked account."""
    conn = get_connection()
    with conn.session as session:
        session.execute(
            text(
                """
                INSERT INTO plaid_items (item_id, owner_email, access_token, institution_name)
                VALUES (:item_id, :owner, :access_token, :institution_name)
                ON CONFLICT (item_id) DO UPDATE SET
                    access_token = EXCLUDED.access_token,
                    institution_name = EXCLUDED.institution_name,
                    updated_at = now()
                """
            ),
            {
                "item_id": item_id,
                "owner": owner_email,
                "access_token": encrypt_token(access_token),
                "institution_name": institution_name,
            },
        )
        session.commit()


def list_plaid_items(owner_email: str) -> pd.DataFrame:
    conn = get_connection()
    return conn.query(
        """
        SELECT item_id, institution_name, cursor, created_at
        FROM plaid_items
        WHERE owner_email = :owner
        ORDER BY created_at DESC
        """,
        params={"owner": owner_email},
        ttl=0,
    )


def get_decrypted_access_token(owner_email: str, item_id: str) -> str:
    conn = get_connection()
    with conn.session as session:
        row = session.execute(
            text("SELECT access_token FROM plaid_items WHERE item_id = :item_id AND owner_email = :owner"),
            {"item_id": item_id, "owner": owner_email},
        ).fetchone()
    if row is None:
        raise ValueError("Plaid item not found for this owner")
    return decrypt_token(row[0])


def update_cursor(owner_email: str, item_id: str, cursor: str) -> None:
    """Owner-scoped even though the only caller (sync_transactions) has
    already verified ownership via get_decrypted_access_token earlier in the
    same call — this function shouldn't rely on that being true forever."""
    conn = get_connection()
    with conn.session as session:
        session.execute(
            text(
                """
                UPDATE plaid_items SET cursor = :cursor, updated_at = now()
                WHERE item_id = :item_id AND owner_email = :owner
                """
            ),
            {"cursor": cursor, "item_id": item_id, "owner": owner_email},
        )
        session.commit()


def upsert_plaid_account(
    owner_email: str, item_id: str, plaid_account_id: str, name: str, institution_name: str | None
) -> int:
    """Get-or-create the accounts row for one Plaid sub-account, returning
    its internal id. Checked by plaid_account_id first (definitive re-link
    match) before falling back to the owner+name uniqueness used by the
    manual/CSV path, since a name collision there is a real possibility
    (e.g. a manually-created "AMEX" account later linked via Plaid too).

    The plaid_account_id lookup is also owner-scoped, even though a real
    Plaid account should never end up linked under two different owners in
    practice — if that ever did happen, this should error loudly on the
    UNIQUE constraint below rather than silently hand one owner's account
    row back for another owner's sync to write into."""
    conn = get_connection()
    with conn.session as session:
        row = session.execute(
            text("SELECT id FROM accounts WHERE plaid_account_id = :plaid_account_id AND owner_email = :owner"),
            {"plaid_account_id": plaid_account_id, "owner": owner_email},
        ).fetchone()
        if row:
            account_id = row[0]
        else:
            row = session.execute(
                text(
                    """
                    INSERT INTO accounts (owner_email, name, plaid_account_id, plaid_item_id, institution_name)
                    VALUES (:owner, :name, :plaid_account_id, :item_id, :institution_name)
                    ON CONFLICT (owner_email, name) DO UPDATE SET
                        plaid_account_id = EXCLUDED.plaid_account_id,
                        plaid_item_id = EXCLUDED.plaid_item_id,
                        institution_name = EXCLUDED.institution_name
                    RETURNING id
                    """
                ),
                {
                    "owner": owner_email,
                    "name": name,
                    "plaid_account_id": plaid_account_id,
                    "item_id": item_id,
                    "institution_name": institution_name,
                },
            ).fetchone()
            account_id = row[0]
        session.commit()
        return account_id
