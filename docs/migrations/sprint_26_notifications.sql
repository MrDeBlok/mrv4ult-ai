-- Sprint 26: Notification center
-- Run in Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS notifications (
    id                      UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    type                    TEXT            NOT NULL,
    title                   TEXT            NOT NULL,
    message                 TEXT            NOT NULL,
    related_import_log_id   UUID            REFERENCES import_logs (id) ON DELETE SET NULL,
    related_request_id      UUID            REFERENCES requests (id) ON DELETE SET NULL,
    related_offer_id        UUID            REFERENCES offers (id) ON DELETE SET NULL,
    is_read                 BOOLEAN         NOT NULL DEFAULT false,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT now(),

    CONSTRAINT notifications_type_check
        CHECK (type IN ('request_match', 'new_lowest_price', 'excellent_buy', 'needs_review'))
);

CREATE INDEX IF NOT EXISTS idx_notifications_is_read_created_at
    ON notifications (is_read, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_notifications_created_at
    ON notifications (created_at DESC);
