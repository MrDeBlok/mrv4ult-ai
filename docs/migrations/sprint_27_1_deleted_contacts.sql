-- Sprint 27.1 — Recoverable contact deletion
-- Safe to re-run in Supabase SQL Editor.

ALTER TABLE dealers
    DROP CONSTRAINT IF EXISTS dealers_contact_type_check;

ALTER TABLE dealers
    ADD CONSTRAINT dealers_contact_type_check
        CHECK (contact_type IN ('dealer', 'private', 'ignored', 'unknown', 'deleted'));
