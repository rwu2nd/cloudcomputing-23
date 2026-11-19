"""Test lockers and accounts for the admin dashboard's Stress Test.

Stress tests run real bookings, allocations, door unlocks and luggage sessions,
but only against a dedicated station, "Stress Test Bay" (zone STRESS_TEST),
created on demand with its own compartments. That station is hidden from users,
from the admin Locker Status view and from the business metrics, so stress
tests never use or count towards the real lockers.

Only accounts created by a stress run (usernames starting with "stress-") may
book or store luggage there, and those accounts may only use the test lockers.
Public registration of "stress-" usernames is refused (see luggage.py).
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Optional
from uuid import uuid4

from app.luggage import (
    STRESS_PREFIX,
    STRESS_ZONE,
    _acquire_locker_lock,
    _db_connection,
    _hash_password,
    _issue_access_token,
)

STRESS_STATION_NAME = "Stress Test Bay"
RUNNER_USERNAME = "stress-runner"
# 14 compartments: enough to see contention with a handful of users.
LAYOUT = (("S", 4), ("M", 4), ("L", 3), ("XL", 3))


def _ensure_station(cur) -> int:
    cur.execute(
        """INSERT INTO locker (name, zone) VALUES (%s, %s)
           ON CONFLICT (name) DO UPDATE SET zone = EXCLUDED.zone, active = TRUE
           RETURNING id""",
        (STRESS_STATION_NAME, STRESS_ZONE),
    )
    station_id = cur.fetchone()["id"]
    for size, count in LAYOUT:
        for i in range(1, count + 1):
            cur.execute(
                """INSERT INTO locker_compartments (station_id, label, size)
                   VALUES (%s, %s, %s) ON CONFLICT (station_id, label) DO NOTHING""",
                (station_id, f"T-{size}{i:02d}", size),
            )
    return station_id


def station_id() -> Optional[int]:
    with _db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM locker WHERE name = %s AND zone = %s", (STRESS_STATION_NAME, STRESS_ZONE))
        row = cur.fetchone()
    return row["id"] if row else None


def prepare_run(counts: dict[str, int]) -> dict:
    """Create the test lockers (if needed), a runner admin and the run's users.

    counts maps scenario -> number of accounts. Browse accounts sign in with a
    password (that is what makes them CPU-heavy); the others get a token.
    """
    run = secrets.token_hex(3)
    password = secrets.token_urlsafe(12)
    # One password hash shared by every account of this run: hashing is
    # deliberately slow, and these accounts are thrown away anyway.
    password_hash = _hash_password(password)
    users: dict[str, list[dict]] = {}
    with _db_connection() as conn, conn.cursor() as cur:
        station = _ensure_station(cur)
        cur.execute(
            """INSERT INTO "user" (id, username, password_hash, role)
               VALUES (%s, %s, %s, 'ADMIN') ON CONFLICT DO NOTHING""",
            (str(uuid4()), RUNNER_USERNAME, _hash_password(secrets.token_urlsafe(24))),
        )
        cur.execute('SELECT id FROM "user" WHERE username = %s', (RUNNER_USERNAME,))
        runner_token = _issue_access_token(cur, cur.fetchone()["id"])["access_token"]
        for scenario, count in counts.items():
            users[scenario] = []
            for i in range(count):
                user_id = uuid4()
                username = f"{STRESS_PREFIX}{run}-{scenario}-{i}"
                cur.execute(
                    """INSERT INTO "user" (id, username, password_hash) VALUES (%s, %s, %s)""",
                    (str(user_id), username, password_hash),
                )
                entry = {"id": str(user_id), "username": username}
                if scenario != "browse":
                    entry["token"] = _issue_access_token(cur, user_id)["access_token"]
                users[scenario].append(entry)
        cur.execute("SELECT id, label FROM locker_compartments WHERE station_id = %s", (station,))
        labels = {row["id"]: row["label"] for row in cur.fetchall()}
    return {"run": run, "station_id": station, "runner_token": runner_token,
            "password": password, "users": users, "labels": labels}


def reset() -> dict:
    """Free every test locker and sign all stress accounts out."""
    sid = station_id()
    if sid is None:
        return {"released": 0}
    with _db_connection() as conn, conn.cursor() as cur:
        _acquire_locker_lock(cur)
        cur.execute(
            """UPDATE locker_reservations SET status = 'CANCELLED', updated_at = NOW()
               WHERE station_id = %s AND status IN ('CONDITIONAL', 'ALLOCATED', 'ARRIVED')""",
            (sid,),
        )
        cancelled = cur.rowcount
        cur.execute(
            """UPDATE luggage_sessions SET status = 'CANCELLED', updated_at = NOW()
               WHERE status IN ('STORED', 'PAYMENT_PENDING', 'PAID_AWAITING_PICKUP')
                 AND compartment_id IN (SELECT id FROM locker_compartments WHERE station_id = %s)""",
            (sid,),
        )
        cancelled += cur.rowcount
        cur.execute(
            "UPDATE locker_compartments SET reserved_by = NULL WHERE station_id = %s AND reserved_by IS NOT NULL",
            (sid,),
        )
        released = cur.rowcount
        cur.execute(
            """UPDATE luggage_auth_sessions SET revoked_at = NOW()
               WHERE revoked_at IS NULL
                 AND user_id IN (SELECT id FROM "user" WHERE username LIKE %s)""",
            (STRESS_PREFIX + "%",),
        )
    return {"released": released, "cancelled": cancelled}


def snapshot(since: Optional[datetime]) -> Optional[dict]:
    """Test lockers and what the stress run has done since it started."""
    sid = station_id()
    if sid is None:
        return None
    with _db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT c.id, c.label, c.size,
                      CASE
                          WHEN c.reserved_by IS NULL THEN 'free'
                          WHEN ls.id IS NOT NULL THEN 'luggage'
                          WHEN r.status = 'ARRIVED' THEN 'parcel'
                          ELSE 'booked'
                      END AS state
               FROM locker_compartments c
               LEFT JOIN locker_reservations r ON r.id = c.reserved_by
               LEFT JOIN luggage_sessions ls ON ls.id = c.reserved_by
               WHERE c.station_id = %s
               ORDER BY CASE c.size WHEN 'S' THEN 1 WHEN 'M' THEN 2 WHEN 'L' THEN 3 ELSE 4 END, c.label""",
            (sid,),
        )
        lockers = cur.fetchall()
        reservations, luggage = {}, {}
        if since is not None:
            cur.execute(
                """SELECT status, count(*) AS n FROM locker_reservations
                   WHERE station_id = %s AND created_at >= %s GROUP BY status""",
                (sid, since),
            )
            reservations = {row["status"]: row["n"] for row in cur.fetchall()}
            cur.execute(
                """SELECT ls.status, count(*) AS n FROM luggage_sessions ls
                   JOIN locker_compartments c ON c.id = ls.compartment_id
                   WHERE c.station_id = %s AND ls.created_at >= %s GROUP BY ls.status""",
                (sid, since),
            )
            luggage = {row["status"]: row["n"] for row in cur.fetchall()}
    return {"station_id": sid, "lockers": lockers, "reservations": reservations, "luggage": luggage}


