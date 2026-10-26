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

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Path, status
from pydantic import BaseModel, Field, ConfigDict

from app.config import get_settings
from app.health import router as health_router

app = FastAPI(
    title="Smart Locker API",
    description="Conditional reservations, simulated arrivals, and concurrent-safe locker allocation.",
    version="1.0.0",
)
app.include_router(health_router)

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

'''
# initialize schema within db
def initialize_schema() -> None:
    """Small self-contained schema for this backend assignment; safe to run repeatedly."""
    ddl = """
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
        preferred_station_id BIGINT NOT NULL REFERENCES locker_stations(id),
        backup_station_ids BIGINT[] NOT NULL DEFAULT '{}',
        required_size TEXT NOT NULL CHECK (required_size IN ('S','M','L','XL')),
        priority INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL CHECK (status IN
            ('CONDITIONAL','ALLOCATED','ARRIVED','FULFILLED','CANCELLED','UNALLOCATED')),
        allocated_station_id BIGINT NULL REFERENCES locker_stations(id),
        compartment_id BIGINT NULL UNIQUE REFERENCES locker_compartments(id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        arrived_at TIMESTAMPTZ NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_reservations_queue
        ON locker_reservations(status, priority DESC, created_at ASC);
    """
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)


@app.on_event("startup")
def startup():
    # A DB outage should make readiness fail; the process can still start and recover.
    try:
        initialize_schema()
    except psycopg2.Error:
        # /health/ready reports the real database state; no hidden in-memory fallback.
        pass
'''

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
    preferred_station_id: int = Field(gt=0)
    backup_station_ids: list[int] = Field(default_factory=list, max_length=20)
    required_size: Size
    # Higher priority first; ties use FIFO created_at, then UUID as stable tie-breaker.
    priority: int = Field(default=0, ge=0, le=100)

# validates outgoing response
class ReservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    customer_id: str
    preferred_station_id: int
    backup_station_ids: list[int]
    required_size: str
    priority: int
    status: str
    allocated_station_id: Optional[int] = None
    compartment_id: Optional[int] = None
    created_at: datetime
    arrived_at: Optional[datetime] = None




# ====================================
# Helper functions
# ====================================

def row_to_reservation(row: dict) -> dict:
    # Normalize psycopg's array values for JSON/Pydantic.
    row["backup_station_ids"] = list(row.get("backup_station_ids") or [])
    return row

