from __future__ import annotations
"""Smart Locker backend - infrastructure placeholder.

PLACEHOLDER - backend teammate: this file only exists so the containers have
something to run. Replace or extend it freely. To keep the infrastructure
working, preserve three things:

  1. The ASGI entrypoint stays `app.main:app` (Dockerfile / Compose / Kubernetes use it).
  2. The /health/live and /health/ready routes stay available (see health.py).
  3. Business routes live under /api/... because the frontend's nginx only
     forwards /api/* to this service.
"""

"""Smart Locker API: conditional reservations and fault-tolerant allocation.

Run with: uvicorn app.main:app --host 0.0.0.0 --port 8000
Requires psycopg2-binary. Tables are initialized idempotently at application startup.
"""

import asyncio
import logging
from datetime import datetime, timezone
import hashlib
import secrets
import string
import hmac
from enum import Enum
from typing import Optional
from uuid import UUID

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Path, status
from pydantic import BaseModel, Field, ConfigDict

from app.config import get_settings
from app.health import router as health_router

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Smart Locker API",
    description="Conditional reservations, simulated arrivals, and concurrent-safe locker allocation.",
    version="1.0.0",
)
app.include_router(health_router)
# All allocation-affecting API operations and worker runs use the same
# transaction-scoped lock. PostgreSQL coordinates this across API replicas.
ALLOCATION_LOCK_KEY = 731_904_221
SCHEMA_LOCK_KEY = 731_904_222

# A short quiet period lets near-simultaneous requests enter the queue before
# size priority is applied. The worker runs in every replica; the DB lock means
# only one replica can process the queue at a time.
QUEUE_QUIET_PERIOD_MS = 300
WORKER_POLL_SECONDS = 0.25
WORKER_BATCH_LIMIT = 500

_worker_task: Optional[asyncio.Task] = None


SIZE_RANK_SQL = """CASE {field}
    WHEN 'S' THEN 1
    WHEN 'M' THEN 2
    WHEN 'L' THEN 3
    WHEN 'XL' THEN 4
END"""


# ====================================
# Database functions
# ====================================

# create database connection
def db_connection():
    """Create a short-lived connection; PostgreSQL is the source of truth across replicas."""
    s = get_settings()
    return psycopg2.connect(
        host=s.postgres_host,
        port=s.postgres_port,
        dbname=s.postgres_db,
        user=s.postgres_user,
        password=s.postgres_password,
        connect_timeout=3,
        application_name="smart-locker-api",
        cursor_factory=RealDictCursor,
    )


