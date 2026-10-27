-- Sanity checks. Keep this file OUT of database/init/ (it would auto-run). Run: psql -h localhost -U <user> <db> -f database/verify.sql
-- Expected: row counts as commented; integrity views return 0 rows.
SELECT 'stations' AS what, count(*) FROM locker_stations;                 -- 5
SELECT 'compartments', count(*) FROM locker_compartments;                  -- 125
SELECT * FROM v_reservation_counts ORDER BY status;                        -- CONDITIONAL 6, ALLOCATED 1, ARRIVED 1, FULFILLED ~107, CANCELLED ~13
SELECT count(*) AS parcels FROM parcels;                                   -- = number of reservations (128)
SELECT * FROM v_station_summary ORDER BY station_id;
SELECT * FROM v_integrity_double_assignments;                              -- 0 rows
SELECT * FROM v_integrity_orphans;                                         -- 0 rows
SELECT * FROM v_dwell_time;
