-- Sprint 27.2 — Simplify contact types to dealer / client / removed
-- Safe to re-run in Supabase SQL Editor.
--
-- Order matters: drop the legacy check constraint before writing new
-- contact_type values, normalize legacy rows, then re-add the constraint.

ALTER TABLE dealers
    DROP CONSTRAINT IF EXISTS dealers_contact_type_check;

UPDATE dealers
SET contact_type = 'removed'
WHERE contact_type IN ('private', 'ignored', 'deleted')
   OR whatsapp_id = 'import-placeholder';

UPDATE dealers
SET contact_type = 'dealer'
WHERE contact_type = 'unknown'
  AND id IN (
      SELECT DISTINCT dealer_id
      FROM offers
      WHERE dealer_id IS NOT NULL
  );

UPDATE dealers
SET contact_type = 'removed'
WHERE contact_type = 'unknown';

ALTER TABLE dealers
    DROP CONSTRAINT IF EXISTS dealers_contact_type_check;

ALTER TABLE dealers
    ADD CONSTRAINT dealers_contact_type_check
        CHECK (contact_type IN ('dealer', 'client', 'removed'));

ALTER TABLE dealers
    ALTER COLUMN contact_type SET DEFAULT 'removed';
