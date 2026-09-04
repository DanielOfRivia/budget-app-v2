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
    date              DATE NOT NULL,           -- posted date: when the bank booked it
    -- When the card was actually presented. Plaid supplies this on ~100% of
    -- rows eventually, but fills it in late, so the newest rows can still be
    -- NULL; CSV imports never have it. Everything user-facing reads
    -- occurred_on (below), which falls back to `date`. Kept because it
    -- differs from the posted date on ~83% of rows and lands in a different
    -- month on ~4% — the posted date is what a card statement bills by, so
    -- it's the only column that can reconcile against one.
    authorized_date   DATE,
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
    -- Optional, one-to-one: a negative-amount row (a refund/credit) can
    -- point at the positive-amount purchase it refunds. UNIQUE means at
    -- most one refund per purchase — deliberately simplified, not a
    -- many-refunds-to-one-purchase model (see conversation history for why).
    -- Left NULL, most negative amounts are credits/rewards with no purchase
    -- to link to (e.g. cashback, welcome bonuses) — this is opt-in, not
    -- inferred or required.
    refund_of_transaction_id BIGINT REFERENCES transactions(id) UNIQUE,
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
    -- A linked refund nets against its original purchase (which carries the
    -- refunder's negative amount), so the refund row itself contributes 0 —
    -- otherwise the pair would be double-counted across two months.
    (CASE
        WHEN t.refund_of_transaction_id IS NOT NULL THEN 0
        ELSE t.amount - t.lent_amount + COALESCE(refunder.amount, 0)
     END)::numeric AS adjusted_amount,
    (t.lent_amount > 0 AND NOT t.lent_settled) AS has_unsettled_lend,
    a.owner_email,
    t.lent_settled,
    t.lent_settled_date,
    t.refund_of_transaction_id,
    -- The date used for all trend/aggregation bucketing: when the charge
    -- actually happened, and for a linked refund the date its *original
    -- purchase* happened, so the refund nets against that month rather than
    -- its own. Never the posted date.
    COALESCE(orig.authorized_date, orig.date, t.authorized_date, t.date) AS effective_date,
    -- Forward link (this row IS a refund of something):
    orig.merchant AS refund_of_merchant,
    COALESCE(orig.authorized_date, orig.date) AS refund_of_date,
    -- Reverse link (this row HAS BEEN refunded by something). Safe as a
    -- plain join because refund_of_transaction_id is UNIQUE — at most one
    -- refund per purchase, so this can't multiply rows.
    refunder.amount AS refunded_by_amount,
    COALESCE(refunder.authorized_date, refunder.date) AS refunded_by_date,
    refunder.id AS refunded_by_transaction_id,
    refunder.merchant AS refunded_by_merchant,
    -- The single date the whole app displays and filters on.
    COALESCE(t.authorized_date, t.date) AS occurred_on
FROM transactions t
JOIN accounts a ON a.id = t.account_id
LEFT JOIN transactions orig ON orig.id = t.refund_of_transaction_id
LEFT JOIN transactions refunder ON refunder.refund_of_transaction_id = t.id;
