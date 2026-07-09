-- Sprint 49.1: Dealer list parser training — extend learning rule field types

ALTER TABLE parser_learning_rules
    DROP CONSTRAINT IF EXISTS parser_learning_rules_field_type_check;

ALTER TABLE parser_learning_rules
    ADD CONSTRAINT parser_learning_rules_field_type_check
        CHECK (field_type IN (
            'condition',
            'brand',
            'brand_header',
            'reference',
            'price',
            'intent',
            'currency',
            'row_split'
        ));
