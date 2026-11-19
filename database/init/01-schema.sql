-- Smart Locker System database schema
-- Runs before 02-seed.sql when PostgreSQL initializes a fresh volume.

CREATE TABLE IF NOT EXISTS locker (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    zone TEXT NOT NULL DEFAULT 'default',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS locker_compartments (
    id BIGSERIAL PRIMARY KEY,
    station_id BIGINT NOT NULL
        REFERENCES locker(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    size TEXT NOT NULL CHECK (size IN ('S', 'M', 'L', 'XL')),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    reserved_by UUID NULL,
    UNIQUE (station_id, label)
);

CREATE INDEX IF NOT EXISTS idx_compartment_station_size
    ON locker_compartments(station_id, size)
    WHERE active = TRUE;


-- Shared identity for parcel, luggage and administration. USER is quoted
-- because it is a PostgreSQL keyword.
CREATE TABLE IF NOT EXISTS "user" (
    id UUID PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'USER' CHECK (role IN ('USER', 'ADMIN')),
    auth_token_version BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_username_lower
    ON "user" (LOWER(username));


-- Parcels assigned to a user by an administrator. A user may only reserve a
-- locker for a parcel that exists here. Whether a parcel is still waiting for a
-- locker, has a locker reserved, is ready for collection or was collected is
-- derived from its reservation(s) in locker_reservations (see parcel_id below),
-- so there is no separate status column to keep in sync.
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





CREATE TABLE IF NOT EXISTS locker_reservations (
    id UUID PRIMARY KEY,
    customer_id TEXT NOT NULL,

    station_id BIGINT NOT NULL
        REFERENCES locker(id),

    required_size TEXT NOT NULL
        CHECK (required_size IN ('S','M','L','XL')),

    status TEXT NOT NULL CHECK (status IN (
        'CONDITIONAL',
        'ALLOCATED',
        'ARRIVED',
        'FULFILLED',
        'CANCELLED',
        'UNALLOCATED'
    )),

    compartment_id BIGINT NULL
        REFERENCES locker_compartments(id),

    -- The assigned parcel this reservation is for. NULL only for legacy rows
    -- and admin load-test reservations; every user booking sets it.
    parcel_id UUID NULL
        REFERENCES parcels(id),

    queue_order BIGINT NULL,
    queue_updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    last_attempt_at TIMESTAMPTZ NULL,
    arrived_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    allocated_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- A parcel can have at most one non-cancelled reservation, ever: it cannot be
-- booked twice while a locker is held, and it cannot be booked again once it
-- has been collected (FULFILLED). A CANCELLED / UNALLOCATED reservation frees
-- the parcel so the user can choose another locker.
CREATE UNIQUE INDEX IF NOT EXISTS uq_reservation_active_parcel
    ON locker_reservations(parcel_id)
    WHERE parcel_id IS NOT NULL
      AND status IN ('CONDITIONAL', 'ALLOCATED', 'ARRIVED', 'FULFILLED');

CREATE SEQUENCE IF NOT EXISTS public.locker_queue_order_seq
    AS BIGINT
    START WITH 1
    INCREMENT BY 1;

-- luggage_sessions, luggage_payments and luggage_unlock_events are created by luggage.py.
CREATE TABLE IF NOT EXISTS luggage_sessions (
    id UUID PRIMARY KEY,
    user_id UUID NULL REFERENCES "user"(id),
    phone_number TEXT NULL,
    compartment_id BIGINT NOT NULL REFERENCES locker_compartments(id),
    pin_hash TEXT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('STORED','PAYMENT_PENDING','PAID_AWAITING_PICKUP','RETRIEVED','CANCELLED')),
    stored_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checkout_started_at TIMESTAMPTZ NULL,
    billable_days INTEGER NULL,
    amount_cents INTEGER NULL,
    retrieved_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS luggage_payments (
    id UUID PRIMARY KEY,
    payment_reference UUID NOT NULL UNIQUE,
    luggage_session_id UUID NOT NULL REFERENCES luggage_sessions(id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    billable_days INTEGER NOT NULL CHECK (billable_days >= 1),
    status TEXT NOT NULL CHECK (status IN ('PENDING','PAID','FAILED')),
    provider_transaction_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    paid_at TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_luggage_payment_session
    ON luggage_payments(luggage_session_id, created_at DESC);


CREATE TABLE IF NOT EXISTS luggage_auth_sessions (
    id UUID PRIMARY KEY,

    user_id UUID NOT NULL
        REFERENCES public."user"(id) ON DELETE CASCADE,

    token_hash TEXT NOT NULL UNIQUE,

    expires_at TIMESTAMPTZ NOT NULL,

    revoked_at TIMESTAMPTZ NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_luggage_auth_user_expiry
    ON luggage_auth_sessions(user_id, expires_at);

CREATE TABLE IF NOT EXISTS luggage_unlock_events (
    id UUID PRIMARY KEY,

    luggage_session_id UUID NOT NULL
        REFERENCES luggage_sessions(id),

    user_id UUID NOT NULL
        REFERENCES public."user"(id),

    compartment_id BIGINT NOT NULL
        REFERENCES locker_compartments(id),

    action TEXT NOT NULL
        CHECK (action IN ('STORE', 'RETRIEVE')),

    simulated BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_luggage_unlock_session
    ON luggage_unlock_events(luggage_session_id, created_at DESC);