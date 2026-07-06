-- Sprint 48.5.2: durable WhatsApp source link on offers

ALTER TABLE offers
    ADD COLUMN IF NOT EXISTS source_import_log_id UUID
        REFERENCES import_logs (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_offers_source_import_log_id
    ON offers (source_import_log_id)
    WHERE source_import_log_id IS NOT NULL;