# helper function to get reservation details
def _get_reservation(cur, reservation_id: UUID, lock: bool = False):
    cur.execute(
        """SELECT id, customer_id, preferred_station_id, backup_station_ids,
                  required_size, priority, status, allocated_station_id,
                  compartment_id, created_at, arrived_at
           FROM locker_reservations WHERE id = %s""" + (" FOR UPDATE" if lock else ""),
        (str(reservation_id),),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return row_to_reservation(row)

# function to allocate which locker to place parcel in
def _allocate(cur, reservation: dict, arrival: bool = True) -> dict:
    """Allocate preferred station first, then backups in caller-supplied order.

    FOR UPDATE SKIP LOCKED and the unique reservation compartment constraint protect
    against two API replicas claiming the same compartment.
    """

    # checks whether reservation can be made or not (has to be cancelled, fulfilled, or unallocated)
    if reservation["status"] in ("CANCELLED", "FULFILLED", "UNALLOCATED"):
        raise HTTPException(status_code=409, detail=f"Cannot allocate reservation in {reservation['status']} state")
    
    if reservation["compartment_id"] is not None:
        if arrival:
            cur.execute(
                """UPDATE locker_reservations SET status='ARRIVED', arrived_at=COALESCE(arrived_at,NOW()),
                   updated_at=NOW() WHERE id=%s RETURNING *""",
                (str(reservation["id"]),),
            )
            return row_to_reservation(cur.fetchone())
        return reservation

    # searches for preferred station + backup station
    station_order = []
    for station_id in [reservation["preferred_station_id"], *reservation["backup_station_ids"]]:
        if station_id not in station_order:
            station_order.append(station_id)

    for station_id in station_order:
        cur.execute("SELECT id FROM locker_stations WHERE id=%s AND active=TRUE", (station_id,))
        if cur.fetchone() is None:
            continue
        cur.execute(
            """SELECT c.id FROM locker_compartments c
               WHERE c.station_id=%s AND c.active=TRUE AND c.size=%s AND c.reserved_by IS NULL
               ORDER BY c.id
               FOR UPDATE SKIP LOCKED LIMIT 1""",
            (station_id, reservation["required_size"]),
        )
        compartment = cur.fetchone()
        if not compartment:
            continue
        compartment_id = compartment["id"]
        # reserved_by and reservation's unique compartment_id are updated atomically.
        cur.execute(
            "UPDATE locker_compartments SET reserved_by=%s WHERE id=%s AND reserved_by IS NULL",
            (str(reservation["id"]), compartment_id),
        )
        if cur.rowcount != 1:
            continue
        new_status = "ARRIVED" if arrival else "ALLOCATED"
        cur.execute(
            """UPDATE locker_reservations SET status=%s, allocated_station_id=%s,
               compartment_id=%s, arrived_at=CASE WHEN %s THEN COALESCE(arrived_at,NOW()) ELSE arrived_at END,
               updated_at=NOW() WHERE id=%s RETURNING *""",
            (new_status, station_id, compartment_id, arrival, str(reservation["id"])),
        )
        return row_to_reservation(cur.fetchone())

    # The conditional reservation is retained: a later arrival/retry can try again.
    cur.execute(
        """UPDATE locker_reservations SET status='CONDITIONAL', updated_at=NOW()
           WHERE id=%s RETURNING *""",
        (str(reservation["id"]),),
    )
    return row_to_reservation(cur.fetchone())


# ====================================
# Routes
# ====================================

@app.get("/api/stations", tags=["stations"])
def list_stations():
    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT s.id,s.name,s.zone,s.active,
                      COUNT(c.id) FILTER (WHERE c.active) AS total_compartments,
                      COUNT(c.id) FILTER (WHERE c.active AND c.reserved_by IS NULL) AS available_compartments
               FROM locker_stations s LEFT JOIN locker_compartments c ON c.station_id=s.id
               GROUP BY s.id ORDER BY s.id"""
        )
        return cur.fetchall()


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
            cur.execute("SELECT id FROM locker_stations WHERE id=%s", (station_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Station not found")
            cur.execute(
                """INSERT INTO locker_compartments(station_id,label,size)
                   VALUES (%s,%s,%s) RETURNING id,station_id,label,size,active""",
                (station_id, payload.label.strip(), payload.size.value),
            )
            return cur.fetchone()
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
@app.post("/api/reservations", response_model=ReservationOut, status_code=status.HTTP_201_CREATED, tags=["reservations"])
def create_reservation(payload: ReservationCreate):
    """Accept a reservation even when the preferred station is full.

    If a suitable compartment exists now, reserve it at the preferred station or
    first suitable backup. Otherwise retain it as CONDITIONAL until arrival.
    """
    if payload.preferred_station_id in payload.backup_station_ids:
        backups = [i for i in payload.backup_station_ids if i != payload.preferred_station_id]
    else:
        backups = payload.backup_station_ids
    with db_connection() as conn, conn.cursor() as cur:
        all_ids = [payload.preferred_station_id, *backups]
        cur.execute("SELECT id FROM locker_stations WHERE id = ANY(%s) AND active=TRUE", (all_ids,))
        existing = {r["id"] for r in cur.fetchall()}
        missing = [sid for sid in all_ids if sid not in existing]
        if missing:
            raise HTTPException(status_code=422, detail={"message": "Unknown or inactive station IDs", "station_ids": missing})
        cur.execute(
            """INSERT INTO locker_reservations
               (id,customer_id,preferred_station_id,backup_station_ids,required_size,priority,status)
               VALUES (gen_random_uuid(),%s,%s,%s,%s,%s,'CONDITIONAL') RETURNING id""",
            (payload.customer_id.strip(), payload.preferred_station_id, backups, payload.required_size.value, payload.priority),
        )
        reservation_id = cur.fetchone()["id"]
        # Reservation is a durable conditional request. Allocation happens on simulated
        # arrival (or through the priority-ordered batch processor), not at creation time.
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
                """SELECT id,customer_id,preferred_station_id,backup_station_ids,required_size,priority,
                          status,allocated_station_id,compartment_id,created_at,arrived_at
                   FROM locker_reservations WHERE status=%s ORDER BY priority DESC,created_at,id""",
                (status_filter,),
            )
        else:
            cur.execute(
                """SELECT id,customer_id,preferred_station_id,backup_station_ids,required_size,priority,
                          status,allocated_station_id,compartment_id,created_at,arrived_at
                   FROM locker_reservations ORDER BY priority DESC,created_at,id"""
            )
        return [row_to_reservation(r) for r in cur.fetchall()]


@app.post("/api/reservations/{reservation_id}/arrive", response_model=ReservationOut, tags=["reservations"])
def simulate_arrival(reservation_id: UUID):
    """Simulate customer arrival and try preferred station, then ordered backups."""
    with db_connection() as conn, conn.cursor() as cur:
        reservation = _get_reservation(cur, reservation_id, lock=True)
        result = _allocate(cur, reservation, arrival=True)
        if result["status"] == "CONDITIONAL":
            # HTTP 200 returns the durable conditional state; clients can retry later.
            return result
        return result


@app.post("/api/arrivals/process", tags=["reservations"])
def process_arrivals(limit: int = 100):
    """Process conditional reservations in priority-descending, FIFO order.

    Useful for demonstrating competing arrivals. A higher priority number wins;
    equal priorities are served oldest-first. This route is safe across replicas.
    """
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
    results = []
    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id FROM locker_reservations WHERE status='CONDITIONAL'
               ORDER BY priority DESC, created_at ASC, id ASC LIMIT %s FOR UPDATE SKIP LOCKED""",
            (limit,),
        )
        ids = [r["id"] for r in cur.fetchall()]
        for reservation_id in ids:
            reservation = _get_reservation(cur, reservation_id, lock=True)
            results.append(_allocate(cur, reservation, arrival=True))
    return {"processed": len(results), "results": results}