# initialize schema within db
def initialize_schema() -> None:
    """Small self-contained schema for this backend assignment; safe to run repeatedly."""
    ddl = """
    CREATE SEQUENCE IF NOT EXISTS locker_queue_order_seq AS BIGINT START WITH 1;

    CREATE TABLE IF NOT EXISTS locker_stations (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        zone TEXT NOT NULL DEFAULT 'default',
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS locker_compartments (
        id BIGSERIAL PRIMARY KEY,
        station_id BIGINT NOT NULL REFERENCES locker_stations(id) ON DELETE CASCADE,
        label TEXT NOT NULL,
        size TEXT NOT NULL CHECK (size IN ('S','M','L','XL')),
        active BOOLEAN NOT NULL DEFAULT TRUE,
        reserved_by UUID NULL,
        UNIQUE (station_id, label)
    );

    CREATE INDEX IF NOT EXISTS idx_compartment_station_size
        ON locker_compartments(station_id, size) WHERE active = TRUE;
    
    CREATE TABLE IF NOT EXISTS locker_reservations (
        id UUID PRIMARY KEY,
        customer_id TEXT NOT NULL,
        station_id BIGINT NOT NULL,
        required_size TEXT NOT NULL CHECK (required_size IN ('S','M','L','XL')),
        status TEXT NOT NULL CHECK (status IN
            ('CONDITIONAL','ALLOCATED','ARRIVED','FULFILLED','CANCELLED','UNALLOCATED')),
        allocated_station_id BIGINT NULL REFERENCES locker_stations(id),
        compartment_id BIGINT NULL REFERENCES locker_compartments(id),
        queue_order BIGINT NULL,
        queue_updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        last_attempt_at TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        allocated_at TIMESTAMPTZ NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    -- Add new columns when upgrading the earlier backend schema.
    ALTER TABLE locker_reservations ADD COLUMN IF NOT EXISTS station_id BIGINT;
    ALTER TABLE locker_reservations ADD COLUMN IF NOT EXISTS queue_order BIGINT;
    ALTER TABLE locker_reservations ADD COLUMN IF NOT EXISTS queue_updated_at TIMESTAMPTZ;
    ALTER TABLE locker_reservations ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ;
    ALTER TABLE locker_reservations ADD COLUMN IF NOT EXISTS allocated_at TIMESTAMPTZ;
    

    -- Historical completed reservations may keep compartment_id. Only active
    -- allocations must be unique for a compartment.
    CREATE UNIQUE INDEX IF NOT EXISTS uq_active_reservation_compartment
        ON locker_reservations(compartment_id)
        WHERE compartment_id IS NOT NULL AND status IN ('ALLOCATED','ARRIVED');


    CREATE INDEX IF NOT EXISTS idx_reservations_waiting_queue
        ON locker_reservations(station_id, status, queue_order)
        WHERE status = 'CONDITIONAL' AND queue_order IS NOT NULL
          AND last_attempt_at IS NULL;
    """
    # with db_connection() as conn:
    #     with conn.cursor() as cur:
    #         cur.execute(ddl)

    with db_connection() as conn, conn.cursor() as cur:
        # Prevent two starting replicas from migrating concurrently, and avoid
        # sequence adjustment racing with this version's allocation operations.
        cur.execute("SELECT pg_advisory_xact_lock(%s::bigint)", (SCHEMA_LOCK_KEY,))
        cur.execute("SELECT pg_advisory_xact_lock(%s::bigint)", (ALLOCATION_LOCK_KEY,))
        cur.execute(ddl)
        cur.execute(
            """SELECT setval(
                   'locker_queue_order_seq',
                   GREATEST(COALESCE((SELECT MAX(queue_order) FROM locker_reservations), 0) + 1, 1),
                   FALSE
               )"""
        )


def _acquire_allocation_lock(cur) -> None:
    """Serialize allocation decisions and releases across every API replica."""
    cur.execute("SELECT pg_advisory_xact_lock(%s::bigint)", (ALLOCATION_LOCK_KEY,))







# ====================================
# Classes
# ====================================

# Locker size parameters - string enum so that only fixed inputs are accepted
class Size(str, Enum):
    S = "S"
    M = "M"
    L = "L"
    XL = "XL"


# create locker object
class StationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    zone: str = Field(default="default", min_length=1, max_length=80)


