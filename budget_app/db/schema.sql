-- Budget app schema. Run this once, manually, in your Postgres provider's SQL console
-- (Neon/Supabase). The app does not run migrations on its own.

CREATE TABLE plaid_items (
    item_id          TEXT PRIMARY KEY,
    owner_email      TEXT NOT NULL,
    access_token     TEXT NOT NULL,          -- app-layer (Fernet) encrypted, wired in the Plaid stage
    institution_name TEXT,
    cursor           TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_plaid_items_owner ON plaid_items(owner_email);

CREATE TABLE accounts (
    id               BIGSERIAL PRIMARY KEY,
    owner_email      TEXT NOT NULL,
    name             TEXT NOT NULL,
    plaid_account_id TEXT UNIQUE,
    plaid_item_id    TEXT REFERENCES plaid_items(item_id),
    institution_name TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_email, name)
);
CREATE INDEX idx_accounts_owner ON accounts(owner_email);

CREATE TABLE transactions (
    id                BIGSERIAL PRIMARY KEY,
    date              DATE NOT NULL,
    merchant          TEXT NOT NULL,
    amount            NUMERIC(12,2) NOT NULL,   -- "actual" full card amount
    account_id        BIGINT NOT NULL REFERENCES accounts(id),
    category          TEXT,
    source_file       TEXT,
    notes             TEXT,
    external_id       TEXT,                     -- Plaid transaction_id, or a hash for CSV rows
    source            TEXT NOT NULL DEFAULT 'csv' CHECK (source IN ('csv', 'plaid', 'manual')),
    -- Lending: a single amount lent out against this transaction, no
    -- per-person split — adjusted = amount - lent_amount, unconditionally
    -- (regardless of settled status). No cap tying lent_amount to amount:
    -- amount can be negative (refunds), which would make that check invalid.
    lent_amount       NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (lent_amount >= 0),
    lent_settled      BOOLEAN NOT NULL DEFAULT false,
    lent_settled_date DATE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, external_id)
);
CREATE INDEX idx_transactions_date ON transactions(date);
CREATE INDEX idx_transactions_category ON transactions(category);

CREATE OR REPLACE VIEW transactions_full AS
SELECT
    t.id, t.date, t.merchant, t.amount, t.account_id, t.category,
    t.source_file, t.notes, t.external_id, t.source, t.created_at, t.updated_at,
    a.name AS account_name,
    t.lent_amount::numeric AS lent_total,
    (t.amount - t.lent_amount)::numeric AS adjusted_amount,
    (t.lent_amount > 0 AND NOT t.lent_settled) AS has_unsettled_lend,
    a.owner_email,
    t.lent_settled,
    t.lent_settled_date
FROM transactions t
JOIN accounts a ON a.id = t.account_id;
