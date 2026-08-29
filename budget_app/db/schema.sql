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
    id            BIGSERIAL PRIMARY KEY,
    date          DATE NOT NULL,
    merchant      TEXT NOT NULL,
    amount        NUMERIC(12,2) NOT NULL,   -- "actual" full card amount
    account_id    BIGINT NOT NULL REFERENCES accounts(id),
    category      TEXT,
    source_file   TEXT,
    notes         TEXT,
    external_id   TEXT,                     -- Plaid transaction_id, or a hash for CSV rows
    source        TEXT NOT NULL DEFAULT 'csv' CHECK (source IN ('csv', 'plaid', 'manual')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, external_id)
);
CREATE INDEX idx_transactions_date ON transactions(date);
CREATE INDEX idx_transactions_category ON transactions(category);

CREATE TABLE splits (
    id             BIGSERIAL PRIMARY KEY,
    transaction_id BIGINT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    person_name    TEXT NOT NULL,
    amount         NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    settled        BOOLEAN NOT NULL DEFAULT false,
    settled_date   DATE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_splits_transaction ON splits(transaction_id);

-- adjusted = actual minus ALL lent amounts, regardless of settled status
CREATE OR REPLACE VIEW transaction_lending AS
SELECT
    t.id AS transaction_id,
    t.amount AS actual_amount,
    COALESCE(SUM(s.amount), 0) AS lent_total,
    t.amount - COALESCE(SUM(s.amount), 0) AS adjusted_amount,
    COALESCE(bool_or(s.settled = false), false) AS has_unsettled_lend
FROM transactions t
LEFT JOIN splits s ON s.transaction_id = t.id
GROUP BY t.id, t.amount;

CREATE OR REPLACE VIEW transactions_full AS
SELECT t.*, a.name AS account_name,
       tl.lent_total, tl.adjusted_amount, tl.has_unsettled_lend,
       a.owner_email
FROM transactions t
JOIN accounts a ON a.id = t.account_id
JOIN transaction_lending tl ON tl.transaction_id = t.id;
