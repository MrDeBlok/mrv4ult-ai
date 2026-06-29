-- Sprint 37.2 — Team management, viewer role, user status
-- Safe to re-run in Supabase SQL Editor.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;

ALTER TABLE users
    DROP CONSTRAINT IF EXISTS users_role_check;

ALTER TABLE users
    ADD CONSTRAINT users_role_check
        CHECK (role IN ('admin', 'trader', 'viewer'));

ALTER TABLE users
    DROP CONSTRAINT IF EXISTS users_status_check;

ALTER TABLE users
    ADD CONSTRAINT users_status_check
        CHECK (status IN ('active', 'disabled'));

UPDATE users
SET status = 'active'
WHERE status IS NULL OR status = '';
