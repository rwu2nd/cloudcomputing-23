-- Migration for databases that were created BEFORE assigned parcels existed.
-- Fresh databases do not need this: database/init/01-schema.sql already
-- contains these changes.
--
-- What it does
--   1. Adds the `parcels` table (parcels an admin assigns to a user).
--   2. Links reservations to parcels (locker_reservations.parcel_id) and
--      enforces one non-cancelled reservation per parcel.
--   3. Drops locker_reservations.unlock_password_hash - parcel collection no
--      longer uses a password, so those hashes are dead data.
--
-- Safe to run more than once. Existing reservations are kept; they simply have
-- no parcel_id (they stay collectable via Collect Parcel but do not appear in
-- the new "Your incoming parcels" list).
--
-- Docker Compose:
--   docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
--       < database/migrations/001-assigned-parcels.sql

BEGIN;

CREATE TABLE IF NOT EXISTS parcels (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL
        REFERENCES "user"(id) ON DELETE CASCADE,
    size TEXT NOT NULL CHECK (size IN ('S', 'M', 'L', 'XL')),
    description TEXT NULL,
    assigned_by UUID NULL
        REFERENCES "user"(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_parcels_user
    ON parcels(user_id, created_at DESC);

ALTER TABLE locker_reservations
    ADD COLUMN IF NOT EXISTS parcel_id UUID NULL REFERENCES parcels(id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_reservation_active_parcel
    ON locker_reservations(parcel_id)
    WHERE parcel_id IS NOT NULL
      AND status IN ('CONDITIONAL', 'ALLOCATED', 'ARRIVED', 'FULFILLED');

ALTER TABLE locker_reservations
    DROP COLUMN IF EXISTS unlock_password_hash;

COMMIT;
