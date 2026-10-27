
-- Smart Locker System PostgreSQL schema

BEGIN; -- atomic transaction for the whole schema

-- Shared helper: keep updated_at current on every UPDATE
CREATE OR REPLACE FUNCTION fn_set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 1. Stations
CREATE TABLE IF NOT EXISTS locker_stations (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT        NOT NULL UNIQUE,
    zone        TEXT        NOT NULL DEFAULT 'default',
    address     TEXT        NULL,
    latitude    NUMERIC(9,6) NULL CHECK (latitude  BETWEEN -90  AND 90),
    longitude   NUMERIC(9,6) NULL CHECK (longitude BETWEEN -180 AND 180),
    active      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_station_name_not_blank CHECK (btrim(name) <> '')
);

DROP TRIGGER IF EXISTS trg_stations_updated_at ON locker_stations;
CREATE TRIGGER trg_stations_updated_at
    BEFORE UPDATE ON locker_stations
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

-- 2. Compartments
--    reserved_by = id of the reservation currently holding this compartment 
--    (NULL = free). UNIQUE guarantees one reservation holds at most one.
-- 

CREATE TABLE IF NOT EXISTS locker_compartments (
    id          BIGSERIAL PRIMARY KEY,
    station_id  BIGINT      NOT NULL REFERENCES locker_stations(id) ON DELETE CASCADE,
    label       TEXT        NOT NULL,
    size        TEXT        NOT NULL CHECK (size IN ('S', 'M', 'L', 'XL')),
    active      BOOLEAN     NOT NULL DEFAULT TRUE,
    reserved_by UUID        NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_compartment_label   UNIQUE (station_id, label),
    CONSTRAINT uq_compartment_holder  UNIQUE (reserved_by),
    CONSTRAINT chk_compartment_label_not_blank CHECK (btrim(label) <> '')
);

-- Fast "find a free compartment of this size at this station" lookup.
CREATE INDEX IF NOT EXISTS idx_compartment_station_size
    ON locker_compartments(station_id, size)
    WHERE active = TRUE;

CREATE INDEX IF NOT EXISTS idx_compartment_free
    ON locker_compartments(station_id, size, id)
    WHERE active = TRUE AND reserved_by IS NULL;


-- 3. Reservations (conditional reservations + allocation result)

