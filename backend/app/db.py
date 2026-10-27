"""Database access layer: one small connection pool shared by all requests.

Why this exists
  * psycopg2's `with conn:` only commits/rolls back - it does NOT close the
    connection. Opening a new connection per request therefore leaks sockets.
  * A bounded pool + semaphore caps connections per replica, so
    replicas * max_connections stays below PostgreSQL's max_connections.
  * The pool is created lazily, so the API still starts if PostgreSQL is not
    up yet (/health/ready reports 503 until it is).

Usage (unchanged from the old helper):
    with db_connection() as conn, conn.cursor() as cur:
        cur.execute(...)
The transaction commits on normal exit and rolls back on any exception.
"""

import atexit
import os
import threading
from contextlib import contextmanager

import psycopg2
from fastapi import HTTPException
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from app.config import get_settings

POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
POOL_WAIT_SECONDS = float(os.getenv("DB_POOL_WAIT_SECONDS", "5"))

_pool = None
_slots = threading.BoundedSemaphore(POOL_MAX)
_init_lock = threading.Lock()


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _init_lock:
            if _pool is None:
                s = get_settings()
                _pool = ThreadedConnectionPool(
                    POOL_MIN,
                    POOL_MAX,
                    host=s.postgres_host,
                    port=s.postgres_port,
                    dbname=s.postgres_db,
                    user=s.postgres_user,
                    password=s.postgres_password,
                    connect_timeout=3,
                    application_name="smart-locker-api",
                    options="-c statement_timeout=10000",
                    cursor_factory=RealDictCursor,
                )
    return _pool


def _checkout():
    """Get a live connection; transparently replace ones broken by a DB restart."""
    pool = _get_pool()
    for _ in range(3):
        conn = pool.getconn()
        try:
            if conn.closed:
                raise psycopg2.InterfaceError("closed")
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            conn.rollback()
            return conn
        except (psycopg2.Error, OSError):
            pool.putconn(conn, close=True)
    raise psycopg2.OperationalError("could not obtain a working database connection")


@contextmanager
def db_connection():
    if not _slots.acquire(timeout=POOL_WAIT_SECONDS):
        raise HTTPException(status_code=503, detail="Database is busy, try again")
    conn = None
    try:
        try:
            conn = _checkout()
        except (psycopg2.Error, OSError):
            raise HTTPException(status_code=503, detail="Database unavailable")
        with conn:  # commit on success, rollback on exception
            yield conn
    finally:
        if conn is not None:
            _get_pool().putconn(conn, close=bool(conn.closed))
        _slots.release()


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


atexit.register(close_pool)