# create locker compartment object
class CompartmentCreate(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    size: Size


# create reservation, validates incoming request
class ReservationCreate(BaseModel):
    customer_id: str = Field(min_length=1, max_length=120)
    station_id: int = Field(gt=0)
    required_size: Size

# validates outgoing response
class ReservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    customer_id: str
    station_id: int
    required_size: str
    status: str
    allocated_station_id: Optional[int] = None
    compartment_id: Optional[int] = None
    queue_order: Optional[int] = None
    last_attempt_at: Optional[datetime] = None
    created_at: datetime
    allocated_at: Optional[datetime] = None

# define password for unlocking locker, min/max length
class UnlockRequest(BaseModel):
    password: str = Field(min_length=1, max_length=100)

# ====================================
# Helper functions
# ====================================

# def row_to_reservation(row: dict) -> dict:
#     # Normalize psycopg's array values for JSON/Pydantic.
#     row["backup_station_ids"] = list(row.get("backup_station_ids") or [])
#     return row

# helper function to get reservation details
def _get_reservation(cur, reservation_id: UUID, lock: bool = False) -> dict:
    sql = """SELECT id, customer_id, station_id, required_size, status,
                    allocated_station_id, compartment_id, queue_order,
                    last_attempt_at, created_at, allocated_at
             FROM locker_reservations WHERE id = %s"""
    if lock:
        sql += " FOR UPDATE"
    cur.execute(sql, (str(reservation_id),))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return row

# function to allocate which locker to place parcel in
def _allocate_one_locked(cur, reservation: dict) -> dict:
    """Allocate the smallest active free compartment that can fit the parcel.

    The selected station is fixed. A parcel may use a larger compartment when
    no exact-size compartment is free, but it never moves to another station.
    Caller must already hold the transaction-scoped allocation lock.
    """
    if reservation["status"] != "CONDITIONAL":
        return reservation

    cur.execute(
        f"""SELECT c.id, c.size
            FROM locker_compartments AS c
            JOIN locker_stations AS s ON s.id = c.station_id
            WHERE c.station_id = %s
              AND s.active = TRUE
              AND c.active = TRUE
              AND c.reserved_by IS NULL
              AND {SIZE_RANK_SQL.format(field='c.size')}
                    >= {SIZE_RANK_SQL.format(field='%s')}
            ORDER BY {SIZE_RANK_SQL.format(field='c.size')} ASC, c.id ASC
            LIMIT 1
            FOR UPDATE OF c SKIP LOCKED""",
        (reservation["station_id"], reservation["required_size"]),
    )
    compartment = cur.fetchone()

    if compartment is None:
        # Do not spin on the same unavailable request every worker poll. A new
        # compartment or a released allocation clears this marker and retries it.
        cur.execute(
            """UPDATE locker_reservations
               SET last_attempt_at = NOW(), updated_at = NOW()
               WHERE id = %s AND status = 'CONDITIONAL'
               RETURNING id, customer_id, station_id, required_size, status,
                         allocated_station_id, compartment_id, queue_order,
                         last_attempt_at, created_at, allocated_at""",
            (str(reservation["id"]),),
        )
        return cur.fetchone()

    compartment_id = compartment["id"]
    cur.execute(
        """UPDATE locker_compartments
           SET reserved_by = %s
           WHERE id = %s AND reserved_by IS NULL""",
        (str(reservation["id"]), compartment_id),
    )
    if cur.rowcount != 1:
        # The shared lock should make this unusual; the row lock and guarded
        # update still prevent double assignment if another writer bypasses it.
        cur.execute(
            """UPDATE locker_reservations SET last_attempt_at = NOW(), updated_at = NOW()
               WHERE id = %s RETURNING id, customer_id, station_id, required_size,
                         status, allocated_station_id, compartment_id, queue_order,
                         last_attempt_at, created_at, allocated_at""",
            (str(reservation["id"]),),
        )
        return cur.fetchone()

    cur.execute(
        """UPDATE locker_reservations
           SET status = 'ALLOCATED', allocated_station_id = station_id,
               compartment_id = %s, allocated_at = NOW(), last_attempt_at = NOW(),
               updated_at = NOW()
           WHERE id = %s AND status = 'CONDITIONAL'
           RETURNING id, customer_id, station_id, required_size, status,
                     allocated_station_id, compartment_id, queue_order,
                     last_attempt_at, created_at, allocated_at""",
        (compartment_id, str(reservation["id"])),
    )
    result = cur.fetchone()
    if result is None:
        raise HTTPException(status_code=409, detail="Reservation changed while allocation was in progress")
    return result


def _process_waiting_reservations_locked(
    cur, station_id: int, limit: int = WORKER_BATCH_LIMIT
) -> list[dict]:
    """Process one station's queue: larger parcels first, then same-size FIFO."""
    cur.execute(
        f"""SELECT id, customer_id, station_id, required_size, status,
                      allocated_station_id, compartment_id, queue_order,
                      last_attempt_at, created_at, allocated_at
               FROM locker_reservations
               WHERE status = 'CONDITIONAL'
                 AND station_id = %s
                 AND queue_order IS NOT NULL
                 AND last_attempt_at IS NULL
               ORDER BY {SIZE_RANK_SQL.format(field='required_size')} DESC,
                        queue_order ASC, id ASC
               LIMIT %s
               FOR UPDATE""",
        (station_id, limit),
    )
    waiting = cur.fetchall()
    return [_allocate_one_locked(cur, reservation) for reservation in waiting]


def _process_if_queue_is_quiet_locked(cur) -> list[dict]:
    """Drain each station's queue after that station has been quiet for the window."""
    cur.execute(
        """SELECT station_id
           FROM locker_reservations
           WHERE status = 'CONDITIONAL'
             AND queue_order IS NOT NULL
             AND last_attempt_at IS NULL
           GROUP BY station_id
           HAVING MAX(queue_updated_at) <=
                  clock_timestamp() - (%s * INTERVAL '1 millisecond')
           ORDER BY station_id""",
        (QUEUE_QUIET_PERIOD_MS,),
    )
    quiet_station_ids = [row["station_id"] for row in cur.fetchall()]

    results: list[dict] = []
    for station_id in quiet_station_ids:
        results.extend(_process_waiting_reservations_locked(cur, station_id))
    return results

# convert generated psw into a hash and stores it instead
def _hash_collection_password(password: str) -> str:
    """Store a salted PBKDF2 hash instead of the plaintext password."""
    salt = secrets.token_bytes(16)
    iterations = 310000

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )

    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def _verify_collection_password(password: str, stored_hash: str) -> bool:
    """Verify a collection password against its stored hash."""
    try:
        algorithm, iterations, salt_hex, digest_hex = stored_hash.split("$")

        if algorithm != "pbkdf2_sha256":
            return False

        expected = bytes.fromhex(digest_hex)

        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )

        return hmac.compare_digest(actual, expected)

    except (ValueError, TypeError):
        return False

