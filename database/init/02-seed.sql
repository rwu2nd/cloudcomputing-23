
-- =========================================
-- 1. Create sample locker stations
-- =========================================

INSERT INTO locker (name, zone)
VALUES
    ('Central Hub', 'CENTRAL'),
    ('North Point', 'NORTH'),
    ('South Plaza', 'SOUTH')
ON CONFLICT (name) DO NOTHING;


-- =========================================
-- 2. Create compartments of different sizes
-- =========================================

INSERT INTO locker_compartments (station_id, label, size)
SELECT l.id, v.label, v.size
FROM locker l
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
    ON l.name = v.station_name
ON CONFLICT (station_id, label) DO NOTHING;

INSERT INTO public."user"
    (id, username, password_hash, role)
VALUES
    (
        gen_random_uuid(),
        'admin',
        'pbkdf2_sha256$310000$8f8189bd68b0701f417e497e299ce7c3$65cd8939d0686954c707b7e8d4cc7aba2c06ff7bdbbb0e9ddd2bdb7f436c09ce', -- Admin@12345
        'ADMIN'
    )
ON CONFLICT (username) DO NOTHING;

INSERT INTO public."user"
    (id, username, password_hash, role)
VALUES
    (
        gen_random_uuid(),
        'jane',
        'pbkdf2_sha256$310000$eb27422637a5f05c4c840aeb0c39d1ba$b7f90f0d4aac54c8606f49dfb9efe06e4f9065871180f26fb052f68900e63a9a', -- janedoe123
        'USER'
    )
ON CONFLICT (username) DO NOTHING;