@app.post("/api/reservations/{reservation_id}/fulfill", response_model=ReservationOut, tags=["reservations"])
def fulfill_reservation(reservation_id: UUID):
    """Mark an arrived/allocated reservation fulfilled and release its compartment."""
    with db_connection() as conn, conn.cursor() as cur:
        reservation = _get_reservation(cur, reservation_id, lock=True)
        if reservation["status"] not in ("ARRIVED", "ALLOCATED"):
            raise HTTPException(status_code=409, detail="Only allocated or arrived reservations can be fulfilled")
        if reservation["compartment_id"] is not None:
            cur.execute(
                "UPDATE locker_compartments SET reserved_by=NULL WHERE id=%s AND reserved_by=%s",
                (reservation["compartment_id"], str(reservation_id)),
            )
        cur.execute(
            """UPDATE locker_reservations SET status='FULFILLED',updated_at=NOW()
               WHERE id=%s RETURNING id,customer_id,preferred_station_id,backup_station_ids,
               required_size,priority,status,allocated_station_id,compartment_id,created_at,arrived_at""",
            (str(reservation_id),),
        )
        return row_to_reservation(cur.fetchone())


@app.delete("/api/reservations/{reservation_id}", response_model=ReservationOut, tags=["reservations"])
def cancel_reservation(reservation_id: UUID):
    with db_connection() as conn, conn.cursor() as cur:
        reservation = _get_reservation(cur, reservation_id, lock=True)
        if reservation["status"] in ("FULFILLED", "CANCELLED"):
            raise HTTPException(status_code=409, detail=f"Cannot cancel reservation in {reservation['status']} state")
        if reservation["compartment_id"] is not None:
            cur.execute(
                "UPDATE locker_compartments SET reserved_by=NULL WHERE id=%s AND reserved_by=%s",
                (reservation["compartment_id"], str(reservation_id)),
            )
        cur.execute(
            """UPDATE locker_reservations SET status='CANCELLED',updated_at=NOW()
               WHERE id=%s RETURNING id,customer_id,preferred_station_id,backup_station_ids,
               required_size,priority,status,allocated_station_id,compartment_id,created_at,arrived_at""",
            (str(reservation_id),),
        )
        return row_to_reservation(cur.fetchone())


@app.get("/api", tags=["system"])
def api_index():
    return {
        "service": "smart-locker-api",
        "docs": "/docs",
        "allocation_policy": "preferred station, then backups in request order",
        "queue_policy": "higher priority first, FIFO for ties",
    }

