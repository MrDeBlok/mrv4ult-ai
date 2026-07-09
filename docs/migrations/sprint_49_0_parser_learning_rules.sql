-- Sprint 49.0: Parser Training Center — learned parser rules

CREATE TABLE IF NOT EXISTS parser_learning_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    field_type TEXT NOT NULL,
    term TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'global',
    dealer_id UUID REFERENCES dealers (id) ON DELETE SET NULL,
    group_id UUID REFERENCES groups (id) ON DELETE SET NULL,
    source_import_log_id UUID REFERENCES import_logs (id) ON DELETE SET NULL,
    created_by_user_id UUID REFERENCES users (id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT parser_learning_rules_field_type_check
        CHECK (field_type IN ('condition', 'brand', 'reference', 'price', 'intent', 'currency')),
    CONSTRAINT parser_learning_rules_scope_check
        CHECK (scope IN ('global', 'dealer', 'group')),
    CONSTRAINT parser_learning_rules_status_check
        CHECK (status IN ('active', 'disabled'))
);

CREATE INDEX IF NOT EXISTS idx_parser_learning_rules_status
    ON parser_learning_rules (status);

CREATE INDEX IF NOT EXISTS idx_parser_learning_rules_field_type
    ON parser_learning_rules (field_type);

CREATE INDEX IF NOT EXISTS idx_parser_learning_rules_scope
    ON parser_learning_rules (scope, dealer_id, group_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_parser_learning_rules_unique_active
    ON parser_learning_rules (
        field_type,
        lower(term),
        scope,
        COALESCE(dealer_id::text, ''),
        COALESCE(group_id::text, '')
    )
    WHERE status = 'active';
