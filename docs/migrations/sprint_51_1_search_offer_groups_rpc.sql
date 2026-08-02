-- Sprint 51.1: paginate search results by brand + reference group server-side.
-- Idempotent: safe to run multiple times in Supabase SQL Editor.

DROP FUNCTION IF EXISTS search_active_offer_groups(jsonb, integer, text, integer, integer, boolean, boolean);

CREATE OR REPLACE FUNCTION search_active_offer_groups(
    search_tokens jsonb DEFAULT '[]'::jsonb,
    max_usd_price integer DEFAULT NULL,
    condition_filter text DEFAULT NULL,
    page_limit integer DEFAULT 50,
    page_offset integer DEFAULT 0,
    filter_business_dealers boolean DEFAULT TRUE,
    cheapest_only boolean DEFAULT FALSE
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SET search_path = public
AS $$
DECLARE
    safe_limit integer := LEAST(GREATEST(coalesce(page_limit, 50), 1), 200);
    safe_offset integer := GREATEST(coalesce(page_offset, 0), 0);
    fetch_limit integer := safe_limit + 1;
    group_rows jsonb := '[]'::jsonb;
    returned_count integer := 0;
    has_more boolean := FALSE;
BEGIN
    WITH filtered AS (
        SELECT
            o.id,
            o.dealer_id,
            o.watch_id,
            o.usd_price,
            o.condition,
            w.brand AS watch_brand,
            w.reference AS watch_reference,
            w.model AS watch_model,
            w.dial AS watch_dial,
            w.bracelet AS watch_bracelet
        FROM offers o
        INNER JOIN watches w ON w.id = o.watch_id
        INNER JOIN dealers d ON d.id = o.dealer_id
        INNER JOIN messages m ON m.id = o.message_id
        WHERE o.status = 'active'
          AND (
              NOT filter_business_dealers
              OR dealer_is_search_visible(d.contact_type)
          )
          AND (
              max_usd_price IS NULL
              OR (o.usd_price IS NOT NULL AND o.usd_price <= max_usd_price)
          )
          AND (
              condition_filter IS NULL
              OR offer_condition_category_sql(o.condition) = condition_filter
          )
          AND (
              coalesce(jsonb_array_length(search_tokens), 0) = 0
              OR NOT EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements(search_tokens) AS token_obj(value)
                  WHERE NOT watch_matches_search_token(
                      w.brand,
                      w.reference,
                      w.model,
                      w.dial,
                      w.bracelet,
                      token_obj.value
                  )
              )
          )
    ),
    offer_rows AS (
        SELECT
            f.id,
            f.dealer_id,
            f.watch_id,
            f.usd_price,
            f.condition,
            f.watch_brand,
            f.watch_reference,
            lower(btrim(f.watch_brand)) AS brand_key,
            normalize_search_reference(f.watch_reference) AS ref_key,
            ROW_NUMBER() OVER (
                PARTITION BY lower(btrim(f.watch_brand)), normalize_search_reference(f.watch_reference)
                ORDER BY f.usd_price NULLS LAST, f.id
            ) AS price_rank
        FROM filtered f
        WHERE coalesce(btrim(f.watch_brand), '') <> ''
          AND coalesce(normalize_search_reference(f.watch_reference), '') <> ''
    ),
    eligible AS (
        SELECT *
        FROM offer_rows
        WHERE NOT cheapest_only OR price_rank = 1
    ),
    aggregated AS (
        SELECT
            brand_key,
            ref_key,
            (array_agg(watch_brand ORDER BY usd_price NULLS LAST, id))[1] AS brand,
            (array_agg(watch_reference ORDER BY usd_price NULLS LAST, id))[1] AS reference,
            (array_agg(watch_id ORDER BY usd_price NULLS LAST, id))[1] AS watch_id,
            MIN(usd_price) AS lowest_usd,
            COUNT(*)::integer AS offer_count,
            COUNT(DISTINCT dealer_id)::integer AS unique_dealers,
            array_remove(
                array_agg(DISTINCT offer_condition_category_sql(condition)),
                NULL
            ) AS condition_categories
        FROM eligible
        GROUP BY brand_key, ref_key
    ),
    paged AS (
        SELECT *
        FROM aggregated
        ORDER BY lowest_usd NULLS LAST, brand, reference
        LIMIT fetch_limit
        OFFSET safe_offset
    )
    SELECT
        coalesce(
            jsonb_agg(
                jsonb_build_object(
                    'brand', brand,
                    'reference', reference,
                    'watch_id', watch_id,
                    'lowest_usd', lowest_usd,
                    'offer_count', offer_count,
                    'unique_dealers', unique_dealers,
                    'condition_categories', coalesce(condition_categories, ARRAY[]::text[])
                )
                ORDER BY lowest_usd NULLS LAST, brand, reference
            ),
            '[]'::jsonb
        ),
        COUNT(*)::integer
    INTO group_rows, returned_count
    FROM paged;

    has_more := returned_count > safe_limit;
    IF has_more THEN
        group_rows := COALESCE(
            (
                SELECT jsonb_agg(value ORDER BY ordinality)
                FROM jsonb_array_elements(group_rows) WITH ORDINALITY AS t(value, ordinality)
                WHERE ordinality <= safe_limit
            ),
            '[]'::jsonb
        );
    END IF;

    RETURN jsonb_build_object(
        'groups', coalesce(group_rows, '[]'::jsonb),
        'has_more', has_more
    );
END;
$$;

COMMENT ON FUNCTION search_active_offer_groups(jsonb, integer, text, integer, integer, boolean, boolean) IS
    'Return one page of brand+reference search groups with aggregate offer stats.';

REVOKE ALL ON FUNCTION search_active_offer_groups(jsonb, integer, text, integer, integer, boolean, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION search_active_offer_groups(jsonb, integer, text, integer, integer, boolean, boolean) TO service_role;
GRANT EXECUTE ON FUNCTION search_active_offer_groups(jsonb, integer, text, integer, integer, boolean, boolean) TO authenticated;
GRANT EXECUTE ON FUNCTION search_active_offer_groups(jsonb, integer, text, integer, integer, boolean, boolean) TO anon;
