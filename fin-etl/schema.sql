-- Schema for the Financial Data Validation Pipeline.
-- Safe to run repeatedly (everything is IF NOT EXISTS).

-- One row per pipeline execution. Gives every loaded and rejected record
-- a run to point back to (an audit trail).
CREATE TABLE IF NOT EXISTS load_runs (
    run_id         BIGSERIAL PRIMARY KEY,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    source_files   TEXT[]      NOT NULL,
    rows_read      INTEGER     NOT NULL DEFAULT 0,
    rows_loaded    INTEGER     NOT NULL DEFAULT 0,
    rows_skipped   INTEGER     NOT NULL DEFAULT 0,  -- identical row already in the DB
    rows_rejected  INTEGER     NOT NULL DEFAULT 0,
    batch_warnings JSONB       NOT NULL DEFAULT '[]'::jsonb
);

-- The clean, validated data. The constraints here are the LAST line of
-- defence: Python validation should make them unreachable, but if it has a
-- bug the database refuses the bad row instead of storing it.
CREATE TABLE IF NOT EXISTS transactions (
    txn_id      TEXT          PRIMARY KEY,           -- business key from the source system
    account_id  TEXT          NOT NULL,
    txn_date    DATE          NOT NULL,
    amount      NUMERIC(18,2) NOT NULL,              -- exact decimal, never float. Credits > 0, debits < 0
    currency    CHAR(3)       NOT NULL,              -- ISO 4217 code, e.g. INR
    txn_type    TEXT          NOT NULL,
    description TEXT,
    source_file TEXT          NOT NULL,
    run_id      BIGINT        NOT NULL REFERENCES load_runs (run_id),
    loaded_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT chk_currency_format   CHECK (currency ~ '^[A-Z]{3}$'),
    CONSTRAINT chk_txn_type          CHECK (txn_type IN ('DEBIT', 'CREDIT')),
    CONSTRAINT chk_amount_nonzero    CHECK (amount <> 0),
    CONSTRAINT chk_sign_matches_type CHECK (
        (txn_type = 'CREDIT' AND amount > 0) OR (txn_type = 'DEBIT' AND amount < 0)
    )
);

CREATE INDEX IF NOT EXISTS idx_transactions_account_date
    ON transactions (account_id, txn_date);

-- Quarantine: every record that failed validation, with the reasons and the
-- original values, so nothing is silently dropped.
CREATE TABLE IF NOT EXISTS rejected_records (
    reject_id     BIGSERIAL PRIMARY KEY,
    run_id        BIGINT  NOT NULL REFERENCES load_runs (run_id),
    source_file   TEXT    NOT NULL,
    source_row    INTEGER NOT NULL,                   -- 1-based position inside the source file
    txn_id        TEXT,                               -- may be NULL: the id itself can be the missing field
    error_codes   TEXT[]  NOT NULL,
    error_details TEXT    NOT NULL,
    raw_record    JSONB   NOT NULL                    -- the values exactly as they arrived
);

CREATE INDEX IF NOT EXISTS idx_rejected_run ON rejected_records (run_id);