# ---------------------------------------------------------------- run reports

def _ensure_reports_table(cur) -> None:
    # Created on first use (like the notification service's table), so
    # existing databases need no migration.
    cur.execute(
        """CREATE TABLE IF NOT EXISTS stress_reports (
               run_name    TEXT PRIMARY KEY,
               started_at  TIMESTAMPTZ NOT NULL,
               finished_at TIMESTAMPTZ,
               report      JSONB NOT NULL,
               created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
           )"""
    )


def save_report(run_name: str, started: datetime, finished: Optional[datetime], report: dict) -> None:
    import json
    with _db_connection() as conn, conn.cursor() as cur:
        _ensure_reports_table(cur)
        cur.execute(
            """INSERT INTO stress_reports (run_name, started_at, finished_at, report)
               VALUES (%s, %s, %s, %s::jsonb)
               ON CONFLICT (run_name) DO UPDATE SET report = EXCLUDED.report, finished_at = EXCLUDED.finished_at""",
            (run_name, started, finished, json.dumps(report, default=str)),
        )


def get_report(run_name: str) -> Optional[dict]:
    with _db_connection() as conn, conn.cursor() as cur:
        _ensure_reports_table(cur)
        cur.execute("SELECT report FROM stress_reports WHERE run_name = %s", (run_name,))
        row = cur.fetchone()
    return row["report"] if row else None


