-- Sprint 32: AI Watch Identification

CREATE TABLE IF NOT EXISTS nickname_aliases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alias_key TEXT NOT NULL,
    brand_name TEXT NOT NULL,
    collection TEXT,
    model_name TEXT,
    nickname TEXT,
    likely_references TEXT[] NOT NULL DEFAULT '{}',
    confidence NUMERIC(4, 3) NOT NULL DEFAULT 0.850,
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT nickname_aliases_status_check CHECK (status IN ('active', 'ignored')),
    CONSTRAINT nickname_aliases_alias_key_unique UNIQUE (alias_key)
);

CREATE INDEX IF NOT EXISTS idx_nickname_aliases_status ON nickname_aliases(status);
CREATE INDEX IF NOT EXISTS idx_nickname_aliases_brand_name ON nickname_aliases(brand_name);

CREATE TABLE IF NOT EXISTS unknown_nicknames (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    detected_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    example_message TEXT,
    dealer_id UUID REFERENCES dealers(id) ON DELETE SET NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'pending',
    CONSTRAINT unknown_nicknames_status_check CHECK (status IN ('pending', 'ignored', 'resolved')),
    CONSTRAINT unknown_nicknames_normalized_text_unique UNIQUE (normalized_text)
);

CREATE INDEX IF NOT EXISTS idx_unknown_nicknames_status ON unknown_nicknames(status);
CREATE INDEX IF NOT EXISTS idx_unknown_nicknames_last_seen ON unknown_nicknames(last_seen_at DESC);
