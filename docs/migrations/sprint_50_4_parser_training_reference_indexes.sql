-- Sprint 50.4: Indexes for reference-based parser training row lookups

CREATE INDEX IF NOT EXISTS idx_parser_training_rows_normalized_reference
    ON parser_training_rows (normalized_reference)
    WHERE normalized_reference IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_parser_training_rows_detected_reference
    ON parser_training_rows (detected_reference)
    WHERE detected_reference IS NOT NULL;

-- reference_brand_mappings.reference_key already has a UNIQUE constraint (implicit index)
