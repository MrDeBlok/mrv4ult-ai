-- Sprint 28 — Client CRM foundation
-- Safe to re-run in Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS client_profiles (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID            NOT NULL UNIQUE REFERENCES dealers (id) ON DELETE CASCADE,
    notes           TEXT,
    preferred_brands TEXT,
    preferred_models TEXT,
    budget_min      INTEGER,
    budget_max      INTEGER,
    preferred_condition TEXT,
    preferred_dial  TEXT,
    status          TEXT            NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),

    CONSTRAINT client_profiles_status_check
        CHECK (status IN ('active', 'inactive'))
);

CREATE INDEX IF NOT EXISTS idx_client_profiles_client_id ON client_profiles (client_id);
CREATE INDEX IF NOT EXISTS idx_client_profiles_status ON client_profiles (status);

ALTER TABLE requests
    ADD COLUMN IF NOT EXISTS client_id UUID REFERENCES dealers (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_requests_client_id ON requests (client_id);

DO $$
BEGIN
    CREATE TRIGGER client_profiles_set_updated_at
        BEFORE UPDATE ON client_profiles
        FOR EACH ROW
        EXECUTE FUNCTION set_updated_at();
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;
