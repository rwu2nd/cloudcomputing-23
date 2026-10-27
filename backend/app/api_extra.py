"""Read-only endpoints backed by the views in database/init/01-schema.sql.

Included from main.py with: app.include_router(extra_router)
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.db import db_connection

router = APIRouter(prefix="/api", tags=["availability & reporting"])

PARCEL_STATES = {"IN_TRANSIT", "STORED", "COLLECTED", "CANCELLED"}


@router.get("/availability")
def availability_all():
    """Free/occupied compartments for every station, split by size."""
    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT station_id, station_name, zone, size, total, available, occupied
               FROM v_station_availability ORDER BY station_id,
               array_position(ARRAY['S','M','L','XL'], size)"""
        )
        return cur.fetchall()


@router.get("/stations/{station_id}/availability")
def availability_for_station(station_id: int):
    with db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM locker_stations WHERE id=%s", (station_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Station not found")
        cur.execute(
            """SELECT size, total, available, occupied FROM v_station_availability
               WHERE station_id=%s ORDER BY array_position(ARRAY['S','M','L','XL'], size)""",
            (station_id,),
        )
        return {"station_id": station_id, "sizes": cur.fetchall()}


@router.get("/compartments")
def list_all_compartments(station_id: Optional[int] = None):
    """Compartment grid with the current holder - feeds the dashboard."""
    with db_connection() as conn, conn.cursor() as cur:
        sql = "SELECT * FROM v_compartment_status"
        args = ()
        if station_id is not None:
            sql += " WHERE station_id=%s"
            args = (station_id,)
        cur.execute(sql + " ORDER BY station_id, compartment_id", args)
        return cur.fetchall()


@router.get("/parcels")
def list_parcels(state: Optional[str] = None, limit: int = 100):
    if state and state not in PARCEL_STATES:
        raise HTTPException(status_code=422, detail=f"state must be one of {sorted(PARCEL_STATES)}")
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT tracking_number, reservation_id, size, state, compartment_id,
                      stored_at, collected_at, created_at
               FROM parcels WHERE (%s::text IS NULL OR state=%s)
               ORDER BY created_at DESC LIMIT %s""",
            (state, state, limit),
        )
        return cur.fetchall()


@router.get("/parcels/{tracking_number}")
def get_parcel(tracking_number: str):
    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT tracking_number, reservation_id, size, state, compartment_id,
                      stored_at, collected_at, created_at
               FROM parcels WHERE tracking_number=%s""",
            (tracking_number.strip().upper(),),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Parcel not found")
        return row


@router.get("/reservations/{reservation_id}/events")
def reservation_events(reservation_id: UUID):
    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT event_type, from_status, to_status, station_id, compartment_id, created_at
               FROM reservation_events WHERE reservation_id=%s ORDER BY created_at, id""",
            (str(reservation_id),),
        )
        return cur.fetchall()


@router.get("/stats")
def stats():
    """Dashboard + evaluation numbers in one call."""
    with db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM v_station_summary ORDER BY station_id")
        stations = cur.fetchall()
        cur.execute("SELECT status, reservations FROM v_reservation_counts")
        by_status = {r["status"]: r["reservations"] for r in cur.fetchall()}
        cur.execute("SELECT COUNT(*) AS n FROM v_integrity_double_assignments")
        doubles = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM v_integrity_orphans")
        orphans = cur.fetchone()["n"]
        return {
            "stations": stations,
            "reservations_by_status": by_status,
            "integrity": {"double_assignments": doubles, "orphaned_compartments": orphans},
        }
