-- Sprint 46.2: track who created each client request for edit/delete permissions.

ALTER TABLE requests
    ADD COLUMN IF NOT EXISTS created_by_user_id UUID REFERENCES users (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_requests_created_by_user_id
    ON requests (created_by_user_id);