# system generates password to unlock compartment. 
def _generate_collection_password(cur, reservation_id: UUID):
    """Generate a password once when a reservation first becomes ARRIVED."""

    cur.execute(
        """SELECT unlock_password_hash
           FROM locker_reservations
           WHERE id = %s
           FOR UPDATE""",
        (str(reservation_id),),
    )

    row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Reservation not found")

    if row["unlock_password_hash"] is not None:
        return None

    alphabet = string.ascii_uppercase + string.digits
    password = "".join(secrets.choice(alphabet) for _ in range(10))

    cur.execute(
        """UPDATE locker_reservations
           SET unlock_password_hash = %s
           WHERE id = %s""",
        (_hash_collection_password(password), str(reservation_id)),
    )

    return password


def _process_queue_once() -> int:
    """Worker entry point: acquire the shared lock and process a quiet queue."""
    with db_connection() as conn, conn.cursor() as cur:
        _acquire_allocation_lock(cur)
        return len(_process_if_queue_is_quiet_locked(cur))


async def _allocation_worker() -> None:
    """Poll PostgreSQL; safe to run in every replica because of the advisory lock."""
    while True:
        try:
            await asyncio.to_thread(_process_queue_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Locker allocation worker iteration failed")
        await asyncio.sleep(WORKER_POLL_SECONDS)

# ====================================
# Routes
# ====================================
@app.on_event("startup")
def startup() -> None:
    global _worker_task
    try:
        initialize_schema()
    except psycopg2.Error:
        # Keep the process alive; readiness should report the actual DB outage.
        logger.exception("Database schema initialization failed")
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_allocation_worker())

@app.on_event("shutdown")
async def shutdown() -> None:
    global _worker_task
    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None


