-- Sprint 41.1 — Import status for insufficient watch evidence
-- Safe to re-run in Supabase SQL Editor.

ALTER TABLE import_logs
    DROP CONSTRAINT IF EXISTS import_logs_status_check;

ALTER TABLE import_logs
    ADD CONSTRAINT import_logs_status_check
        CHECK (status IN (
            'success',
            'no_watch_detected',
            'warning',
            'error',
            'noise',
            'request_intent',
            'insufficient_evidence'
        ));
