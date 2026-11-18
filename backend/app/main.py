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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import secrets
import string
import hmac
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import Depends, FastAPI, HTTPException, Path, status
from pydantic import BaseModel, Field, ConfigDict

from app.config import get_settings
from app.health import router as health_router
from app.luggage import (
    router as luggage_router,
    store_luggage,
    StoreLuggageRequest,
    require_admin_user,
    authenticated_user,
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Smart Locker API",
    description="Conditional reservations, simulated arrivals, and concurrent-safe locker allocation.",
    version="1.0.0",
)
app.include_router(health_router)
app.include_router(luggage_router)
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
    compartment_id: Optional[int] = None
    queue_order: Optional[int] = None
    last_attempt_at: Optional[datetime] = None
    created_at: datetime
    allocated_at: Optional[datetime] = None

# define password for unlocking locker, min/max length
class UnlockRequest(BaseModel):
    password: str = Field(min_length=1, max_length=100)


class AdminGroup(BaseModel):
    kind: str = Field(pattern=r"^(PARCEL_COLLECT|PARCEL_RETURN|LUGGAGE)$")
    station_id: int = Field(gt=0)
    size: Size
    count: int = Field(ge=1, le=100)


class AdminConcurrencyRequest(BaseModel):
    groups: list[AdminGroup] = Field(min_length=1, max_length=4)

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
                    compartment_id, queue_order,
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
            JOIN locker AS s ON s.id = c.station_id
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
                         compartment_id, queue_order,
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
                         status,  compartment_id, queue_order,
                         last_attempt_at, created_at, allocated_at""",
            (str(reservation["id"]),),
        )
        return cur.fetchone()

    cur.execute(
        """UPDATE locker_reservations
           SET status = 'ALLOCATED',
               compartment_id = %s, allocated_at = NOW(), last_attempt_at = NOW(),
               updated_at = NOW()
           WHERE id = %s AND status = 'CONDITIONAL'
           RETURNING id, customer_id, station_id, required_size, status,
                      compartment_id, queue_order,
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
                       compartment_id, queue_order,
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
async def startup():
    global _worker_task

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
               FROM locker AS s
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
               FROM locker AS s
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
                "INSERT INTO locker(name,zone) VALUES (%s,%s) RETURNING id,name,zone,active",
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
                "SELECT id FROM locker WHERE id = %s AND active = TRUE",
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
def create_reservation(payload: ReservationCreate, user: dict = Depends(authenticated_user)):
    """Queue a parcel for the one station selected by the user.

    A background worker assigns the smallest fitting compartment after the
    queue has been quiet for the batching interval. The response is initially
    CONDITIONAL; the client can poll GET /api/reservations/{id} for the result.
    """
    if isinstance(user, dict) and user.get("username") and payload.customer_id.strip() != user["username"]:
        raise HTTPException(status_code=403, detail="Reservations must be created for the signed-in account")
    with db_connection() as conn, conn.cursor() as cur:
        _acquire_allocation_lock(cur)
        cur.execute(
            "SELECT id FROM locker WHERE id = %s AND active = TRUE",
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
def get_reservation(reservation_id: UUID, user: dict = Depends(authenticated_user)):
    with db_connection() as conn, conn.cursor() as cur:
        reservation = _get_reservation(cur, reservation_id)
        if reservation["customer_id"] != user["username"]:
            raise HTTPException(status_code=403, detail="This reservation belongs to another account")
        return reservation


@app.get("/api/reservations", tags=["reservations"])
def list_reservations(status_filter: Optional[str] = None, user: dict = Depends(authenticated_user)):
    allowed = {"CONDITIONAL", "ALLOCATED", "ARRIVED", "FULFILLED", "CANCELLED", "UNALLOCATED"}
    if status_filter and status_filter not in allowed:
        raise HTTPException(status_code=422, detail=f"status_filter must be one of {sorted(allowed)}")
    with db_connection() as conn, conn.cursor() as cur:
        if status_filter:
            cur.execute(
                """SELECT id, customer_id, station_id, required_size, status,
                           compartment_id, queue_order,
                          last_attempt_at, created_at, allocated_at
                   FROM locker_reservations
                   WHERE status = %s AND customer_id = %s
                   ORDER BY created_at, id""",
                (status_filter, user["username"]),
            )
        else:
            cur.execute(
                """SELECT id, customer_id, station_id, required_size, status,
                           compartment_id, queue_order,
                          last_attempt_at, created_at, allocated_at
                   FROM locker_reservations
                   WHERE customer_id = %s
                   ORDER BY created_at, id""",
                (user["username"],),
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

@app.post("/api/reservations/{reservation_id}/arrive", tags=["parcels"])
def mark_reservation_arrived(reservation_id: UUID, user: dict = Depends(authenticated_user)):
    """Mark an allocated parcel as arrived and return its one-time collection password."""
    with db_connection() as conn, conn.cursor() as cur:
        _acquire_allocation_lock(cur)
        reservation = _get_reservation(cur, reservation_id, lock=True)
        if reservation["customer_id"] != user["username"]:
            raise HTTPException(status_code=403, detail="This parcel belongs to another account")
        if reservation["status"] == "ARRIVED":
            raise HTTPException(status_code=409, detail="Parcel has already been marked as arrived")
        if reservation["status"] != "ALLOCATED" or reservation["compartment_id"] is None:
            raise HTTPException(status_code=409, detail="Only an allocated parcel can be marked as arrived")
        cur.execute(
            """UPDATE locker_reservations
               SET status = 'ARRIVED', arrived_at = NOW(), updated_at = NOW()
               WHERE id = %s""",
            (str(reservation_id),),
        )
        collection_password = _generate_collection_password(cur, reservation_id)
        return {
            "reservation_id": str(reservation_id),
            "status": "ARRIVED",
            "collection_password": collection_password,
            "message": "Give this password to the parcel recipient. It is shown only once.",
        }


# list of parcels available for collection
@app.get("/api/parcels/collection", tags=["parcels"])
def list_parcels_for_collection(user: dict = Depends(authenticated_user)):
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
               JOIN locker s
                   ON s.id = r.station_id
               JOIN locker_compartments c
                   ON c.id = r.compartment_id
               WHERE r.status = 'ARRIVED' AND r.customer_id = %s
               ORDER BY r.arrived_at ASC""",
            (user["username"],),
        )

        return cur.fetchall()

# receive parcel unlock locker feature
@app.post("/api/reservations/{reservation_id}/unlock", tags=["parcels"])
def unlock_locker(
    reservation_id: UUID,
    payload: UnlockRequest,
    user: dict = Depends(authenticated_user),
):
    """Verify collection password and simulate locker unlocking."""

    with db_connection() as conn, conn.cursor() as cur:
        _acquire_allocation_lock(cur)
        reservation = _get_reservation(cur, reservation_id, lock=True)
        if reservation["customer_id"] != user["username"]:
            raise HTTPException(status_code=403, detail="This parcel belongs to another account")

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
        cur.execute(
            """UPDATE locker_reservations
               SET last_attempt_at = NULL, queue_updated_at = clock_timestamp()
               WHERE status = 'CONDITIONAL' AND station_id = %s""",
            (reservation["station_id"],),
        )

        return {
            "message": "Locker unlocked",
            "reservation_id": str(reservation_id),
            "status": "FULFILLED",
        }

@app.post("/api/reservations/{reservation_id}/fulfill", response_model=ReservationOut, tags=["reservations"])
def fulfill_reservation(reservation_id: UUID, user: dict = Depends(authenticated_user)):
    """Complete a reservation and refresh its station queue after releasing the slot."""
    with db_connection() as conn, conn.cursor() as cur:
        _acquire_allocation_lock(cur)
        reservation = _get_reservation(cur, reservation_id, lock=True)
        if reservation["customer_id"] != user["username"]:
            raise HTTPException(status_code=403, detail="This reservation belongs to another account")
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
                          compartment_id, queue_order,
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
def cancel_reservation(reservation_id: UUID, user: dict = Depends(authenticated_user)):
    with db_connection() as conn, conn.cursor() as cur:
        _acquire_allocation_lock(cur)
        reservation = _get_reservation(cur, reservation_id, lock=True)
        if reservation["customer_id"] != user["username"]:
            raise HTTPException(status_code=403, detail="This reservation belongs to another account")
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
                          compartment_id, queue_order,
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



# ====================================
# Development-only admin/testing API
# ====================================
@app.get("/api/admin/lockers", tags=["admin"])
def admin_locker_snapshot(admin: dict = Depends(require_admin_user)):
    """Return the live station/compartment view expected by AdminPanel."""
    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT s.id AS station_id, s.name AS station_name,
                      c.id, c.label, c.size, c.active,
                        COALESCE(r.id, ls.id, c.reserved_by) AS reservation_id,

                        CASE
                            WHEN ls.id IS NOT NULL THEN 'LUGGAGE'
                            ELSE COALESCE(r.customer_id, 'UNKNOWN ALLOCATION')
                        END AS customer_id
               FROM locker s
               LEFT JOIN locker_compartments c ON c.station_id = s.id
               LEFT JOIN locker_reservations r
                 ON r.id = c.reserved_by AND r.status IN ('ALLOCATED','ARRIVED')
               LEFT JOIN luggage_sessions ls
                 ON ls.id = c.reserved_by
                AND ls.status IN ('STORED','PAYMENT_PENDING','PAID_AWAITING_PICKUP')
               WHERE s.active = TRUE
               ORDER BY s.id, c.id"""
        )
        rows = cur.fetchall()
    stations = {}
    for row in rows:
        sid = row["station_id"]
        if sid not in stations:
            stations[sid] = {
                "key": chr(65 + len(stations)),
                "station_id": sid,
                "station_name": row["station_name"],
                "compartments": [],
            }
        if row["id"] is not None:
            stations[sid]["compartments"].append({
                "id": row["id"], "label": row["label"], "size": row["size"],
                "active": row["active"], "reservation_id": str(row["reservation_id"]) if row["reservation_id"] else None,
                "customer_id": row["customer_id"],
            })
    return {"locations": list(stations.values())}


@app.delete("/api/admin/lockers/{compartment_id}/allocation", tags=["admin"])
def admin_release_allocation(compartment_id: int, admin: dict = Depends(require_admin_user)):
    with db_connection() as conn, conn.cursor() as cur:
        _acquire_allocation_lock(cur)
        cur.execute(
            """SELECT c.reserved_by, c.station_id
               FROM locker_compartments c WHERE c.id = %s FOR UPDATE""",
            (compartment_id,),
        )
        compartment = cur.fetchone()
        if compartment is None:
            raise HTTPException(status_code=404, detail="Compartment not found")
        allocation_id = compartment["reserved_by"]
        if allocation_id is None:
            raise HTTPException(status_code=409, detail="Compartment is already available")
        cur.execute(
            """UPDATE locker_reservations SET status = 'CANCELLED', updated_at = NOW()
               WHERE id = %s AND status IN ('ALLOCATED','ARRIVED')""",
            (str(allocation_id),),
        )
        released_type = "PARCEL"
        if cur.rowcount == 0:
            cur.execute(
                """UPDATE luggage_sessions SET status = 'CANCELLED', updated_at = NOW()
                   WHERE id = %s AND status IN ('STORED','PAYMENT_PENDING','PAID_AWAITING_PICKUP')""",
                (str(allocation_id),),
            )
            if cur.rowcount:
                released_type = "LUGGAGE"
            else:
                raise HTTPException(
                    status_code=409,
                    detail="Allocation owner could not be identified"
                )
        cur.execute(
            "UPDATE locker_compartments SET reserved_by = NULL WHERE id = %s AND reserved_by = %s",
            (compartment_id, str(allocation_id)),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="Compartment ownership changed")
        cur.execute(
            """UPDATE locker_reservations
               SET last_attempt_at = NULL, queue_updated_at = clock_timestamp()
               WHERE status = 'CONDITIONAL' AND station_id = %s""",
            (compartment["station_id"],),
        )
    return {"compartment_id": compartment_id, "released": released_type}


@app.post("/api/admin/tests/concurrency", tags=["admin"])
def admin_concurrency_test(payload: AdminConcurrencyRequest, admin: dict = Depends(require_admin_user)):
    """Issue concurrent real booking operations, then race arrival transitions."""
    total = sum(group.count for group in payload.groups)
    if total > 100:
        raise HTTPException(status_code=422, detail="Demo limit is 100 requests per test run")
    run_id = uuid4()
    started = time.monotonic()
    errors = []
    tasks = []

    # Validate stations and prepare the one synthetic account used by luggage
    # test sessions. Each actual booking below opens its own DB transaction.
    with db_connection() as conn, conn.cursor() as cur:
        station_ids = sorted({group.station_id for group in payload.groups})
        cur.execute(
            "SELECT id FROM locker WHERE active = TRUE AND id = ANY(%s)",
            (station_ids,),
        )
        active_stations = {row["id"] for row in cur.fetchall()}
        for group in payload.groups:
            if group.station_id not in active_stations:
                errors.append({"kind": group.kind, "detail": f"Station {group.station_id} is inactive or missing"})

        luggage_groups = [
            group for group in payload.groups
            if group.kind == "LUGGAGE" and group.station_id in active_stations
        ]
        test_user = None
        if luggage_groups:
            test_user_id = uuid4()
            test_username = f"admin-test-{run_id.hex[:12]}"
            test_salt = secrets.token_bytes(16)
            test_password_hash = hashlib.pbkdf2_hmac(
                "sha256", secrets.token_urlsafe(24).encode("utf-8"), test_salt, 100000
            ).hex()
            cur.execute(
                """INSERT INTO public."user"(id, username, password_hash)
                   VALUES (%s, %s, %s)""",
                (str(test_user_id), test_username,
                 f"pbkdf2_sha256$100000${test_salt.hex()}${test_password_hash}"),
            )
            test_user = {"id": test_user_id, "username": test_username}

        for group in payload.groups:
            if group.station_id not in active_stations:
                continue
            if group.kind == "LUGGAGE":
                if group.size != Size.XL:
                    errors.append({"kind": group.kind, "detail": "Luggage test requests must use XL compartments"})
                    continue
                cur.execute(
                    """SELECT id FROM locker_compartments
                       WHERE station_id = %s AND size = 'XL'
                         AND active = TRUE AND reserved_by IS NULL
                       ORDER BY id LIMIT %s""",
                    (group.station_id, group.count),
                )
                candidates = cur.fetchall()
                for slot in candidates:
                    compartment_id = slot["id"]
                    tasks.append((
                        group.kind, "XL",
                        lambda compartment_id=compartment_id: store_luggage(
                            StoreLuggageRequest(compartment_id=compartment_id), test_user
                        ),
                    ))
                if len(candidates) < group.count:
                    errors.append({"kind": group.kind, "detail": "Not enough free XL compartments for every luggage test request"})
            else:
                for index in range(group.count):
                    customer_id = f"ADMIN-TEST-{run_id.hex[:8]}-{len(tasks)}"
                    tasks.append((
                        group.kind, group.size.value,
                        lambda group=group, customer_id=customer_id: create_reservation(
                            ReservationCreate(
                                customer_id=customer_id,
                                station_id=group.station_id,
                                required_size=group.size,
                            )
                        ),
                    ))

    # Run independent API operations concurrently. PostgreSQL advisory locks
    # serialize only the allocation-critical section, not request creation.
    created_items = []
    if tasks:
        with ThreadPoolExecutor(max_workers=min(32, len(tasks))) as pool:
            futures = {
                pool.submit(task): (kind, size)
                for kind, size, task in tasks
            }
            for future in as_completed(futures):
                kind, size = futures[future]
                try:
                    result = future.result()
                    if kind == "LUGGAGE":
                        created_items.append({
                            "id": str(result["session_id"]), "kind": kind, "size": size,
                            "status": "STORED", "compartment_id": result["compartment_id"],
                        })
                    else:
                        created_items.append({
                            "id": str(result["id"]), "kind": kind, "size": size,
                            "status": result["status"], "compartment_id": result.get("compartment_id"),
                        })
                except Exception as exc:
                    detail = getattr(exc, "detail", str(exc))
                    errors.append({"kind": kind, "detail": str(detail)})

    # Let the standard quiet-period scheduler see the full batch, then run it
    # under the exact same transaction advisory lock as the background worker.
    time.sleep((QUEUE_QUIET_PERIOD_MS + 50) / 1000)
    with db_connection() as conn, conn.cursor() as cur:
        _acquire_allocation_lock(cur)
        _process_if_queue_is_quiet_locked(cur)

    # Simultaneously transition every successful parcel allocation to ARRIVED.
    allocated_ids = []
    for item in created_items:
        if item["kind"] == "LUGGAGE":
            continue
        with db_connection() as conn, conn.cursor() as cur:
            current = _get_reservation(cur, UUID(item["id"]))
        if current["status"] == "ALLOCATED":
            allocated_ids.append(item["id"])
        else:
            item["status"] = current["status"]
            item["compartment_id"] = current["compartment_id"]

    def arrive(reservation_id):
        return mark_reservation_arrived(UUID(reservation_id))

    if allocated_ids:
        with ThreadPoolExecutor(max_workers=min(32, len(allocated_ids))) as pool:
            futures = {pool.submit(arrive, reservation_id): reservation_id for reservation_id in allocated_ids}
            for future in as_completed(futures):
                reservation_id = futures[future]
                try:
                    future.result()  # Password is deliberately not included in the admin report.
                    with db_connection() as conn, conn.cursor() as cur:
                        final_row = _get_reservation(cur, UUID(reservation_id))
                    for item in created_items:
                        if item["id"] == reservation_id:
                            item["status"] = final_row["status"]
                            item["compartment_id"] = final_row["compartment_id"]
                            break
                except Exception as exc:
                    errors.append({"kind": "ARRIVAL", "id": reservation_id,
                                   "detail": str(getattr(exc, "detail", exc))})

    assigned = [x["compartment_id"] for x in created_items if x["compartment_id"] is not None]
    duplicates = sorted({value for value in assigned if assigned.count(value) > 1})
    allocated = sum(1 for x in created_items if x["status"] in ("ARRIVED", "STORED"))
    conditional = sum(1 for x in created_items if x["status"] == "CONDITIONAL")
    return {
        "run_id": str(run_id),
        "total_requested": total,
        "created": len(created_items),
        "allocated": allocated,
        "conditional": conditional,
        "duplicate_compartments": duplicates,
        "errors": errors,
        "results": created_items,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "note": "Concurrent booking calls were written to PostgreSQL. Parcel allocations were processed by the shared queue and arrival transitions were raced concurrently; luggage test sessions remain STORED. Use Locker Status to release test allocations.",
    }


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
            "POST /api/reservations/{id}/arrive returns a one-time collection password",
            "POST /api/reservations/{id}/unlock verifies the password and releases the compartment",
            "POST /api/luggage/sessions stores luggage in an XL compartment for the signed-in user",
        ],
        "allocation_policy": "selected station only; smallest available compartment that fits",
        "queue_policy": "larger parcel sizes first; FIFO by database queue_order for equal sizes",
        "queue_batch_window_ms": QUEUE_QUIET_PERIOD_MS,
    }