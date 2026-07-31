-- Sprint 50.6: server-side active offer search RPC.
-- Idempotent: safe to run multiple times in Supabase SQL Editor.

CREATE OR REPLACE FUNCTION normalize_search_reference(value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT upper(regexp_replace(coalesce(value, ''), '[\s\-/.]', '', 'g'));
$$;

CREATE OR REPLACE FUNCTION compact_search_text(value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT lower(regexp_replace(coalesce(value, ''), '[\s\-/]', '', 'g'));
$$;

CREATE OR REPLACE FUNCTION reference_contains_token(reference text, token text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT
        coalesce(normalize_search_reference(reference), '') <> ''
        AND coalesce(normalize_search_reference(token), '') <> ''
        AND strpos(
            normalize_search_reference(reference),
            normalize_search_reference(token)
        ) > 0;
$$;

CREATE OR REPLACE FUNCTION search_field_matches(field text, term text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT
        field IS NOT NULL
        AND btrim(field) <> ''
        AND term IS NOT NULL
        AND btrim(term) <> ''
        AND (
            lower(field) LIKE '%' || lower(term) || '%'
            OR compact_search_text(field) LIKE '%' || compact_search_text(term) || '%'
            OR (
                compact_search_text(term) <> ''
                AND compact_search_text(field) LIKE compact_search_text(term) || '%'
            )
        );
$$;

CREATE OR REPLACE FUNCTION watch_matches_search_token(
    brand text,
    reference text,
    model text,
    dial text,
    bracelet text,
    token_json jsonb
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    term text;
BEGIN
    IF coalesce(jsonb_array_length(token_json -> 'terms'), 0) = 0
       AND coalesce(token_json ->> 'reference_like', 'false') <> 'true' THEN
        RETURN TRUE;
    END IF;

    IF coalesce(token_json ->> 'reference_like', 'false') = 'true' THEN
        RETURN reference_contains_token(reference, token_json ->> 'token');
    END IF;

    FOR term IN
        SELECT jsonb_array_elements_text(token_json -> 'terms')
    LOOP
        IF search_field_matches(brand, term)
            OR search_field_matches(reference, term)
            OR search_field_matches(model, term)
            OR search_field_matches(dial, term)
            OR search_field_matches(bracelet, term) THEN
            RETURN TRUE;
        END IF;
    END LOOP;

    RETURN FALSE;
END;
$$;

CREATE OR REPLACE FUNCTION offer_condition_category_sql(stored text)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    key text;
BEGIN
    IF stored IS NULL OR btrim(stored) = '' THEN
        RETURN 'Unknown';
    END IF;

    key := lower(regexp_replace(btrim(stored), '[\s_-]+', ' ', 'g'));

    IF key IN (
        'brand new',
        'brand new / unworn',
        'fresh new',
        'fresh new / unworn',
        'new / unworn',
        'bn',
        'new',
        'unworn',
        'bnib',
        'nos',
        'unworn complete',
        'sticker',
        'stickers',
        'full stickers',
        'stickered'
    ) THEN
        RETURN 'New';
    END IF;

    IF key IN (
        'good condition',
        'like new',
        'mint',
        'worn',
        'pre owned',
        'pre-owned',
        'preowned',
        'used',
        'lnib',
        'second hand',
        'serviced',
        'polished',
        'pre-owned'
    ) THEN
        RETURN 'Pre-Owned';
    END IF;

    RETURN 'Unknown';
END;
$$;

CREATE OR REPLACE FUNCTION dealer_is_search_visible(contact_type text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT
        contact_type IS NULL
        OR contact_type IN ('dealer', 'unknown');
$$;

CREATE INDEX IF NOT EXISTS idx_offers_active_watch_id
    ON offers (watch_id)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_offers_active_dealer_id
    ON offers (dealer_id)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_offers_active_usd_price
    ON offers (usd_price)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_watches_reference
    ON watches (reference);

CREATE INDEX IF NOT EXISTS idx_watches_brand_lower
    ON watches (lower(brand));

DROP FUNCTION IF EXISTS search_active_offers(jsonb, integer, text, integer, integer, boolean);

CREATE OR REPLACE FUNCTION search_active_offers(
    search_tokens jsonb DEFAULT '[]'::jsonb,
    max_usd_price integer DEFAULT NULL,
    condition_filter text DEFAULT NULL,
    page_limit integer DEFAULT 1000,
    page_offset integer DEFAULT 0,
    filter_business_dealers boolean DEFAULT TRUE
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SET search_path = public
AS $$
DECLARE
    safe_limit integer := LEAST(GREATEST(coalesce(page_limit, 1000), 1), 1000);
    safe_offset integer := GREATEST(coalesce(page_offset, 0), 0);
    total_matches bigint := 0;
    offer_rows jsonb := '[]'::jsonb;
BEGIN
    WITH filtered AS (
        SELECT
            o.id,
            o.dealer_id,
            o.watch_id,
            o.original_price,
            o.original_currency,
            o.usd_price,
            o.card_date,
            o.condition,
            m.id AS message_id,
            w.brand AS watch_brand,
            w.reference AS watch_reference,
            w.model AS watch_model,
            w.dial AS watch_dial,
            w.bracelet AS watch_bracelet,
            d.display_name AS dealer_display_name,
            d.contact_type AS dealer_contact_type,
            d.whatsapp_id AS dealer_whatsapp_id
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
    counted AS (
        SELECT COUNT(*) AS total_count
        FROM filtered
    ),
    paged AS (
        SELECT
            f.*,
            c.total_count
        FROM filtered f
        CROSS JOIN counted c
        ORDER BY f.usd_price NULLS LAST, f.id
        LIMIT safe_limit
        OFFSET safe_offset
    )
    SELECT
        coalesce(MAX(total_count), 0),
        coalesce(
            jsonb_agg(
                jsonb_build_object(
                    'id', id,
                    'dealer_id', dealer_id,
                    'watch_id', watch_id,
                    'original_price', original_price,
                    'original_currency', original_currency,
                    'usd_price', usd_price,
                    'card_date', card_date,
                    'condition', condition,
                    'message_id', message_id,
                    'watches', jsonb_build_object(
                        'brand', watch_brand,
                        'reference', watch_reference,
                        'model', watch_model,
                        'dial', watch_dial,
                        'bracelet', watch_bracelet
                    ),
                    'dealers', jsonb_build_object(
                        'display_name', dealer_display_name,
                        'contact_type', dealer_contact_type,
                        'whatsapp_id', dealer_whatsapp_id
                    ),
                    'messages', jsonb_build_object(
                        'id', message_id
                    )
                )
                ORDER BY usd_price NULLS LAST, id
            ),
            '[]'::jsonb
        )
    INTO total_matches, offer_rows
    FROM paged;

    RETURN jsonb_build_object(
        'offers', coalesce(offer_rows, '[]'::jsonb),
        'total_count', total_matches
    );
END;
$$;

COMMENT ON FUNCTION search_active_offers(jsonb, integer, text, integer, integer, boolean) IS
    'Filter active offers server-side for MRVAULT search. Pagination applies after filtering.';

REVOKE ALL ON FUNCTION search_active_offers(jsonb, integer, text, integer, integer, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION search_active_offers(jsonb, integer, text, integer, integer, boolean) TO service_role;
GRANT EXECUTE ON FUNCTION search_active_offers(jsonb, integer, text, integer, integer, boolean) TO authenticated;
GRANT EXECUTE ON FUNCTION search_active_offers(jsonb, integer, text, integer, integer, boolean) TO anon;
