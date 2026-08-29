import pandas as pd
from sqlalchemy import text

from budget_app.db.engine import get_connection


def _get_or_create_account(session, owner_email: str, name: str) -> int:
    """Look up or create an account row within an existing session/transaction."""
    row = session.execute(
        text("SELECT id FROM accounts WHERE owner_email = :owner AND name = :name"),
        {"owner": owner_email, "name": name},
    ).fetchone()
    if row:
        return row[0]

    row = session.execute(
        text(
            """
            INSERT INTO accounts (owner_email, name)
            VALUES (:owner, :name)
            ON CONFLICT (owner_email, name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """
        ),
        {"owner": owner_email, "name": name},
    ).fetchone()
    return row[0]


def get_or_create_account(owner_email: str, name: str) -> int:
    conn = get_connection()
    with conn.session as session:
        account_id = _get_or_create_account(session, owner_email, name)
        session.commit()
        return account_id


def list_accounts(owner_email: str) -> pd.DataFrame:
    conn = get_connection()
    return conn.query(
        "SELECT id, name, institution_name FROM accounts WHERE owner_email = :owner ORDER BY name",
        params={"owner": owner_email},
        ttl=0,
    )
