-- Sprint 52.1: add original-currency filter to watch-reference detail RPC.
-- Idempotent: safe to run multiple times in Supabase SQL Editor.

DROP FUNCTION IF EXISTS get_watch_reference_offers_page(text, text, text, timestamptz, timestamptz, text, integer, integer, boolean);

CREATE OR REPLACE FUNCTION get_watch_reference_offers_page(
    p_brand text,
    p_reference text,
    p_condition_filter text DEFAULT NULL,
    p_date_from timestamptz DEFAULT NULL,
    p_date_to timestamptz DEFAULT NULL,
    p_sort_filter text DEFAULT '',
    p_page integer DEFAULT 1,
    p_page_limit integer DEFAULT 50,
    p_filter_business_dealers boolean DEFAULT TRUE,
    p_currency_filter text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SET search_path = public
AS $$
DECLARE
    safe_limit integer := LEAST(GREATEST(coalesce(p_page_limit, 50), 1), 50);
    safe_page integer := GREATEST(coalesce(p_page, 1), 1);
    safe_offset integer;
    total_matches bigint := 0;
    total_pages integer := 1;
    offer_rows jsonb := '[]'::jsonb;
    stats_json jsonb := '{}'::jsonb;
BEGIN
    WITH filtered AS (
        SELECT
            o.id,
            o.message_id,
            o.source_import_log_id,
            o.dealer_id,
            o.watch_id,
            o.original_price,
            o.original_currency,
            o.usd_price,
            o.price_review_status,
            o.price_review_reason,
            o.suggested_original_price,
            o.suggested_currency,
            o.price_confidence,
            o.corrected_original_price,
            o.corrected_original_currency,
            o.corrected_usd_price,
            o.card_date,
            o.condition,
            m.received_at,
            m.group_id,
            w.brand AS watch_brand,
            w.reference AS watch_reference,
            w.model AS watch_model,
            w.dial AS watch_dial,
            w.bracelet AS watch_bracelet,
            d.display_name AS dealer_display_name,
            d.phone_number AS dealer_phone_number,
            d.whatsapp_id AS dealer_whatsapp_id,
            d.contact_type AS dealer_contact_type,
            g.name AS group_name,
            offer_market_usd_price(o.usd_price, o.price_review_status, o.corrected_usd_price) AS market_usd_price
        FROM offers o
        INNER JOIN watches w ON w.id = o.watch_id
        INNER JOIN dealers d ON d.id = o.dealer_id
        LEFT JOIN messages m ON m.id = o.message_id
        LEFT JOIN groups g ON g.id = m.group_id
        WHERE o.status = 'active'
          AND watch_matches_brand_reference_group(
              w.brand,
              w.reference,
              p_brand,
              p_reference
          )
          AND (
              NOT p_filter_business_dealers
              OR dealer_is_search_visible(d.contact_type)
          )
          AND (
              p_condition_filter IS NULL
              OR btrim(p_condition_filter) = ''
              OR offer_condition_category_sql(o.condition) = p_condition_filter
          )
          AND (
              (p_date_from IS NULL AND p_date_to IS NULL)
              OR (
                  m.received_at IS NOT NULL
                  AND (p_date_from IS NULL OR m.received_at >= p_date_from)
                  AND (p_date_to IS NULL OR m.received_at < p_date_to)
              )
          )
          AND (
              p_currency_filter IS NULL
              OR btrim(p_currency_filter) = ''
              OR upper(btrim(o.original_currency)) = upper(btrim(p_currency_filter))
          )
    ),
    counted AS (
        SELECT COUNT(*) AS total_count
        FROM filtered
    ),
    stats AS (
        SELECT jsonb_build_object(
            'lowest_usd_price', MIN(f.market_usd_price) FILTER (WHERE f.market_usd_price IS NOT NULL),
            'average_usd_price', AVG(f.market_usd_price) FILTER (WHERE f.market_usd_price IS NOT NULL),
            'highest_usd_price', MAX(f.market_usd_price) FILTER (WHERE f.market_usd_price IS NOT NULL),
            'active_offer_count', COUNT(*),
            'unique_dealer_count', COUNT(DISTINCT f.dealer_id),
            'unique_group_count', COUNT(
                DISTINCT COALESCE(NULLIF(btrim(f.group_id::text), ''), NULLIF(btrim(f.group_name), ''), '')
            ) FILTER (
                WHERE COALESCE(NULLIF(btrim(f.group_id::text), ''), NULLIF(btrim(f.group_name), '')) IS NOT NULL
            ),
            'condition_counts', jsonb_build_object(
                'New', COUNT(*) FILTER (WHERE offer_condition_category_sql(f.condition) = 'New'),
                'Pre-Owned', COUNT(*) FILTER (WHERE offer_condition_category_sql(f.condition) = 'Pre-Owned'),
                'Unknown', COUNT(*) FILTER (WHERE offer_condition_category_sql(f.condition) = 'Unknown')
            )
        ) AS payload
        FROM filtered f
    )
    SELECT
        coalesce(c.total_count, 0),
        coalesce(s.payload, '{}'::jsonb)
    INTO total_matches, stats_json
    FROM counted c
    CROSS JOIN stats s;

    IF total_matches <= 0 THEN
        safe_page := 1;
        total_pages := 1;
        safe_offset := 0;
    ELSE
        total_pages := GREATEST(1, CEIL(total_matches::numeric / safe_limit::numeric)::integer);
        IF safe_page > total_pages THEN
            safe_page := total_pages;
        END IF;
        safe_offset := (safe_page - 1) * safe_limit;
    END IF;

    WITH filtered AS (
        SELECT
            o.id,
            o.message_id,
            o.source_import_log_id,
            o.dealer_id,
            o.watch_id,
            o.original_price,
            o.original_currency,
            o.usd_price,
            o.price_review_status,
            o.price_review_reason,
            o.suggested_original_price,
            o.suggested_currency,
            o.price_confidence,
            o.corrected_original_price,
            o.corrected_original_currency,
            o.corrected_usd_price,
            o.card_date,
            o.condition,
            m.received_at,
            m.group_id,
            w.brand AS watch_brand,
            w.reference AS watch_reference,
            w.model AS watch_model,
            w.dial AS watch_dial,
            w.bracelet AS watch_bracelet,
            d.display_name AS dealer_display_name,
            d.phone_number AS dealer_phone_number,
            d.whatsapp_id AS dealer_whatsapp_id,
            d.contact_type AS dealer_contact_type,
            g.name AS group_name,
            offer_market_usd_price(o.usd_price, o.price_review_status, o.corrected_usd_price) AS market_usd_price
        FROM offers o
        INNER JOIN watches w ON w.id = o.watch_id
        INNER JOIN dealers d ON d.id = o.dealer_id
        LEFT JOIN messages m ON m.id = o.message_id
        LEFT JOIN groups g ON g.id = m.group_id
        WHERE o.status = 'active'
          AND watch_matches_brand_reference_group(
              w.brand,
              w.reference,
              p_brand,
              p_reference
          )
          AND (
              NOT p_filter_business_dealers
              OR dealer_is_search_visible(d.contact_type)
          )
          AND (
              p_condition_filter IS NULL
              OR btrim(p_condition_filter) = ''
              OR offer_condition_category_sql(o.condition) = p_condition_filter
          )
          AND (
              (p_date_from IS NULL AND p_date_to IS NULL)
              OR (
                  m.received_at IS NOT NULL
                  AND (p_date_from IS NULL OR m.received_at >= p_date_from)
                  AND (p_date_to IS NULL OR m.received_at < p_date_to)
              )
          )
          AND (
              p_currency_filter IS NULL
              OR btrim(p_currency_filter) = ''
              OR upper(btrim(o.original_currency)) = upper(btrim(p_currency_filter))
          )
    )
    SELECT coalesce(
        jsonb_agg(
            jsonb_build_object(
                'id', p.id,
                'message_id', p.message_id,
                'source_import_log_id', p.source_import_log_id,
                'dealer_id', p.dealer_id,
                'watch_id', p.watch_id,
                'original_price', p.original_price,
                'original_currency', p.original_currency,
                'usd_price', p.usd_price,
                'price_review_status', p.price_review_status,
                'price_review_reason', p.price_review_reason,
                'suggested_original_price', p.suggested_original_price,
                'suggested_currency', p.suggested_currency,
                'price_confidence', p.price_confidence,
                'corrected_original_price', p.corrected_original_price,
                'corrected_original_currency', p.corrected_original_currency,
                'corrected_usd_price', p.corrected_usd_price,
                'card_date', p.card_date,
                'condition', p.condition,
                'watches', jsonb_build_object(
                    'brand', p.watch_brand,
                    'reference', p.watch_reference,
                    'model', p.watch_model,
                    'dial', p.watch_dial,
                    'bracelet', p.watch_bracelet
                ),
                'dealers', jsonb_build_object(
                    'display_name', p.dealer_display_name,
                    'phone_number', p.dealer_phone_number,
                    'whatsapp_id', p.dealer_whatsapp_id,
                    'contact_type', p.dealer_contact_type
                ),
                'messages', jsonb_build_object(
                    'id', p.message_id,
                    'received_at', p.received_at,
                    'group_id', p.group_id,
                    'groups', CASE
                        WHEN p.group_name IS NULL THEN NULL
                        ELSE jsonb_build_object('name', p.group_name)
                    END
                )
            )
            ORDER BY
                CASE
                    WHEN coalesce(lower(btrim(p_sort_filter)), '') IN ('price_asc', 'price_desc') THEN
                        offer_price_review_sort_rank(p.price_review_status)
                    ELSE 0
                END,
                CASE
                    WHEN coalesce(lower(btrim(p_sort_filter)), '') = 'price_asc' THEN
                        CASE WHEN p.market_usd_price IS NULL THEN 1 ELSE 0 END
                    WHEN coalesce(lower(btrim(p_sort_filter)), '') = 'price_desc' THEN
                        CASE WHEN p.market_usd_price IS NULL THEN 1 ELSE 0 END
                    ELSE
                        CASE WHEN p.received_at IS NULL THEN 1 ELSE 0 END
                END,
                CASE
                    WHEN coalesce(lower(btrim(p_sort_filter)), '') = 'price_asc' THEN p.market_usd_price
                    ELSE NULL
                END ASC NULLS LAST,
                CASE
                    WHEN coalesce(lower(btrim(p_sort_filter)), '') = 'price_desc' THEN p.market_usd_price
                    ELSE NULL
                END DESC NULLS LAST,
                CASE
                    WHEN coalesce(lower(btrim(p_sort_filter)), '') IN ('price_asc', 'price_desc') THEN NULL
                    ELSE p.received_at
                END DESC NULLS LAST,
                CASE
                    WHEN coalesce(lower(btrim(p_sort_filter)), '') IN ('price_asc', 'price_desc') THEN p.received_at
                    ELSE NULL
                END DESC NULLS LAST,
                CASE
                    WHEN coalesce(lower(btrim(p_sort_filter)), '') IN ('price_asc', 'price_desc') THEN NULL
                    ELSE p.market_usd_price
                END ASC NULLS LAST,
                p.id ASC
        ),
        '[]'::jsonb
    )
    INTO offer_rows
    FROM (
        SELECT *
        FROM filtered
        ORDER BY
            CASE
                WHEN coalesce(lower(btrim(p_sort_filter)), '') IN ('price_asc', 'price_desc') THEN
                    offer_price_review_sort_rank(price_review_status)
                ELSE 0
            END,
            CASE
                WHEN coalesce(lower(btrim(p_sort_filter)), '') = 'price_asc' THEN
                    CASE WHEN market_usd_price IS NULL THEN 1 ELSE 0 END
                WHEN coalesce(lower(btrim(p_sort_filter)), '') = 'price_desc' THEN
                    CASE WHEN market_usd_price IS NULL THEN 1 ELSE 0 END
                ELSE
                    CASE WHEN received_at IS NULL THEN 1 ELSE 0 END
            END,
            CASE
                WHEN coalesce(lower(btrim(p_sort_filter)), '') = 'price_asc' THEN market_usd_price
                ELSE NULL
            END ASC NULLS LAST,
            CASE
                WHEN coalesce(lower(btrim(p_sort_filter)), '') = 'price_desc' THEN market_usd_price
                ELSE NULL
            END DESC NULLS LAST,
            CASE
                WHEN coalesce(lower(btrim(p_sort_filter)), '') IN ('price_asc', 'price_desc') THEN NULL
                ELSE received_at
            END DESC NULLS LAST,
            CASE
                WHEN coalesce(lower(btrim(p_sort_filter)), '') IN ('price_asc', 'price_desc') THEN received_at
                ELSE NULL
            END DESC NULLS LAST,
            CASE
                WHEN coalesce(lower(btrim(p_sort_filter)), '') IN ('price_asc', 'price_desc') THEN NULL
                ELSE market_usd_price
            END ASC NULLS LAST,
            id ASC
        OFFSET safe_offset
        LIMIT safe_limit
    ) p;

    RETURN jsonb_build_object(
        'offers', coalesce(offer_rows, '[]'::jsonb),
        'total_count', total_matches,
        'has_more', (safe_offset + safe_limit) < total_matches,
        'page', safe_page,
        'total_pages', total_pages,
        'page_limit', safe_limit,
        'page_offset', safe_offset,
        'statistics', coalesce(stats_json, '{}'::jsonb)
    );
END;
$$;
COMMENT ON FUNCTION get_watch_reference_offers_page(
    text, text, text, timestamptz, timestamptz, text, integer, integer, boolean, text
) IS
    'Return one paginated page of active offers for a brand/reference with full-set statistics.';

REVOKE ALL ON FUNCTION get_watch_reference_offers_page(
    text, text, text, timestamptz, timestamptz, text, integer, integer, boolean, text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION get_watch_reference_offers_page(
    text, text, text, timestamptz, timestamptz, text, integer, integer, boolean, text
) TO service_role;
GRANT EXECUTE ON FUNCTION get_watch_reference_offers_page(
    text, text, text, timestamptz, timestamptz, text, integer, integer, boolean, text
) TO authenticated;
GRANT EXECUTE ON FUNCTION get_watch_reference_offers_page(
    text, text, text, timestamptz, timestamptz, text, integer, integer, boolean, text
) TO anon;
