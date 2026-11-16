
-- =========================================
-- 1. Create sample locker stations
-- =========================================

INSERT INTO locker_stations (name, zone)
VALUES
    ('Central Hub', 'CENTRAL'),
    ('North Point', 'NORTH'),
    ('South Plaza', 'SOUTH')
ON CONFLICT (name) DO NOTHING;


-- =========================================
-- 2. Create compartments of different sizes
-- =========================================

INSERT INTO locker_compartments (station_id, label, size)
SELECT s.id, v.label, v.size
FROM locker_stations s
JOIN (VALUES
    ('Central Hub', 'C-S01', 'S'),
    ('Central Hub', 'C-S02', 'S'),
    ('Central Hub', 'C-M01', 'M'),
    ('Central Hub', 'C-M02', 'M'),
    ('Central Hub', 'C-L01', 'L'),
    ('North Point', 'N-S01', 'S'),
    ('North Point', 'N-M01', 'M'),
    ('North Point', 'N-M02', 'M'),
    ('North Point', 'N-L01', 'L'),
    ('North Point', 'N-XL01', 'XL'),
    ('South Plaza', 'S-S01', 'S'),
    ('South Plaza', 'S-M01', 'M'),
    ('South Plaza', 'S-L01', 'L'),
    ('South Plaza', 'S-L02', 'L'),
    ('South Plaza', 'S-XL01', 'XL')
) AS v(station_name, label, size)
ON s.name = v.station_name
ON CONFLICT (station_id, label) DO NOTHING;


-- =========================================
-- 3. Create sample reservations
-- All begin as CONDITIONAL so arrivals can
-- trigger allocation through your API.
-- =========================================

INSERT INTO locker_reservations (
    id,
    customer_id,
    preferred_station_id,
    backup_station_ids,
    required_size,
    priority,
    status
)
SELECT
    v.id::UUID,
    v.customer_id,
    preferred.id,
    ARRAY(
        SELECT backup.id
        FROM unnest(v.backup_names) WITH ORDINALITY AS b(name, ord)
        JOIN locker_stations backup ON backup.name = b.name
        ORDER BY b.ord
    )::BIGINT[],
    v.required_size,
    v.priority,
    'CONDITIONAL'
FROM (VALUES
    (
        '10000000-0000-4000-8000-000000000001',
        'CUSTOMER-001', 'Central Hub',
        ARRAY['North Point', 'South Plaza']::TEXT[],
        'M', 10
    ),
    (
        '10000000-0000-4000-8000-000000000002',
        'CUSTOMER-002', 'Central Hub',
        ARRAY['South Plaza', 'North Point']::TEXT[],
        'S', 5
    ),
    (
        '10000000-0000-4000-8000-000000000003',
        'CUSTOMER-003', 'North Point',
        ARRAY['Central Hub']::TEXT[],
        'XL', 20
    ),
    (
        '10000000-0000-4000-8000-000000000004',
        'CUSTOMER-004', 'South Plaza',
        ARRAY['Central Hub', 'North Point']::TEXT[],
        'L', 15
    ),
    (
        '10000000-0000-4000-8000-000000000005',
        'CUSTOMER-005', 'Central Hub',
        ARRAY['North Point']::TEXT[],
        'XL', 30
    ),
    (
        '10000000-0000-4000-8000-000000000006',
        'CUSTOMER-006', 'North Point',
        ARRAY['South Plaza']::TEXT[],
        'M', 0
    )
) AS v(id, customer_id, preferred_name, backup_names,
       required_size, priority)
JOIN locker_stations preferred
    ON preferred.name = v.preferred_name
ON CONFLICT (id) DO NOTHING;
