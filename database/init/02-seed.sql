-- Smart Locker System - seed data (generated)
-- Runs after 01-schema.sql on a fresh volume. Deterministic and re-runnable.
--
-- Contents
--   * 5 stations (3 original demo stations + East Gate + Load Test Depot)
--   * ~125 compartments across S / M / L / XL
--   * 6 original CONDITIONAL demo reservations (contention scenarios)
--   * 2 live reservations holding compartments (East Gate)
--   * 120 historical reservations over the last ~30 days (analytics)
--   * parcels and audit events are created automatically by triggers

BEGIN;


-- 1. Stations

INSERT INTO locker_stations (name, zone, address, latitude, longitude) VALUES
    ('Central Hub',      'CENTRAL', 'Raffles Place, Singapore',  1.284000, 103.851200),
    ('North Point',      'NORTH',   'Yishun, Singapore',         1.429500, 103.835300),
    ('South Plaza',      'SOUTH',   'HarbourFront, Singapore',   1.265300, 103.822000),
    ('East Gate',        'EAST',    'Tampines, Singapore',       1.354100, 103.943700),
    ('Load Test Depot',  'TEST',    'Simulation only',           NULL,     NULL)
ON CONFLICT (name) DO NOTHING;


-- 2. Compartments

-- 2a. Original demo stations: small on purpose so stations fill up quickly and the backup-station logic is easy to demonstrate.
INSERT INTO locker_compartments (station_id, label, size)
SELECT s.id, v.label, v.size
FROM (VALUES
    ('Central Hub', 'C-S01',  'S'),  ('Central Hub', 'C-S02',  'S'),
    ('Central Hub', 'C-M01',  'M'),  ('Central Hub', 'C-M02',  'M'),
    ('Central Hub', 'C-L01',  'L'),
    ('North Point', 'N-S01',  'S'),  ('North Point', 'N-M01',  'M'),
    ('North Point', 'N-M02',  'M'),  ('North Point', 'N-L01',  'L'),
    ('North Point', 'N-XL01', 'XL'),
    ('South Plaza', 'S-S01',  'S'),  ('South Plaza', 'S-M01',  'M'),
    ('South Plaza', 'S-L01',  'L'),  ('South Plaza', 'S-L02',  'L'),
    ('South Plaza', 'S-XL01', 'XL'),
    ('East Gate',   'E-S01',  'S'),  ('East Gate',   'E-S02',  'S'),
    ('East Gate',   'E-S03',  'S'),  ('East Gate',   'E-M01',  'M'),
    ('East Gate',   'E-M02',  'M'),  ('East Gate',   'E-M03',  'M'),
    ('East Gate',   'E-L01',  'L'),  ('East Gate',   'E-L02',  'L'),
    ('East Gate',   'E-XL01', 'XL'), ('East Gate',   'E-XL02', 'XL')
) AS v(station_name, label, size)
JOIN locker_stations s ON s.name = v.station_name
ON CONFLICT (station_id, label) DO NOTHING;

-- 2b. Load Test Depot: 100 compartments for the concurrency stress test. Mix per block of 10: 4 S, 3 M, 2 L, 1 XL.
INSERT INTO locker_compartments (station_id, label, size)
SELECT s.id,
       'T-' || lpad(g::text, 3, '0'),
       CASE WHEN g % 10 IN (1, 2, 3, 4) THEN 'S'
            WHEN g % 10 IN (5, 6, 7)    THEN 'M'
            WHEN g % 10 IN (8, 9)       THEN 'L'
            ELSE 'XL' END
FROM locker_stations s, generate_series(1, 100) AS g
WHERE s.name = 'Load Test Depot'
ON CONFLICT (station_id, label) DO NOTHING;

-- 3. Original demo reservations (all CONDITIONAL).
--    Scenarios:
--      * 003 (prio 20) and 005 (prio 30) both need an XL; North Point has
--        only one XL -> priority decides, loser falls back / stays pending.
--      * 005 prefers Central Hub (no XL there) -> redirected to backup.
--      * 001 / 002 / 004 / 006 allocate normally at their preferred station.

INSERT INTO locker_reservations
    (id, customer_id, preferred_station_id, backup_station_ids, required_size, priority, status)
SELECT v.id::uuid,
       v.customer_id,
       pref.id,
       COALESCE(ARRAY(
           SELECT bs.id
           FROM unnest(v.backup_names) WITH ORDINALITY AS b(name, ord)
           JOIN locker_stations bs ON bs.name = b.name
           ORDER BY b.ord), '{}')::bigint[],
       v.required_size,
       v.priority,
       'CONDITIONAL'
