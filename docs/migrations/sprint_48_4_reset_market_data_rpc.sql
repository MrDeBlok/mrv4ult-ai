-- Sprint 48.4: admin market data reset preview RPC.
-- Idempotent: safe to run multiple times in Supabase SQL Editor.
-- EXECUTE deletes are handled by the app batched reset (/admin/reset-market-data/emergency)
-- because monolithic DELETE/TRUNCATE hits pg_safeupdate (21000) and statement timeouts (57014)
-- on databases with 100k+ offers.

DROP FUNCTION IF EXISTS reset_market_data_admin();

CREATE OR REPLACE FUNCTION reset_market_data_admin(dry_run boolean DEFAULT false)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    offers_total bigint := 0;
    offers_active bigint := 0;
    import_logs_total bigint := 0;
    messages_total bigint := 0;
    request_matches_total bigint := 0;
    market_notifications_total bigint := 0;
BEGIN
    SELECT COUNT(*) INTO offers_total FROM offers;
    SELECT COUNT(*) INTO offers_active FROM offers WHERE status = 'active';
    SELECT COUNT(*) INTO import_logs_total FROM import_logs;
    SELECT COUNT(*) INTO messages_total FROM messages;
    SELECT COUNT(*) INTO request_matches_total FROM request_matches;
    SELECT COUNT(*) INTO market_notifications_total
    FROM notifications
    WHERE related_offer_id IS NOT NULL
       OR related_import_log_id IS NOT NULL
       OR type IN (
           'request_match',
           'new_lowest_price',
           'excellent_buy',
           'needs_review'
       );

    IF dry_run THEN
        RETURN jsonb_build_object(
            'method', 'rpc',
            'dry_run', true,
            'verification', jsonb_build_object(
                'offers_total', offers_total,
                'offers_active', offers_active,
                'import_logs_total', import_logs_total,
                'messages_total', messages_total,
                'request_matches_total', request_matches_total,
                'market_notifications_total', market_notifications_total
            ),
            'success', false
        );
    END IF;

    RETURN jsonb_build_object(
        'method', 'rpc',
        'dry_run', false,
        'error', 'Use app batched reset at /admin/reset-market-data/emergency',
        'verification', jsonb_build_object(
            'offers_total', offers_total,
            'offers_active', offers_active,
            'import_logs_total', import_logs_total,
            'messages_total', messages_total,
            'request_matches_total', request_matches_total,
            'market_notifications_total', market_notifications_total
        ),
        'success', false
    );
END;
$$;

COMMENT ON FUNCTION reset_market_data_admin(boolean) IS
    'Admin market data reset preview. Pass dry_run=true to count only. Execute via app batched reset.';

REVOKE ALL ON FUNCTION reset_market_data_admin(boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION reset_market_data_admin(boolean) TO service_role;