CREATE TABLE IF NOT EXISTS locker_reservations (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id          TEXT        NOT NULL,
    preferred_station_id BIGINT      NOT NULL REFERENCES locker_stations(id),
    backup_station_ids   BIGINT[]    NOT NULL DEFAULT '{}',
    required_size        TEXT        NOT NULL CHECK (required_size IN ('S', 'M', 'L', 'XL')),
    priority             INTEGER     NOT NULL DEFAULT 0 CHECK (priority BETWEEN 0 AND 100),
    status               TEXT        NOT NULL CHECK (status IN (
    'CONDITIONAL', 'ALLOCATED', 'ARRIVED','FULFILLED', 'CANCELLED', 'UNALLOCATED')),

    allocated_station_id BIGINT      NULL REFERENCES locker_stations(id),
    compartment_id       BIGINT      NULL REFERENCES locker_compartments(id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    arrived_at           TIMESTAMPTZ NULL,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_customer_not_blank CHECK (btrim(customer_id) <> ''),
    CONSTRAINT chk_backup_limit       CHECK (cardinality(backup_station_ids) <= 20),
    CONSTRAINT chk_preferred_not_in_backup
        CHECK (NOT (preferred_station_id = ANY (backup_station_ids))),

    -- Status and allocation columns must agree
    CONSTRAINT chk_status_allocation CHECK (
        (status IN ('ALLOCATED', 'ARRIVED')
            AND compartment_id IS NOT NULL AND allocated_station_id IS NOT NULL)
        OR (status IN ('CONDITIONAL', 'UNALLOCATED')
            AND compartment_id IS NULL AND allocated_station_id IS NULL)
        OR status IN ('FULFILLED', 'CANCELLED')
    ),
    CONSTRAINT chk_arrived_has_timestamp CHECK (status <> 'ARRIVED' OR arrived_at IS NOT NULL)
);

-- KEY CONCURRENCY CONSTRAINT: a compartment can belong to at most one reservation
-- Finished reservations (FULFILLED/CANCELLED) keep compartment_id as history, so the compartment can be reused
-- UNIQUE on compartment_id would block reuse after the first fulfilled booking
CREATE UNIQUE INDEX IF NOT EXISTS uq_reservation_live_compartment
    ON locker_reservations(compartment_id)
    WHERE status IN ('ALLOCATED', 'ARRIVED');

CREATE INDEX IF NOT EXISTS idx_reservations_queue
    ON locker_reservations(status, priority DESC, created_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS idx_reservations_customer
    ON locker_reservations(customer_id);
CREATE INDEX IF NOT EXISTS idx_reservations_preferred
    ON locker_reservations(preferred_station_id);
CREATE INDEX IF NOT EXISTS idx_reservations_allocated
    ON locker_reservations(allocated_station_id) WHERE allocated_station_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_reservations_created
    ON locker_reservations(created_at);

DROP TRIGGER IF EXISTS trg_reservations_updated_at ON locker_reservations;
CREATE TRIGGER trg_reservations_updated_at
    BEFORE UPDATE ON locker_reservations
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

-- Compartment reservation link
ALTER TABLE locker_compartments
    DROP CONSTRAINT IF EXISTS fk_compartment_reserved_by;
ALTER TABLE locker_compartments
    ADD CONSTRAINT fk_compartment_reserved_by
    FOREIGN KEY (reserved_by) REFERENCES locker_reservations(id)
    ON DELETE SET NULL
    DEFERRABLE INITIALLY IMMEDIATE;


-- 4. Parcels (parcel state, kept in sync automatically from reservations)

CREATE TABLE IF NOT EXISTS parcels (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tracking_number TEXT        NOT NULL UNIQUE,
    reservation_id  UUID        NOT NULL UNIQUE
                        REFERENCES locker_reservations(id) ON DELETE CASCADE,
    size            TEXT        NOT NULL CHECK (size IN ('S', 'M', 'L', 'XL')),
    state           TEXT        NOT NULL DEFAULT 'IN_TRANSIT'
                        CHECK (state IN ('IN_TRANSIT', 'STORED', 'COLLECTED', 'CANCELLED')),
    compartment_id  BIGINT      NULL REFERENCES locker_compartments(id),
    pickup_code     CHAR(6)     NOT NULL
                        DEFAULT lpad((floor(random() * 1000000))::int::text, 6, '0'),
    stored_at       TIMESTAMPTZ NULL,
    collected_at    TIMESTAMPTZ NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_parcel_collected_ts CHECK (state <> 'COLLECTED' OR collected_at IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_parcels_state ON parcels(state);
CREATE INDEX IF NOT EXISTS idx_parcels_compartment ON parcels(compartment_id);

DROP TRIGGER IF EXISTS trg_parcels_updated_at ON parcels;
CREATE TRIGGER trg_parcels_updated_at
    BEFORE UPDATE ON parcels
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();


-- 5. Audit log (evidence for the report: allocations, fallbacks, timings)

CREATE TABLE IF NOT EXISTS reservation_events (
    id             BIGSERIAL PRIMARY KEY,
    reservation_id UUID        NOT NULL REFERENCES locker_reservations(id) ON DELETE CASCADE,
    event_type     TEXT        NOT NULL,
    from_status    TEXT        NULL,
    to_status      TEXT        NOT NULL,
    station_id     BIGINT      NULL,
    compartment_id BIGINT      NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_events_reservation ON reservation_events(reservation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_created     ON reservation_events(created_at);


-- 6. Triggers on reservations


-- 6a. Backup stations must exist and the allocation must be valid (allocated station = preferred or one of the backups).
CREATE OR REPLACE FUNCTION fn_validate_reservation_stations() RETURNS trigger AS $$
DECLARE
    missing BIGINT;
BEGIN
    SELECT b INTO missing
    FROM unnest(NEW.backup_station_ids) AS b
    WHERE NOT EXISTS (SELECT 1 FROM locker_stations s WHERE s.id = b)
    LIMIT 1;
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'Backup station % does not exist', missing
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    IF NEW.allocated_station_id IS NOT NULL
       AND NEW.allocated_station_id <> NEW.preferred_station_id
       AND NOT (NEW.allocated_station_id = ANY (NEW.backup_station_ids)) THEN
        RAISE EXCEPTION 'Allocated station % is neither the preferred nor a backup station',
            NEW.allocated_station_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_reservations_validate ON locker_reservations;
CREATE TRIGGER trg_reservations_validate
    BEFORE INSERT OR UPDATE ON locker_reservations
    FOR EACH ROW EXECUTE FUNCTION fn_validate_reservation_stations();

-- 6b. Audit log of status changes.
CREATE OR REPLACE FUNCTION fn_log_reservation_event() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO reservation_events
            (reservation_id, event_type, from_status, to_status, station_id, compartment_id, created_at)
        VALUES (NEW.id, 'CREATED', NULL, NEW.status,
                NEW.allocated_station_id, NEW.compartment_id, NEW.created_at);
    ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO reservation_events
            (reservation_id, event_type, from_status, to_status, station_id, compartment_id, created_at)
        VALUES (NEW.id,
                CASE NEW.status
                    WHEN 'ALLOCATED' THEN 'ALLOCATED'
                    WHEN 'ARRIVED'   THEN 'ARRIVED'
                    WHEN 'FULFILLED' THEN 'COLLECTED'
                    WHEN 'CANCELLED' THEN 'CANCELLED'
                    ELSE 'STATUS_CHANGED'
                END,
                OLD.status, NEW.status,
                NEW.allocated_station_id, NEW.compartment_id, NEW.updated_at);
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_reservations_audit ON locker_reservations;
CREATE TRIGGER trg_reservations_audit
    AFTER INSERT OR UPDATE ON locker_reservations
    FOR EACH ROW EXECUTE FUNCTION fn_log_reservation_event();

-- 6c. Keep the parcel row in step with its reservation.
CREATE OR REPLACE FUNCTION fn_sync_parcel() RETURNS trigger AS $$
DECLARE
    v_state TEXT;
BEGIN
    v_state := CASE NEW.status
        WHEN 'ARRIVED'   THEN 'STORED'
        WHEN 'FULFILLED' THEN 'COLLECTED'
        WHEN 'CANCELLED' THEN 'CANCELLED'
        ELSE 'IN_TRANSIT'
    END;

    INSERT INTO parcels (tracking_number, reservation_id, size, state, compartment_id,
                         stored_at, collected_at, created_at, updated_at)
    VALUES ('SLK-' || upper(substr(md5(NEW.id::text), 1, 10)),
            NEW.id, NEW.required_size, v_state, NEW.compartment_id,
            NEW.arrived_at,
            CASE WHEN v_state = 'COLLECTED' THEN NEW.updated_at END,
            NEW.created_at, NEW.updated_at)
    ON CONFLICT (reservation_id) DO UPDATE
        SET state          = EXCLUDED.state,
            compartment_id = COALESCE(EXCLUDED.compartment_id, parcels.compartment_id),
            stored_at      = COALESCE(parcels.stored_at, EXCLUDED.stored_at),
            collected_at   = COALESCE(parcels.collected_at, EXCLUDED.collected_at);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_reservations_parcel ON locker_reservations;
CREATE TRIGGER trg_reservations_parcel
    AFTER INSERT OR UPDATE OF status, compartment_id, arrived_at ON locker_reservations
    FOR EACH ROW EXECUTE FUNCTION fn_sync_parcel();

-- 6d. Deferred integrity checks 
-- evaluated at COMMIT, so the API can update the compartment and the reservation in 
-- either order inside one transaction, but an inconsistent final state is rejected.
CREATE OR REPLACE FUNCTION fn_check_reservation_integrity() RETURNS trigger AS $$
DECLARE
    c RECORD;
BEGIN
    IF NEW.status IN ('ALLOCATED', 'ARRIVED') THEN
        SELECT station_id, size, reserved_by INTO c
        FROM locker_compartments WHERE id = NEW.compartment_id;
        IF NOT FOUND OR c.reserved_by IS DISTINCT FROM NEW.id THEN
            RAISE EXCEPTION 'Reservation % is % but compartment % is not held by it',
                NEW.id, NEW.status, NEW.compartment_id USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF c.station_id <> NEW.allocated_station_id THEN
            RAISE EXCEPTION 'Reservation % allocated station does not match compartment station', NEW.id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        IF c.size <> NEW.required_size THEN
            RAISE EXCEPTION 'Reservation % compartment size % does not match required size %',
                NEW.id, c.size, NEW.required_size USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS ctrg_reservation_integrity ON locker_reservations;
CREATE CONSTRAINT TRIGGER ctrg_reservation_integrity
    AFTER INSERT OR UPDATE ON locker_reservations
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION fn_check_reservation_integrity();

-- Reverse direction: a held compartment must point at a live reservation
CREATE OR REPLACE FUNCTION fn_check_compartment_integrity() RETURNS trigger AS $$
BEGIN
    IF NEW.reserved_by IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM locker_reservations r
            WHERE r.id = NEW.reserved_by
              AND r.compartment_id = NEW.id
              AND r.status IN ('ALLOCATED', 'ARRIVED')
        ) THEN
            RAISE EXCEPTION 'Compartment % is held by % which is not a live reservation for it',
                NEW.id, NEW.reserved_by USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS ctrg_compartment_integrity ON locker_compartments;
CREATE CONSTRAINT TRIGGER ctrg_compartment_integrity
    AFTER INSERT OR UPDATE OF reserved_by ON locker_compartments
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION fn_check_compartment_integrity();


-- 7. Views (availability, dashboards, analytics, integrity checks)

-- Availability by station and size (used by the dashboard / availability API).
CREATE OR REPLACE VIEW v_station_availability AS
SELECT s.id   AS station_id,
       s.name AS station_name,
       s.zone,
       c.size,
       COUNT(*)                                   AS total,
       COUNT(*) FILTER (WHERE c.reserved_by IS NULL) AS available,
       COUNT(*) FILTER (WHERE c.reserved_by IS NOT NULL) AS occupied
FROM locker_stations s
JOIN locker_compartments c ON c.station_id = s.id AND c.active
WHERE s.active
GROUP BY s.id, s.name, s.zone, c.size;

-- One row per station with utilisation.
CREATE OR REPLACE VIEW v_station_summary AS
SELECT s.id AS station_id, s.name, s.zone, s.address, s.latitude, s.longitude, s.active,
       COUNT(c.id) FILTER (WHERE c.active)                              AS total_compartments,
       COUNT(c.id) FILTER (WHERE c.active AND c.reserved_by IS NULL)    AS available_compartments,
       COUNT(c.id) FILTER (WHERE c.active AND c.reserved_by IS NOT NULL) AS occupied_compartments,
       ROUND(100.0 * COUNT(c.id) FILTER (WHERE c.active AND c.reserved_by IS NOT NULL)
             / NULLIF(COUNT(c.id) FILTER (WHERE c.active), 0), 1)       AS utilisation_pct
FROM locker_stations s
LEFT JOIN locker_compartments c ON c.station_id = s.id
GROUP BY s.id;

-- Compartment grid with the current holder
CREATE OR REPLACE VIEW v_compartment_status AS
SELECT c.id AS compartment_id, s.id AS station_id, s.name AS station_name,
       c.label, c.size, c.active,
       (c.reserved_by IS NOT NULL) AS reserved,
       c.reserved_by AS reservation_id,
       r.customer_id, r.status AS reservation_status
FROM locker_compartments c
JOIN locker_stations s ON s.id = c.station_id
LEFT JOIN locker_reservations r ON r.id = c.reserved_by;

-- Reservations with readable names and parcel info
CREATE OR REPLACE VIEW v_reservation_details AS
SELECT r.id, r.customer_id, r.status, r.priority, r.required_size,
       ps.name AS preferred_station,
       als.name AS allocated_station,
       cm.label AS compartment_label,
       (r.allocated_station_id IS NOT NULL
        AND r.allocated_station_id <> r.preferred_station_id) AS used_backup,
       p.tracking_number, p.state AS parcel_state,
       r.created_at, r.arrived_at, r.updated_at
FROM locker_reservations r
JOIN locker_stations ps ON ps.id = r.preferred_station_id
LEFT JOIN locker_stations als ON als.id = r.allocated_station_id
LEFT JOIN locker_compartments cm ON cm.id = r.compartment_id
LEFT JOIN parcels p ON p.reservation_id = r.id;

-- Demand by station / day-of-week / hour (input for the predictive feature)
CREATE OR REPLACE VIEW v_demand_by_hour AS
SELECT s.name AS station_name,
       EXTRACT(ISODOW FROM r.created_at AT TIME ZONE 'Asia/Singapore')::int AS iso_dow,
       EXTRACT(HOUR   FROM r.created_at AT TIME ZONE 'Asia/Singapore')::int AS hour_of_day,
       COUNT(*) AS bookings
FROM locker_reservations r
JOIN locker_stations s ON s.id = r.preferred_station_id
WHERE r.status <> 'CANCELLED'
GROUP BY 1, 2, 3;

-- Average time a compartment stays occupied (arrival -> collection)
CREATE OR REPLACE VIEW v_dwell_time AS
SELECT s.name AS station_name,
       COUNT(*) AS collected_parcels,
       ROUND(AVG(EXTRACT(EPOCH FROM (r.updated_at - r.arrived_at)) / 60)::numeric, 1) AS avg_dwell_minutes
FROM locker_reservations r
JOIN locker_stations s ON s.id = r.allocated_station_id
WHERE r.status = 'FULFILLED' AND r.arrived_at IS NOT NULL
GROUP BY s.name;

-- Evaluation helpers for Member 5 (all three should return zero rows / zero)
CREATE OR REPLACE VIEW v_integrity_double_assignments AS
SELECT compartment_id, COUNT(*) AS live_reservations
FROM locker_reservations
WHERE status IN ('ALLOCATED', 'ARRIVED')
GROUP BY compartment_id
HAVING COUNT(*) > 1;

CREATE OR REPLACE VIEW v_integrity_orphans AS
SELECT c.id AS compartment_id, c.reserved_by
FROM locker_compartments c
LEFT JOIN locker_reservations r
       ON r.id = c.reserved_by AND r.compartment_id = c.id AND r.status IN ('ALLOCATED', 'ARRIVED')
WHERE c.reserved_by IS NOT NULL AND r.id IS NULL;

CREATE OR REPLACE VIEW v_reservation_counts AS
SELECT status, COUNT(*) AS reservations
FROM locker_reservations GROUP BY status;

COMMIT;
