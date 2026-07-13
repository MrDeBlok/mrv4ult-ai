-- Sprint 50.4: Indexes for reference-based parser training row lookups

-- Backfill normalized_reference so indexed lookups do not need detected_reference OR scans.
UPDATE parser_training_rows
SET normalized_reference = UPPER(TRIM(detected_reference))
WHERE normalized_reference IS NULL
  AND detected_reference IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_parser_training_rows_normalized_reference
    ON parser_training_rows (normalized_reference)
    WHERE normalized_reference IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_parser_training_rows_detected_reference
    ON parser_training_rows (detected_reference)
    WHERE detected_reference IS NOT NULL;

-- Composite index supports equality filter + ORDER BY id without a separate sort step.
CREATE INDEX IF NOT EXISTS idx_parser_training_rows_norm_reference_id
    ON parser_training_rows (normalized_reference, id)
    WHERE normalized_reference IS NOT NULL;

-- reference_brand_mappings.reference_key already has a UNIQUE constraint (implicit index)

-- Verify plan after applying (Supabase SQL editor):
-- EXPLAIN (ANALYZE, BUFFERS)
-- SELECT id, status, created_offer_id, import_log_id, source_message_id, row_index,
--        raw_row_text, detected_brand, detected_reference, detected_condition,
--        detected_year, detected_card_date, detected_price, detected_currency,
--        normalized_brand, normalized_reference, normalized_condition, usd_price
-- FROM parser_training_rows
-- WHERE normalized_reference = '5524G'
-- ORDER BY id
-- LIMIT 50 OFFSET 0;