@app.get("/api/stations", tags=["stations"])
def list_stations():
    """List active stations with the number of currently free slots per size."""
    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT s.id, s.name, s.zone,
                      COUNT(c.id) FILTER (WHERE c.active = TRUE) AS total_compartments,
                      COUNT(c.id) FILTER (WHERE c.active = TRUE AND c.reserved_by IS NULL)
                          AS available_compartments,
                      COUNT(c.id) FILTER (WHERE c.active = TRUE AND c.reserved_by IS NULL
                                          AND c.size = 'S') AS available_s,
                      COUNT(c.id) FILTER (WHERE c.active = TRUE AND c.reserved_by IS NULL
                                          AND c.size = 'M') AS available_m,
                      COUNT(c.id) FILTER (WHERE c.active = TRUE AND c.reserved_by IS NULL
                                          AND c.size = 'L') AS available_l,
                      COUNT(c.id) FILTER (WHERE c.active = TRUE AND c.reserved_by IS NULL
                                          AND c.size = 'XL') AS available_xl
               FROM locker_stations AS s
               LEFT JOIN locker_compartments AS c ON c.station_id = s.id
               WHERE s.active = TRUE
               GROUP BY s.id
               ORDER BY s.id"""
        )
        rows = cur.fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "zone": row["zone"],
            "total_compartments": row["total_compartments"],
            "available_compartments": row["available_compartments"],
            "available_by_size": {
                "S": row["available_s"],
                "M": row["available_m"],
                "L": row["available_l"],
                "XL": row["available_xl"],
            },
        }
        for row in rows
    ]


# Difference between list_stations and get_station_availability??

@app.get("/api/stations/{station_id}/availability", tags=["stations"])
def get_station_availability(station_id: int):
    """Show a selected station's free compartment count for each parcel size."""
    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT s.id, s.name, s.zone,
                      COUNT(c.id) FILTER (WHERE c.active = TRUE AND c.reserved_by IS NULL
                                          AND c.size = 'S') AS available_s,
                      COUNT(c.id) FILTER (WHERE c.active = TRUE AND c.reserved_by IS NULL
                                          AND c.size = 'M') AS available_m,
                      COUNT(c.id) FILTER (WHERE c.active = TRUE AND c.reserved_by IS NULL
                                          AND c.size = 'L') AS available_l,
                      COUNT(c.id) FILTER (WHERE c.active = TRUE AND c.reserved_by IS NULL
                                          AND c.size = 'XL') AS available_xl
               FROM locker_stations AS s
               LEFT JOIN locker_compartments AS c ON c.station_id = s.id
               WHERE s.id = %s AND s.active = TRUE
               GROUP BY s.id""",
            (station_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Active station not found")
    by_size = {
        "S": row["available_s"],
        "M": row["available_m"],
        "L": row["available_l"],
        "XL": row["available_xl"],
    }
    return {
        "station_id": row["id"],
        "station_name": row["name"],
        "zone": row["zone"],
        "available_by_size": by_size,
        "total_available": sum(by_size.values()),
    }


@app.post("/api/stations", status_code=status.HTTP_201_CREATED, tags=["stations"])
def create_station(payload: StationCreate):
    try:
        with db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO locker_stations(name,zone) VALUES (%s,%s) RETURNING id,name,zone,active",
                (payload.name.strip(), payload.zone.strip()),
            )
            return cur.fetchone()
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="Station name already exists")


@app.post("/api/stations/{station_id}/compartments", status_code=status.HTTP_201_CREATED, tags=["stations"])
def create_compartment(station_id: int, payload: CompartmentCreate):
    try:
        with db_connection() as conn, conn.cursor() as cur:
            _acquire_allocation_lock(cur)
            cur.execute(
                "SELECT id FROM locker_stations WHERE id = %s AND active = TRUE",
                (station_id,),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Active station not found")
            cur.execute(
                """INSERT INTO locker_compartments(station_id, label, size)
                   VALUES (%s, %s, %s)
                   RETURNING id, station_id, label, size, active""",
                (station_id, payload.label.strip(), payload.size.value),
            )
            result = cur.fetchone()
            # New inventory may satisfy previously waiting reservations.
            cur.execute(
                """UPDATE locker_reservations
                   SET last_attempt_at = NULL, queue_updated_at = clock_timestamp()
                   WHERE status = 'CONDITIONAL' AND station_id = %s""",
                (station_id,),
            )
            return result
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="Compartment label already exists at this station")


@app.get("/api/stations/{station_id}/compartments", tags=["stations"])
def list_compartments(station_id: int):
    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id,station_id,label,size,active,(reserved_by IS NOT NULL) AS reserved
               FROM locker_compartments WHERE station_id=%s ORDER BY id""",
            (station_id,),
        )
        return cur.fetchall()


# POST request to create reservations for locker
@app.post(
    "/api/reservations",
    response_model=ReservationOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["reservations"],
)
def create_reservation(payload: ReservationCreate):
    """Queue a parcel for the one station selected by the user.

    A background worker assigns the smallest fitting compartment after the
    queue has been quiet for the batching interval. The response is initially
    CONDITIONAL; the client can poll GET /api/reservations/{id} for the result.
    """
    with db_connection() as conn, conn.cursor() as cur:
        _acquire_allocation_lock(cur)
        cur.execute(
            "SELECT id FROM locker_stations WHERE id = %s AND active = TRUE",
            (payload.station_id,),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=422, detail="Unknown or inactive selected station")

        cur.execute(
            """INSERT INTO locker_reservations
               (id, customer_id, station_id, required_size, status, queue_order,
                queue_updated_at, created_at)
               VALUES (gen_random_uuid(), %s, %s, %s, 'CONDITIONAL',
                       nextval('locker_queue_order_seq'), clock_timestamp(), clock_timestamp())
               RETURNING id""",
            (payload.customer_id.strip(), payload.station_id, payload.required_size.value),
        )
        reservation_id = cur.fetchone()["id"]
        return _get_reservation(cur, reservation_id)


