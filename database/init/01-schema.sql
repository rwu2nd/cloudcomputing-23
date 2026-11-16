
-- Smart Locker System database schema
-- Runs before 02-seed.sql when PostgreSQL initializes a fresh volume.

CREATE TABLE IF NOT EXISTS locker_stations (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    zone TEXT NOT NULL DEFAULT 'default',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS locker_compartments (
    id BIGSERIAL PRIMARY KEY,
    station_id BIGINT NOT NULL
        REFERENCES locker_stations(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    size TEXT NOT NULL CHECK (size IN ('S', 'M', 'L', 'XL')),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    reserved_by UUID NULL,
    UNIQUE (station_id, label)
);

CREATE INDEX IF NOT EXISTS idx_compartment_station_size
    ON locker_compartments(station_id, size)
    WHERE active = TRUE;

CREATE TABLE IF NOT EXISTS locker_reservations (
    id UUID PRIMARY KEY,
    customer_id TEXT NOT NULL,
    preferred_station_id BIGINT NOT NULL
        REFERENCES locker_stations(id),
    backup_station_ids BIGINT[] NOT NULL DEFAULT '{}',
    required_size TEXT NOT NULL
        CHECK (required_size IN ('S', 'M', 'L', 'XL')),
    priority INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (
        status IN (
            'CONDITIONAL',
            'ALLOCATED',
            'ARRIVED',
            'FULFILLED',
            'CANCELLED',
            'UNALLOCATED'
        )
    ),

    unlock_password_hash TEXT NULL,

    allocated_station_id BIGINT NULL
        REFERENCES locker_stations(id),
    compartment_id BIGINT NULL
        REFERENCES locker_compartments(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    arrived_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reservations_queue
    ON locker_reservations(status, priority DESC, created_at ASC);
