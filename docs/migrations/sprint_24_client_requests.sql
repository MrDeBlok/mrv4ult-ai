-- MRV4ULT AI — Sprint 24 migration
-- Client requests / wanted list + request_matches
--
-- Run this entire script once in the Supabase SQL Editor.
-- Safe to re-run: uses IF NOT EXISTS / IF EXISTS where possible.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Ensure requests table exists (fresh databases)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS requests (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    client_name     TEXT            NOT NULL DEFAULT 'Unknown client',
    brand           TEXT,
    reference       TEXT,
    model           TEXT,
    alias           TEXT,
    dial            TEXT,
    condition       TEXT,
    min_year        INTEGER,
    max_year        INTEGER,
    max_price       INTEGER,
    currency        TEXT,
    notes           TEXT,
    status          TEXT            NOT NULL DEFAULT 'open',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 2. Upgrade legacy requests columns (pre-Sprint 24 schema)
-- ---------------------------------------------------------------------------

ALTER TABLE requests ADD COLUMN IF NOT EXISTS alias TEXT;
ALTER TABLE requests ADD COLUMN IF NOT EXISTS min_year INTEGER;
ALTER TABLE requests ADD COLUMN IF NOT EXISTS max_year INTEGER;
ALTER TABLE requests ADD COLUMN IF NOT EXISTS currency TEXT;

-- Backfill currency from legacy max_currency when present
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'requests'
          AND column_name = 'max_currency'
    ) THEN
        EXECUTE $sql$
            UPDATE requests
            SET currency = max_currency
            WHERE currency IS NULL
              AND max_currency IS NOT NULL
        $sql$;
    END IF;
END $$;

-- Ensure every row has a client name before enforcing NOT NULL
UPDATE requests
SET client_name = 'Unknown client'
WHERE client_name IS NULL
   OR btrim(client_name) = '';

ALTER TABLE requests ALTER COLUMN client_name SET DEFAULT 'Unknown client';
ALTER TABLE requests ALTER COLUMN client_name SET NOT NULL;

-- Normalize legacy status values to Sprint 24 vocabulary
UPDATE requests SET status = 'open'    WHERE status = 'active';
UPDATE requests SET status = 'closed'  WHERE status IN ('fulfilled', 'cancelled');
UPDATE requests SET status = 'open'    WHERE status IS NULL OR btrim(status) = '';

ALTER TABLE requests ALTER COLUMN status SET DEFAULT 'open';

-- Replace legacy status constraint
ALTER TABLE requests DROP CONSTRAINT IF EXISTS requests_status_check;
ALTER TABLE requests DROP CONSTRAINT IF EXISTS requests_source_check;

ALTER TABLE requests
    ADD CONSTRAINT requests_status_check
    CHECK (status IN ('open', 'matched', 'closed', 'active'));

-- ---------------------------------------------------------------------------
-- 3. Create request_matches table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS request_matches (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id      UUID            NOT NULL REFERENCES requests (id) ON DELETE CASCADE,
    offer_id        UUID            NOT NULL REFERENCES offers (id) ON DELETE CASCADE,
    import_log_id   UUID            REFERENCES import_logs (id) ON DELETE SET NULL,
    match_strength  TEXT            NOT NULL,
    match_reason    TEXT            NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),

    CONSTRAINT request_matches_strength_check
        CHECK (match_strength IN ('strong', 'medium')),

    CONSTRAINT request_matches_unique UNIQUE (request_id, offer_id)
);

-- ---------------------------------------------------------------------------
-- 4. Indexes
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_requests_status
    ON requests (status);

CREATE INDEX IF NOT EXISTS idx_requests_brand_reference
    ON requests (brand, reference)
    WHERE brand IS NOT NULL AND reference IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_request_matches_request_id
    ON request_matches (request_id);

CREATE INDEX IF NOT EXISTS idx_request_matches_offer_id
    ON request_matches (offer_id);

CREATE INDEX IF NOT EXISTS idx_request_matches_import_log_id
    ON request_matches (import_log_id)
    WHERE import_log_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 5. Updated-at trigger for requests
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_requests_updated_at ON requests;

CREATE TRIGGER trg_requests_updated_at
    BEFORE UPDATE ON requests
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;
