-- Sprint 50.0: Offer-centric parser training rows

CREATE TABLE IF NOT EXISTS parser_training_rows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    import_log_id UUID NOT NULL REFERENCES import_logs (id) ON DELETE CASCADE,
    source_message_id UUID REFERENCES messages (id) ON DELETE SET NULL,
    row_index INTEGER NOT NULL,
    raw_row_text TEXT NOT NULL DEFAULT '',
    detected_brand TEXT,
    detected_reference TEXT,
    detected_condition TEXT,
    detected_year TEXT,
    detected_card_date TEXT,
    detected_price NUMERIC,
    detected_currency TEXT,
    normalized_brand TEXT,
    normalized_reference TEXT,
    normalized_condition TEXT,
    usd_price NUMERIC,
    confidence_overall NUMERIC,
    confidence_brand NUMERIC,
    confidence_reference NUMERIC,
    confidence_condition NUMERIC,
    confidence_price NUMERIC,
    confidence_intent NUMERIC,
    parser_explanation JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending_review',
    issue_types TEXT[] NOT NULL DEFAULT '{}',
    created_offer_id UUID REFERENCES offers (id) ON DELETE SET NULL,
    created_by_user_id UUID REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT parser_training_rows_status_check
        CHECK (status IN ('pending_review', 'approved', 'corrected', 'ignored', 'failed')),
    CONSTRAINT parser_training_rows_import_row_unique
        UNIQUE (import_log_id, row_index)
);

CREATE INDEX IF NOT EXISTS idx_parser_training_rows_import_log_id
    ON parser_training_rows (import_log_id);

CREATE INDEX IF NOT EXISTS idx_parser_training_rows_status
    ON parser_training_rows (status);

CREATE INDEX IF NOT EXISTS idx_parser_training_rows_import_status
    ON parser_training_rows (import_log_id, status);

CREATE INDEX IF NOT EXISTS idx_parser_training_rows_created_offer_id
    ON parser_training_rows (created_offer_id)
    WHERE created_offer_id IS NOT NULL;
