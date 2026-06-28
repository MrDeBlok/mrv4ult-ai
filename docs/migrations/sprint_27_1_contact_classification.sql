-- Sprint 27.1 — Contact classification for privacy-safe imports
-- Safe to re-run in Supabase SQL Editor.

ALTER TABLE dealers
    ADD COLUMN IF NOT EXISTS contact_type TEXT NOT NULL DEFAULT 'unknown';

DO $$
BEGIN
    ALTER TABLE dealers
        ADD CONSTRAINT dealers_contact_type_check
            CHECK (contact_type IN ('dealer', 'private', 'ignored', 'unknown'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_dealers_contact_type ON dealers (contact_type);

-- Preserve existing dealer visibility; new contacts default via ingest rules.
UPDATE dealers
SET contact_type = 'dealer'
WHERE contact_type = 'unknown';
