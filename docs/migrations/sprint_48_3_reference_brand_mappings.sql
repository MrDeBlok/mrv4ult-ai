-- Sprint 48.3: Admin-taught reference to brand mappings

CREATE TABLE IF NOT EXISTS reference_brand_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reference_key TEXT NOT NULL,
    brand_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT reference_brand_mappings_status_check CHECK (status IN ('active', 'ignored')),
    CONSTRAINT reference_brand_mappings_reference_key_unique UNIQUE (reference_key)
);

CREATE INDEX IF NOT EXISTS idx_reference_brand_mappings_status ON reference_brand_mappings(status);
CREATE INDEX IF NOT EXISTS idx_reference_brand_mappings_brand_name ON reference_brand_mappings(brand_name);