@app.get("/api/reservations/{reservation_id}", response_model=ReservationOut, tags=["reservations"])
def get_reservation(reservation_id: UUID):
    with db_connection() as conn, conn.cursor() as cur:
        return _get_reservation(cur, reservation_id)


@app.get("/api/reservations", tags=["reservations"])
def list_reservations(status_filter: Optional[str] = None):
    allowed = {"CONDITIONAL", "ALLOCATED", "ARRIVED", "FULFILLED", "CANCELLED", "UNALLOCATED"}
    if status_filter and status_filter not in allowed:
        raise HTTPException(status_code=422, detail=f"status_filter must be one of {sorted(allowed)}")
    with db_connection() as conn, conn.cursor() as cur:
        if status_filter:
            cur.execute(
                """SELECT id, customer_id, station_id, required_size, status,
                          allocated_station_id, compartment_id, queue_order,
                          last_attempt_at, created_at, allocated_at
                   FROM locker_reservations
                   WHERE status = %s
                   ORDER BY created_at, id""",
                (status_filter,),
            )
        else:
            cur.execute(
                """SELECT id, customer_id, station_id, required_size, status,
                          allocated_station_id, compartment_id, queue_order,
                          last_attempt_at, created_at, allocated_at
                   FROM locker_reservations
                   ORDER BY created_at, id"""
            )
        return cur.fetchall()





@app.post("/api/reservations/process-queue", tags=["reservations"])
def process_reservations_queue():
    """Manually trigger the same quiet-queue drain used by the background worker.

    Normally the worker does this automatically. Calls made before the short
    batching window has elapsed return processed=0 and leave the queue intact.
    """
    with db_connection() as conn, conn.cursor() as cur:
        _acquire_allocation_lock(cur)
        results = _process_if_queue_is_quiet_locked(cur)
    return {"processed": len(results), "results": results}

