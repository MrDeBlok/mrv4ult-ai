-- MRV4ULT AI — PostgreSQL schema (v1)
-- Ready to paste into the Supabase SQL Editor.

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

CREATE TABLE groups (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL,
    country     TEXT,
    language    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE dealers (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    whatsapp_id     TEXT        NOT NULL,
    display_name    TEXT,
    phone_number    TEXT,
    company_name    TEXT,
    country         TEXT,
    notes           TEXT,
    is_active       BOOLEAN     NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT dealers_whatsapp_id_unique UNIQUE (whatsapp_id)
);

CREATE TABLE messages (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id            UUID        NOT NULL REFERENCES groups (id) ON DELETE RESTRICT,
    dealer_id           UUID        NOT NULL REFERENCES dealers (id) ON DELETE RESTRICT,
    raw_text            TEXT        NOT NULL,
    message_type        TEXT        NOT NULL,
    source              TEXT        NOT NULL DEFAULT 'whatsapp',
    whatsapp_message_id TEXT,
    received_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    parsed_at           TIMESTAMPTZ,
    parser_version      TEXT,
    parse_status        TEXT,
    parse_error         TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT messages_message_type_check
        CHECK (message_type IN ('offer', 'offer_list', 'request', 'unknown')),

    CONSTRAINT messages_parse_status_check
        CHECK (parse_status IS NULL OR parse_status IN ('success', 'partial', 'failed'))
);

CREATE TABLE watches (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    brand       TEXT,
    reference   TEXT,
    model       TEXT,
    dial        TEXT,
    bracelet    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE offers (
    id                      UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id              UUID            NOT NULL REFERENCES messages (id) ON DELETE RESTRICT,
    watch_id                UUID            NOT NULL REFERENCES watches (id) ON DELETE RESTRICT,
    dealer_id               UUID            NOT NULL REFERENCES dealers (id) ON DELETE RESTRICT,
    condition               TEXT,
    production_year         INTEGER,
    card_date               TEXT,
    notes                   TEXT,
    original_price          INTEGER,
    original_currency       TEXT,
    usd_price               INTEGER,
    exchange_rate_to_usd    NUMERIC(12, 6),
    source_line             TEXT,
    line_index              INTEGER         NOT NULL DEFAULT 0,
    is_duplicate            BOOLEAN         NOT NULL DEFAULT false,
    duplicate_of_id         UUID            REFERENCES offers (id) ON DELETE SET NULL,
    status                  TEXT            NOT NULL DEFAULT 'active',
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT now(),

    CONSTRAINT offers_status_check
        CHECK (status IN ('active', 'sold', 'withdrawn', 'expired')),

    CONSTRAINT offers_line_index_check
        CHECK (line_index >= 0),

    CONSTRAINT offers_message_line_unique
        UNIQUE (message_id, line_index)
);

CREATE TABLE requests (
    id                      UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id              UUID            REFERENCES messages (id) ON DELETE SET NULL,
    client_name             TEXT,
    client_phone            TEXT,
    brand                   TEXT,
    reference               TEXT,
    model                   TEXT,
    dial                    TEXT,
    bracelet                TEXT,
    condition               TEXT,
    production_year         INTEGER,
    card_date               TEXT,
    notes                   TEXT,
    max_price               INTEGER,
    max_currency            TEXT,
    max_usd_price           INTEGER,
    exchange_rate_to_usd    NUMERIC(12, 6),
    source                  TEXT            NOT NULL DEFAULT 'manual',
    status                  TEXT            NOT NULL DEFAULT 'active',
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT now(),
    expires_at              TIMESTAMPTZ,

    CONSTRAINT requests_source_check
        CHECK (source IN ('whatsapp', 'manual', 'import')),

    CONSTRAINT requests_status_check
        CHECK (status IN ('active', 'matched', 'fulfilled', 'cancelled'))
);

CREATE TABLE import_logs (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id          UUID            NOT NULL REFERENCES messages (id) ON DELETE CASCADE,
    import_time         TIMESTAMPTZ     NOT NULL,
    group_name          TEXT            NOT NULL,
    dealer_whatsapp     TEXT            NOT NULL,
    dealer_alias        TEXT,
    watches_parsed      INTEGER         NOT NULL DEFAULT 0,
    new_offers          INTEGER         NOT NULL DEFAULT 0,
    duplicate_offers    INTEGER         NOT NULL DEFAULT 0,
    matched_requests    INTEGER         NOT NULL DEFAULT 0,
    processing_time     TEXT            NOT NULL,
    processing_time_ms  INTEGER         NOT NULL DEFAULT 0,
    status              TEXT            NOT NULL,
    summary             JSONB           NOT NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),

    CONSTRAINT import_logs_status_check
        CHECK (status IN ('success', 'warning', 'error'))
);

-- ---------------------------------------------------------------------------
-- Indexes — watches
-- ---------------------------------------------------------------------------

CREATE INDEX idx_watches_brand
    ON watches (brand)
    WHERE brand IS NOT NULL;

CREATE INDEX idx_watches_reference
    ON watches (reference)
    WHERE reference IS NOT NULL;

CREATE INDEX idx_watches_brand_reference
    ON watches (brand, reference)
    WHERE brand IS NOT NULL AND reference IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Indexes — offers
-- ---------------------------------------------------------------------------

CREATE INDEX idx_offers_message_id
    ON offers (message_id);

CREATE INDEX idx_offers_watch_id
    ON offers (watch_id);

CREATE INDEX idx_offers_dealer_id
    ON offers (dealer_id);

CREATE INDEX idx_offers_usd_price
    ON offers (usd_price)
    WHERE usd_price IS NOT NULL;

CREATE INDEX idx_offers_duplicate_of_id
    ON offers (duplicate_of_id)
    WHERE duplicate_of_id IS NOT NULL;

CREATE INDEX idx_offers_status
    ON offers (status);

CREATE INDEX idx_offers_is_duplicate
    ON offers (is_duplicate)
    WHERE is_duplicate = true;

-- ---------------------------------------------------------------------------
-- Indexes — messages
-- ---------------------------------------------------------------------------

CREATE INDEX idx_messages_group_id
    ON messages (group_id);

CREATE INDEX idx_messages_dealer_id
    ON messages (dealer_id);

CREATE INDEX idx_messages_message_type
    ON messages (message_type);

CREATE INDEX idx_messages_received_at
    ON messages (received_at DESC);

CREATE INDEX idx_import_logs_import_time
    ON import_logs (import_time DESC);

CREATE INDEX idx_import_logs_message_id
    ON import_logs (message_id);

-- ---------------------------------------------------------------------------
-- Indexes — requests
-- ---------------------------------------------------------------------------

CREATE INDEX idx_requests_message_id
    ON requests (message_id)
    WHERE message_id IS NOT NULL;

CREATE INDEX idx_requests_brand
    ON requests (brand)
    WHERE brand IS NOT NULL;

CREATE INDEX idx_requests_reference
    ON requests (reference)
    WHERE reference IS NOT NULL;

CREATE INDEX idx_requests_status
    ON requests (status);

CREATE INDEX idx_requests_brand_reference
    ON requests (brand, reference)
    WHERE brand IS NOT NULL AND reference IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Updated-at trigger
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_dealers_updated_at
    BEFORE UPDATE ON dealers
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_watches_updated_at
    BEFORE UPDATE ON watches
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_offers_updated_at
    BEFORE UPDATE ON offers
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_requests_updated_at
    BEFORE UPDATE ON requests
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