def list_reports(limit: int = 30) -> list[dict]:
    with _db_connection() as conn, conn.cursor() as cur:
        _ensure_reports_table(cur)
        cur.execute(
            """SELECT run_name, started_at, finished_at,
                      report -> 'config' AS config,
                      report -> 'summary' AS summary
               FROM stress_reports ORDER BY started_at DESC LIMIT %s""",
            (limit,),
        )
        return cur.fetchall()


def correctness(since: datetime, until: datetime) -> dict:
    """Database-level checks for one run on the test lockers.

    double_allocations: pairs of bookings/luggage sessions whose time holding
    the same compartment overlapped. Every release path sets the final status
    and updated_at in the same transaction, so for finished rows updated_at
    is the moment the compartment was let go.
    """
    sid = station_id()
    if sid is None:
        return {}
    with _db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """WITH holds AS (
                   SELECT 'parcel' AS kind, r.id::text AS id, r.compartment_id,
                          r.allocated_at AS held_from,
                          CASE WHEN r.status IN ('FULFILLED', 'CANCELLED') THEN r.updated_at ELSE NOW() END AS held_to
                   FROM locker_reservations r
                   WHERE r.station_id = %(sid)s AND r.created_at BETWEEN %(since)s AND %(until)s
                     AND r.compartment_id IS NOT NULL AND r.allocated_at IS NOT NULL
                   UNION ALL
                   SELECT 'luggage', ls.id::text, ls.compartment_id, ls.stored_at,
                          CASE WHEN ls.status = 'RETRIEVED' THEN ls.retrieved_at
                               WHEN ls.status = 'CANCELLED' THEN ls.updated_at ELSE NOW() END
                   FROM luggage_sessions ls
                   JOIN locker_compartments c ON c.id = ls.compartment_id
                   WHERE c.station_id = %(sid)s AND ls.created_at BETWEEN %(since)s AND %(until)s
               )
               SELECT
                   (SELECT count(*) FROM holds a JOIN holds b
                      ON a.compartment_id = b.compartment_id AND a.id < b.id
                     AND a.held_from < b.held_to AND b.held_from < a.held_to) AS double_allocations,
                   (SELECT count(*) FROM holds) AS holds_checked,
                   (SELECT count(*) FROM locker_compartments
                     WHERE station_id = %(sid)s AND reserved_by IS NOT NULL) AS lockers_still_held,
                   (SELECT count(*) FROM locker_reservations
                     WHERE station_id = %(sid)s AND created_at BETWEEN %(since)s AND %(until)s
                       AND status = 'FULFILLED') AS parcels_collected,
                   (SELECT count(*) FROM locker_reservations
                     WHERE station_id = %(sid)s AND created_at BETWEEN %(since)s AND %(until)s
                       AND status = 'CANCELLED') AS parcels_cancelled,
                   (SELECT count(*) FROM luggage_sessions ls JOIN locker_compartments c ON c.id = ls.compartment_id
                     WHERE c.station_id = %(sid)s AND ls.created_at BETWEEN %(since)s AND %(until)s
                       AND ls.status = 'RETRIEVED') AS luggage_collected,
                   (SELECT count(*) FROM locker_reservations r JOIN locker s ON s.id = r.station_id
                     WHERE s.zone <> %(zone)s AND r.created_at BETWEEN %(since)s AND %(until)s) AS real_bookings,
                   (SELECT count(*) FROM locker_reservations r JOIN locker s ON s.id = r.station_id
                     WHERE s.zone <> %(zone)s AND r.created_at BETWEEN %(since)s AND %(until)s
                       AND r.status IN ('ALLOCATED', 'ARRIVED', 'FULFILLED')) AS real_bookings_ok""",
            {"sid": sid, "since": since, "until": until, "zone": STRESS_ZONE},
        )
        return dict(cur.fetchone())