# list of parcels available for collection
@app.get("/api/parcels/collection", tags=["parcels"])
def list_parcels_for_collection():
    """List parcels that have arrived and are awaiting collection."""

    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT
                   r.id AS reservation_id,
                   r.required_size,
                   r.status,
                   s.name AS station_name,
                   c.label AS compartment_label
               FROM locker_reservations r
               JOIN locker_stations s
                   ON s.id = r.allocated_station_id
               JOIN locker_compartments c
                   ON c.id = r.compartment_id
               WHERE r.status = 'ARRIVED'
               ORDER BY r.arrived_at ASC"""
        )

        return cur.fetchall()

# receive parcel unlock locker feature
@app.post("/api/reservations/{reservation_id}/unlock", tags=["parcels"])
def unlock_locker(
    reservation_id: UUID,
    payload: UnlockRequest,
):
    """Verify collection password and simulate locker unlocking."""

    with db_connection() as conn, conn.cursor() as cur:
        reservation = _get_reservation(cur, reservation_id, lock=True)

        if reservation["status"] != "ARRIVED":
            raise HTTPException(
                status_code=409,
                detail="Parcel is not available for collection",
            )

        if reservation["compartment_id"] is None:
            raise HTTPException(
                status_code=409,
                detail="No compartment is allocated",
            )

        cur.execute(
            """SELECT unlock_password_hash
               FROM locker_reservations
               WHERE id = %s""",
            (str(reservation_id),),
        )

        row = cur.fetchone()

        if (
            row is None
            or row["unlock_password_hash"] is None
            or not _verify_collection_password(
                payload.password,
                row["unlock_password_hash"],
            )
        ):
            raise HTTPException(
                status_code=401,
                detail="Incorrect collection password",
            )

        # Simulate unlocking and release the compartment.
        cur.execute(
            """UPDATE locker_compartments
               SET reserved_by = NULL
               WHERE id = %s AND reserved_by = %s""",
            (
                reservation["compartment_id"],
                str(reservation_id),
            ),
        )

        if cur.rowcount != 1:
            raise HTTPException(
                status_code=409,
                detail="Compartment allocation could not be verified",
            )

        cur.execute(
            """UPDATE locker_reservations
               SET status = 'FULFILLED', updated_at = NOW()
               WHERE id = %s""",
            (str(reservation_id),),
        )

        return {
            "message": "Locker unlocked",
            "reservation_id": str(reservation_id),
            "status": "FULFILLED",
        }

@app.post("/api/reservations/{reservation_id}/fulfill", response_model=ReservationOut, tags=["reservations"])
def fulfill_reservation(reservation_id: UUID):
    """Complete a reservation and refresh its station queue after releasing the slot."""
    with db_connection() as conn, conn.cursor() as cur:
        _acquire_allocation_lock(cur)
        reservation = _get_reservation(cur, reservation_id, lock=True)
        if reservation["status"] != "ALLOCATED":
            raise HTTPException(status_code=409, detail="Only allocated reservations can be fulfilled")

        if reservation["compartment_id"] is not None:
            cur.execute(
                """UPDATE locker_compartments SET reserved_by = NULL
                   WHERE id = %s AND reserved_by = %s""",
                (reservation["compartment_id"], str(reservation_id)),
            )

        cur.execute(
            """UPDATE locker_reservations
               SET status = 'FULFILLED', updated_at = NOW()
               WHERE id = %s
               RETURNING id, customer_id, station_id, required_size, status,
                         allocated_station_id, compartment_id, queue_order,
                         last_attempt_at, created_at, allocated_at""",
            (str(reservation_id),),
        )
        result = cur.fetchone()
        cur.execute(
            """UPDATE locker_reservations
               SET last_attempt_at = NULL, queue_updated_at = clock_timestamp()
               WHERE status = 'CONDITIONAL' AND station_id = %s""",
            (reservation["station_id"],),
        )
        return result


@app.delete("/api/reservations/{reservation_id}", response_model=ReservationOut, tags=["reservations"])
def cancel_reservation(reservation_id: UUID):
    with db_connection() as conn, conn.cursor() as cur:
        _acquire_allocation_lock(cur)
        reservation = _get_reservation(cur, reservation_id, lock=True)
        if reservation["status"] in ("FULFILLED", "CANCELLED"):
            raise HTTPException(status_code=409, detail=f"Cannot cancel reservation in {reservation['status']} state")

        if reservation["compartment_id"] is not None:
            cur.execute(
                """UPDATE locker_compartments SET reserved_by = NULL
                   WHERE id = %s AND reserved_by = %s""",
                (reservation["compartment_id"], str(reservation_id)),
            )

        cur.execute(
            """UPDATE locker_reservations
               SET status = 'CANCELLED', updated_at = NOW()
               WHERE id = %s
               RETURNING id, customer_id, station_id, required_size, status,
                         allocated_station_id, compartment_id, queue_order,
                         last_attempt_at, created_at, allocated_at""",
            (str(reservation_id),),
        )
        result = cur.fetchone()
        if reservation["compartment_id"] is not None:
            cur.execute(
                """UPDATE locker_reservations
                   SET last_attempt_at = NULL, queue_updated_at = clock_timestamp()
                   WHERE status = 'CONDITIONAL' AND station_id = %s""",
                (reservation["station_id"],),
            )
        return result


@app.get("/api", tags=["system"])
def api_index():
    return {
        "service": "smart-locker-api",
        "docs": "/docs",
        "workflow": [
            "GET /api/stations to view availability",
            "user selects one station",
            "POST /api/reservations to queue a parcel at that station",
            "background worker allocates the smallest fitting compartment",
        ],
        "allocation_policy": "selected station only; smallest available compartment that fits",
        "queue_policy": "larger parcel sizes first; FIFO by database queue_order for equal sizes",
        "queue_batch_window_ms": QUEUE_QUIET_PERIOD_MS,
    }