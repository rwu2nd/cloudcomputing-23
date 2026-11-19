-- Delete every account except the seeded ones (admin, jane) and real admins,
-- together with everything they own: parcels, reservations, luggage sessions
-- (with payments and unlock events), login sessions and notifications.
-- Lockers they were holding are freed. All or nothing: one transaction.
--
-- Run it with:  make k8s-cleanup-users   (Kubernetes)
--               make cleanup-users       (Docker Compose)
BEGIN;

-- Same lock as allocation, so no booking is half-way through while we delete.
SELECT pg_advisory_xact_lock(731904221);

CREATE TEMP TABLE doomed ON COMMIT DROP AS
    SELECT id, username FROM "user"
    WHERE username NOT IN ('admin', 'jane')
      AND (role <> 'ADMIN' OR username = 'stress-runner');

CREATE TEMP TABLE doomed_reservations ON COMMIT DROP AS
    SELECT id FROM locker_reservations
    WHERE customer_id IN (SELECT id::text FROM doomed)
       OR parcel_id IN (SELECT id FROM parcels WHERE user_id IN (SELECT id FROM doomed));

CREATE TEMP TABLE doomed_luggage ON COMMIT DROP AS
    SELECT id FROM luggage_sessions WHERE user_id IN (SELECT id FROM doomed);

SELECT (SELECT count(*) FROM doomed) AS users,
       (SELECT count(*) FROM doomed_reservations) AS reservations,
       (SELECT count(*) FROM doomed_luggage) AS luggage_sessions;

UPDATE locker_compartments SET reserved_by = NULL
WHERE reserved_by IN (SELECT id FROM doomed_reservations UNION SELECT id FROM doomed_luggage);

DELETE FROM luggage_unlock_events
WHERE luggage_session_id IN (SELECT id FROM doomed_luggage) OR user_id IN (SELECT id FROM doomed);
DELETE FROM luggage_payments WHERE luggage_session_id IN (SELECT id FROM doomed_luggage);
DELETE FROM luggage_sessions WHERE id IN (SELECT id FROM doomed_luggage);
DELETE FROM locker_reservations WHERE id IN (SELECT id FROM doomed_reservations);

-- The notification service creates its table on first start; skip if absent.
DO $$
BEGIN
    IF to_regclass('public.notifications') IS NOT NULL THEN
        DELETE FROM notifications WHERE user_id IN (SELECT username FROM doomed);
    END IF;
END $$;

-- Parcels and login sessions go with the user (ON DELETE CASCADE).
DELETE FROM "user" WHERE id IN (SELECT id FROM doomed);

-- Let any remaining queued bookings try the lockers that were just freed.
UPDATE locker_reservations SET last_attempt_at = NULL, queue_updated_at = clock_timestamp()
WHERE status = 'CONDITIONAL';

COMMIT;