FROM (VALUES
    ('10000000-0000-4000-8000-000000000001', 'CUSTOMER-001', 'Central Hub',
        ARRAY['North Point', 'South Plaza']::text[], 'M',  10),
    ('10000000-0000-4000-8000-000000000002', 'CUSTOMER-002', 'Central Hub',
        ARRAY['South Plaza', 'North Point']::text[], 'S',   5),
    ('10000000-0000-4000-8000-000000000003', 'CUSTOMER-003', 'North Point',
        ARRAY['Central Hub']::text[],                'XL', 20),
    ('10000000-0000-4000-8000-000000000004', 'CUSTOMER-004', 'South Plaza',
        ARRAY['Central Hub', 'North Point']::text[], 'L',  15),
    ('10000000-0000-4000-8000-000000000005', 'CUSTOMER-005', 'Central Hub',
        ARRAY['North Point']::text[],                'XL', 30),
    ('10000000-0000-4000-8000-000000000006', 'CUSTOMER-006', 'North Point',
        ARRAY['South Plaza']::text[],                'M',   0)
) AS v(id, customer_id, preferred_name, backup_names, required_size, priority)
JOIN locker_stations pref ON pref.name = v.preferred_name
ON CONFLICT (id) DO NOTHING;

-- 4. Two live reservations holding compartments at East Gate, so the
--    dashboard shows occupied compartments immediately.
--    Reservation first, then the compartment (integrity checks run at COMMIT).

INSERT INTO locker_reservations
    (id, customer_id, preferred_station_id, backup_station_ids, required_size,
     priority, status, allocated_station_id, compartment_id, created_at, arrived_at, updated_at)
SELECT v.id::uuid, v.customer_id, s.id, '{}', v.size, v.priority, v.status,
       s.id, c.id,
       NOW() - interval '3 hours',
       CASE WHEN v.status = 'ARRIVED' THEN NOW() - interval '90 minutes' END,
       NOW() - interval '90 minutes'
FROM (VALUES
    ('30000000-0000-4000-8000-000000000001', 'CUSTOMER-007', 'E-S01', 'S', 5, 'ARRIVED'),
    ('30000000-0000-4000-8000-000000000002', 'CUSTOMER-008', 'E-M01', 'M', 0, 'ALLOCATED')
) AS v(id, customer_id, label, size, priority, status)
JOIN locker_stations s ON s.name = 'East Gate'
JOIN locker_compartments c ON c.station_id = s.id AND c.label = v.label
ON CONFLICT (id) DO NOTHING;

UPDATE locker_compartments c
SET reserved_by = r.id
FROM locker_reservations r
WHERE r.id IN ('30000000-0000-4000-8000-000000000001'::uuid,
               '30000000-0000-4000-8000-000000000002'::uuid)
  AND c.id = r.compartment_id
  AND c.reserved_by IS NULL;


-- 5. Historical reservations (analytics / "next available slot" input).
--    120 rows spread over ~30 days. Every 9th is CANCELLED, the rest are
--    FULFILLED. Every 5th was redirected from Central Hub to a backup.
--    Compartments are reused freely: history keeps compartment_id, and the
--    uniqueness rule only applies to live (ALLOCATED/ARRIVED) reservations.

WITH pool AS (
    SELECT c.id AS comp_id, c.station_id, c.size,
           row_number() OVER (ORDER BY c.id) AS rn,
           count(*) OVER () AS n
    FROM locker_compartments c
    JOIN locker_stations s ON s.id = c.station_id
    WHERE s.zone <> 'TEST'
),
central AS (SELECT id FROM locker_stations WHERE name = 'Central Hub'),
hist AS (
    SELECT g AS i,
           p.comp_id, p.station_id AS alloc_station, p.size,
           (g % 9 = 0) AS cancelled,
           NOW() - interval '30 days' + g * interval '355 minutes'
                 + ((g * 37) % 60) * interval '1 minute' AS created_at
    FROM generate_series(1, 120) AS g
    JOIN pool p ON p.rn = ((g - 1) % (SELECT max(n) FROM pool)) + 1
)
INSERT INTO locker_reservations
    (id, customer_id, preferred_station_id, backup_station_ids, required_size, priority,
     status, allocated_station_id, compartment_id, created_at, arrived_at, updated_at)
SELECT ('20000000-0000-4000-8000-' || lpad(h.i::text, 12, '0'))::uuid,
       'CUSTOMER-' || lpad((100 + (h.i % 40))::text, 3, '0'),
       CASE WHEN h.i % 5 = 0 AND h.alloc_station <> ce.id THEN ce.id ELSE h.alloc_station END,
       CASE WHEN h.i % 5 = 0 AND h.alloc_station <> ce.id
            THEN ARRAY[h.alloc_station] ELSE '{}'::bigint[] END,
       h.size,
       (h.i % 3) * 10,
       CASE WHEN h.cancelled THEN 'CANCELLED' ELSE 'FULFILLED' END,
       CASE WHEN h.cancelled THEN NULL ELSE h.alloc_station END,
       CASE WHEN h.cancelled THEN NULL ELSE h.comp_id END,
       h.created_at,
       CASE WHEN h.cancelled THEN NULL
            ELSE h.created_at + (10 + (h.i * 13) % 90) * interval '1 minute' END,
       CASE WHEN h.cancelled THEN h.created_at + interval '20 minutes'
            ELSE h.created_at + (10 + (h.i * 13) % 90) * interval '1 minute'
                              + (5 + (h.i * 7) % 45) * interval '1 minute' END
FROM hist h CROSS JOIN central ce
ON CONFLICT (id) DO NOTHING;

COMMIT;

