-- Sprint 43.1 — import_logs list query indexes
-- Safe to re-run in Supabase SQL Editor.

CREATE INDEX IF NOT EXISTS idx_import_logs_import_time
    ON import_logs (import_time DESC);

CREATE INDEX IF NOT EXISTS idx_import_logs_status_import_time
    ON import_logs (status, import_time DESC);
