-- Sprint 30: Watch Knowledge Engine 2.0

CREATE TABLE IF NOT EXISTS brand_aliases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alias_key TEXT NOT NULL,
    brand_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT brand_aliases_status_check CHECK (status IN ('active', 'ignored')),
    CONSTRAINT brand_aliases_alias_key_unique UNIQUE (alias_key)
);

CREATE INDEX IF NOT EXISTS idx_brand_aliases_status ON brand_aliases(status);
CREATE INDEX IF NOT EXISTS idx_brand_aliases_brand_name ON brand_aliases(brand_name);

CREATE TABLE IF NOT EXISTS unknown_brands (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    detected_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    example_message TEXT,
    dealer_id UUID REFERENCES dealers(id) ON DELETE SET NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'pending',
    CONSTRAINT unknown_brands_status_check CHECK (status IN ('pending', 'ignored', 'resolved')),
    CONSTRAINT unknown_brands_normalized_text_unique UNIQUE (normalized_text)
);

CREATE INDEX IF NOT EXISTS idx_unknown_brands_status ON unknown_brands(status);
CREATE INDEX IF NOT EXISTS idx_unknown_brands_last_seen ON unknown_brands(last_seen_at DESC);
