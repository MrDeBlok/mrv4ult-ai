-- Sprint 50.5: dealer default currency intelligence columns

ALTER TABLE dealers
    ADD COLUMN IF NOT EXISTS default_currency TEXT,
    ADD COLUMN IF NOT EXISTS default_currency_confidence INTEGER,
    ADD COLUMN IF NOT EXISTS inferred_from_phone_country BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS inferred_from_offer_history BOOLEAN NOT NULL DEFAULT false;
