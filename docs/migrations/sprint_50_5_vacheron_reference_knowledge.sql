-- Sprint 50.5: Seed trusted Vacheron Constantin Overseas reference mappings

INSERT INTO reference_brand_mappings (reference_key, brand_name, status, source)
VALUES
    ('4300V', 'Vacheron Constantin', 'active', 'authority_vacheron_overseas'),
    ('4500V', 'Vacheron Constantin', 'active', 'authority_vacheron_overseas'),
    ('4520V', 'Vacheron Constantin', 'active', 'authority_vacheron_overseas'),
    ('4600V', 'Vacheron Constantin', 'active', 'authority_vacheron_overseas'),
    ('5500V', 'Vacheron Constantin', 'active', 'authority_vacheron_overseas'),
    ('5520V', 'Vacheron Constantin', 'active', 'authority_vacheron_overseas'),
    ('6000V', 'Vacheron Constantin', 'active', 'authority_vacheron_overseas'),
    ('7900V', 'Vacheron Constantin', 'active', 'authority_vacheron_overseas'),
    ('7920V', 'Vacheron Constantin', 'active', 'authority_vacheron_overseas')
ON CONFLICT (reference_key) DO UPDATE
SET
    brand_name = EXCLUDED.brand_name,
    status = 'active',
    source = EXCLUDED.source,
    updated_at = now()
WHERE reference_brand_mappings.brand_name IS DISTINCT FROM EXCLUDED.brand_name;
