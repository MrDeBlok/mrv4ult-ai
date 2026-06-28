-- Sprint 33 — Users and private contact/import ownership
-- Safe to re-run in Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS users (
    id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT            NOT NULL,
    email       TEXT            NOT NULL UNIQUE,
    role        TEXT            NOT NULL,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT now(),

    CONSTRAINT users_role_check
        CHECK (role IN ('admin', 'trader'))
);

ALTER TABLE import_logs
    ADD COLUMN IF NOT EXISTS imported_by_user_id UUID REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS imported_by_user_id UUID REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE dealers
    ADD COLUMN IF NOT EXISTS owner_user_id UUID REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE dealers
    ADD COLUMN IF NOT EXISTS classified_by_user_id UUID REFERENCES users (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_import_logs_imported_by_user_id
    ON import_logs (imported_by_user_id);

CREATE INDEX IF NOT EXISTS idx_dealers_owner_user_id
    ON dealers (owner_user_id);

INSERT INTO users (id, name, email, role)
VALUES
    ('11111111-1111-4111-8111-111111111111', 'Admin User', 'admin@mrvault.local', 'admin'),
    ('22222222-2222-4222-8222-222222222222', 'Trader One', 'trader1@mrvault.local', 'trader'),
    ('33333333-3333-4333-8333-333333333333', 'Trader Two', 'trader2@mrvault.local', 'trader')
ON CONFLICT (email) DO NOTHING;
