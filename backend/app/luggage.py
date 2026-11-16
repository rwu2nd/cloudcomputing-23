"""Virtual XL luggage-storage API for the Smart Locker demo.

Authentication uses username/password accounts and opaque bearer tokens.
Payment is simulated by a demo-only API action; no payment provider is called.
Locker opening is represented by a database event; no physical controller is
contacted. Luggage uses only XL compartments and shares the same PostgreSQL
advisory lock and locker_compartments.reserved_by field as parcel allocation.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import math
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

import psycopg2
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from psycopg2.extras import RealDictCursor

from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/luggage", tags=["luggage"])
bearer = HTTPBearer(auto_error=False)

# This value MUST remain identical to ALLOCATION_LOCK_KEY in app.main.
# Parcel and luggage operations therefore coordinate on the same physical slots.
ALLOCATION_LOCK_KEY = 731_904_221
SCHEMA_LOCK_KEY = 731_904_223
RATE_PER_DAY_CENTS = 1000  # SGD 10.00 per 24-hour block
BILLING_PERIOD_SECONDS = 24 * 60 * 60
PASSWORD_ITERATIONS = 310_000
AUTH_TTL_HOURS = int(os.getenv("LUGGAGE_AUTH_TTL_HOURS", "12"))
DEMO_MODE = os.getenv("LUGGAGE_DEMO_MODE", "false").lower() == "true"
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_.-]{3,32}$")
    password: str = Field(min_length=10, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class StoreLuggageRequest(BaseModel):
    compartment_id: int = Field(gt=0)


def _db_connection():
    settings = get_settings()
    return psycopg2.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=3,
        application_name="smart-locker-luggage-demo",
        cursor_factory=RealDictCursor,
    )


def _normalize_username(username: str) -> str:
    normalized = username.strip().lower()
    if not USERNAME_RE.fullmatch(normalized):
        raise HTTPException(
            status_code=422,
            detail="Username must be 3-32 characters using letters, numbers, dot, underscore or hyphen",
        )
    return normalized


def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256 and a fresh random salt."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, digest_hex = encoded_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        if iterations < 100_000 or iterations > 2_000_000:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _issue_access_token(cur, user_id: UUID) -> dict:
    token = secrets.token_urlsafe(32)
    cur.execute(
        """INSERT INTO luggage_auth_sessions(id, user_id, token_hash, expires_at)
           VALUES (%s, %s, %s, NOW() + (%s * INTERVAL '1 hour'))""",
        (str(uuid4()), str(user_id), _hash_token(token), AUTH_TTL_HOURS),
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": AUTH_TTL_HOURS * 3600,
    }


def _current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
) -> dict:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Login required")
    token_hash = _hash_token(credentials.credentials)
    with _db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT u.id, u.username
               FROM luggage_auth_sessions a
               JOIN luggage_users u ON u.id = a.user_id
               WHERE a.token_hash = %s
                 AND a.revoked_at IS NULL
                 AND a.expires_at > NOW()""",
            (token_hash,),
        )
        user = cur.fetchone()
    if user is None:
        raise HTTPException(status_code=401, detail="Login expired or invalid; please sign in again")
    return {"id": user["id"], "username": user["username"]}


def _acquire_locker_lock(cur) -> None:
    """Use the same transaction lock as parcel allocation in app.main."""
    cur.execute("SELECT pg_advisory_xact_lock(%s::bigint)", (ALLOCATION_LOCK_KEY,))


def initialize_luggage_schema() -> None:
    """Create demo account/luggage tables and migrate the previous OTP/PIN schema.

    Legacy phone/PIN columns are retained as nullable columns for compatibility.
    Existing phone-only sessions cannot safely be assigned to a username account;
    they remain in the database for an administrator to review/migrate manually.
    """
    ddl = """
    CREATE TABLE IF NOT EXISTS luggage_users (
        id UUID PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE UNIQUE INDEX IF NOT EXISTS uq_luggage_users_username_lower
        ON luggage_users(LOWER(username));

    CREATE TABLE IF NOT EXISTS luggage_auth_sessions (
        id UUID PRIMARY KEY,
        user_id UUID NULL,
        phone_number TEXT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        expires_at TIMESTAMPTZ NOT NULL,
        revoked_at TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    ALTER TABLE luggage_auth_sessions ADD COLUMN IF NOT EXISTS user_id UUID;
    ALTER TABLE luggage_auth_sessions ALTER COLUMN phone_number DROP NOT NULL;
    DO $$ BEGIN
        ALTER TABLE luggage_auth_sessions
            ADD CONSTRAINT fk_luggage_auth_user
            FOREIGN KEY (user_id) REFERENCES luggage_users(id);
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$;
    CREATE INDEX IF NOT EXISTS idx_luggage_auth_user
        ON luggage_auth_sessions(user_id, expires_at);

    CREATE TABLE IF NOT EXISTS luggage_sessions (
        id UUID PRIMARY KEY,
        user_id UUID NULL,
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
    ALTER TABLE luggage_sessions ADD COLUMN IF NOT EXISTS user_id UUID;
    ALTER TABLE luggage_sessions ALTER COLUMN phone_number DROP NOT NULL;
    ALTER TABLE luggage_sessions ALTER COLUMN pin_hash DROP NOT NULL;
    DO $$ BEGIN
        ALTER TABLE luggage_sessions
            ADD CONSTRAINT fk_luggage_session_user
            FOREIGN KEY (user_id) REFERENCES luggage_users(id);
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$;
    CREATE UNIQUE INDEX IF NOT EXISTS uq_luggage_active_compartment
        ON luggage_sessions(compartment_id)
        WHERE status IN ('STORED','PAYMENT_PENDING','PAID_AWAITING_PICKUP');
    CREATE INDEX IF NOT EXISTS idx_luggage_user_active
        ON luggage_sessions(user_id, status);

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

    CREATE TABLE IF NOT EXISTS luggage_unlock_events (
        id UUID PRIMARY KEY,
        luggage_session_id UUID NOT NULL REFERENCES luggage_sessions(id),
        user_id UUID NOT NULL REFERENCES luggage_users(id),
        compartment_id BIGINT NOT NULL REFERENCES locker_compartments(id),
        action TEXT NOT NULL CHECK (action IN ('STORE','RETRIEVE')),
        simulated BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_luggage_unlock_session
        ON luggage_unlock_events(luggage_session_id, created_at DESC);
    """
    with _db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s::bigint)", (SCHEMA_LOCK_KEY,))
        cur.execute(ddl)


def _billable_days(stored_at: datetime, billing_end: datetime) -> int:
    if stored_at.tzinfo is None:
        stored_at = stored_at.replace(tzinfo=timezone.utc)
    if billing_end.tzinfo is None:
        billing_end = billing_end.replace(tzinfo=timezone.utc)
    seconds = max(0.0, (billing_end - stored_at).total_seconds())
    return max(1, math.ceil(seconds / BILLING_PERIOD_SECONDS))


def _record_virtual_unlock(cur, user_id: UUID, session_id: UUID,
                           compartment_id: int, action: str) -> dict:
    """Record an app unlock event; this demo does not communicate with hardware."""
    event_id = uuid4()
    cur.execute(
        """INSERT INTO luggage_unlock_events
           (id, luggage_session_id, user_id, compartment_id, action, simulated)
           VALUES (%s, %s, %s, %s, %s, TRUE)
           RETURNING id, action, simulated, created_at""",
        (str(event_id), str(session_id), str(user_id), compartment_id, action),
    )
    return cur.fetchone()


def _session_query() -> str:
    return """SELECT ls.id AS session_id, ls.status, ls.stored_at,
                     ls.checkout_started_at, ls.billable_days, ls.amount_cents,
                     ls.retrieved_at, c.id AS compartment_id, c.label AS compartment_label,
                     c.station_id, s.name AS station_name, c.size
              FROM luggage_sessions ls
              JOIN locker_compartments c ON c.id = ls.compartment_id
              JOIN locker_stations s ON s.id = c.station_id"""


@router.post("/auth/register", status_code=status.HTTP_201_CREATED, tags=["authentication"])
def register_account(payload: RegisterRequest):
    """Create a username/password account and sign the new user in."""
    username = _normalize_username(payload.username)
    password_hash = _hash_password(payload.password)
    user_id = uuid4()
    with _db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO luggage_users(id, username, password_hash)
               VALUES (%s, %s, %s)
               ON CONFLICT DO NOTHING
               RETURNING id, username""",
            (str(user_id), username, password_hash),
        )
        user = cur.fetchone()
        if user is None:
            raise HTTPException(status_code=409, detail="That username is already registered")
        token_response = _issue_access_token(cur, user["id"])
    return {
        "user": {"id": str(user["id"]), "username": user["username"]},
        **token_response,
        "message": "Account created successfully",
    }


@router.post("/auth/login", tags=["authentication"])
def login_account(payload: LoginRequest):
    """Verify the password and issue a new opaque bearer token."""
    username = _normalize_username(payload.username)
    with _db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, username, password_hash FROM luggage_users WHERE username = %s",
            (username,),
        )
        user = cur.fetchone()
        if user is None or not _verify_password(payload.password, user["password_hash"]):
            # Do not reveal whether the username or password was incorrect.
            raise HTTPException(status_code=401, detail="Incorrect username or password")
        token_response = _issue_access_token(cur, user["id"])
    return {
        "user": {"id": str(user["id"]), "username": user["username"]},
        **token_response,
    }


@router.post("/auth/logout", tags=["authentication"])
def logout_account(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    user: dict = Depends(_current_user),
):
    """Revoke the current access token."""
    assert credentials is not None  # _current_user already validated it.
    with _db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE luggage_auth_sessions SET revoked_at = NOW() WHERE token_hash = %s AND user_id = %s",
            (_hash_token(credentials.credentials), str(user["id"])),
        )
    return {"logged_out": True}


@router.get("/auth/me", tags=["authentication"])
def get_current_account(user: dict = Depends(_current_user)):
    return {"id": str(user["id"]), "username": user["username"]}


@router.get("/slots", tags=["luggage"])
def list_available_luggage_slots(user: dict = Depends(_current_user)):
    """List free, active XL compartments the signed-in user can choose from."""
    with _db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT c.id AS compartment_id, c.station_id, s.name AS station_name,
                      s.zone, c.label, c.size
               FROM locker_compartments c
               JOIN locker_stations s ON s.id = c.station_id
               WHERE c.size = 'XL' AND c.active = TRUE AND s.active = TRUE
                 AND c.reserved_by IS NULL
               ORDER BY s.id, c.label"""
        )
        return cur.fetchall()


@router.post("/sessions", status_code=status.HTTP_201_CREATED, tags=["luggage"])
def store_luggage(payload: StoreLuggageRequest, user: dict = Depends(_current_user)):
    """Claim the exact XL compartment chosen by the user and simulate its unlock."""
    session_id = uuid4()
    user_id = user["id"]
    with _db_connection() as conn, conn.cursor() as cur:
        _acquire_locker_lock(cur)
        cur.execute(
            """SELECT c.id, c.station_id, c.label, c.size, s.name AS station_name
               FROM locker_compartments c
               JOIN locker_stations s ON s.id = c.station_id
               WHERE c.id = %s AND c.size = 'XL' AND c.active = TRUE
                 AND s.active = TRUE AND c.reserved_by IS NULL
               FOR UPDATE OF c""",
            (payload.compartment_id,),
        )
        compartment = cur.fetchone()
        if compartment is None:
            raise HTTPException(status_code=409, detail="That XL locker is no longer available")

        cur.execute(
            """INSERT INTO luggage_sessions(id, user_id, compartment_id, status)
               VALUES (%s, %s, %s, 'STORED')""",
            (str(session_id), str(user_id), payload.compartment_id),
        )
        cur.execute(
            """UPDATE locker_compartments SET reserved_by = %s
               WHERE id = %s AND reserved_by IS NULL""",
            (str(session_id), payload.compartment_id),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="That XL locker was just claimed")

        unlock_event = _record_virtual_unlock(
            cur, user_id, session_id, payload.compartment_id, "STORE"
        )

    return {
        "session_id": str(session_id),
        "status": "STORED",
        "compartment_id": compartment["id"],
        "station_id": compartment["station_id"],
        "station_name": compartment.get("station_name"),
        "compartment_label": compartment["label"],
        "size": "XL",
        "unlock": {
            "simulated": True,
            "event_id": str(unlock_event["id"]),
            "message": "Virtual locker unlocked. Place your luggage in the selected slot and close it in the demo.",
        },
    }


@router.get("/sessions", tags=["luggage"])
def my_luggage_sessions(user: dict = Depends(_current_user)):
    """Return only luggage sessions owned by the signed-in account."""
    with _db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            _session_query() + " WHERE ls.user_id = %s ORDER BY ls.stored_at DESC",
            (str(user["id"]),),
        )
        return cur.fetchall()


@router.get("/sessions/{session_id}", tags=["luggage"])
def get_my_luggage_session(session_id: UUID, user: dict = Depends(_current_user)):
    """Get one session only when it belongs to the signed-in user."""
    with _db_connection() as conn, conn.cursor() as cur:
        cur.execute(
            _session_query() + " WHERE ls.id = %s AND ls.user_id = %s",
            (str(session_id), str(user["id"])),
        )
        session = cur.fetchone()
    if session is None:
        raise HTTPException(status_code=404, detail="Luggage session not found")
    return session


def _create_demo_checkout(session_id: UUID, user_id: UUID) -> dict:
    """Freeze the duration-based amount and create a local demo payment record."""
    with _db_connection() as conn, conn.cursor() as cur:
        _acquire_locker_lock(cur)
        cur.execute(
            """SELECT id, user_id, status, stored_at
               FROM luggage_sessions
               WHERE id = %s AND user_id = %s
               FOR UPDATE""",
            (str(session_id), str(user_id)),
        )
        session = cur.fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="Luggage session not found")
        if session["status"] not in ("STORED", "PAYMENT_PENDING"):
            raise HTTPException(status_code=409, detail="Luggage session is not ready for checkout")

        cur.execute(
            """SELECT payment_reference, amount_cents, billable_days, status
               FROM luggage_payments
               WHERE luggage_session_id = %s AND status = 'PENDING'
               ORDER BY created_at DESC LIMIT 1""",
            (str(session_id),),
        )
        payment = cur.fetchone()
        if payment is None:
            now = datetime.now(timezone.utc)
            days = _billable_days(session["stored_at"], now)
            amount_cents = days * RATE_PER_DAY_CENTS
            payment_reference = uuid4()
            cur.execute(
                """INSERT INTO luggage_payments
                   (id, payment_reference, luggage_session_id, amount_cents, billable_days, status)
                   VALUES (%s, %s, %s, %s, %s, 'PENDING')""",
                (str(uuid4()), str(payment_reference), str(session_id), amount_cents, days),
            )
            cur.execute(
                """UPDATE luggage_sessions
                   SET status = 'PAYMENT_PENDING', checkout_started_at = %s,
                       billable_days = %s, amount_cents = %s, updated_at = NOW()
                   WHERE id = %s""",
                (now, days, amount_cents, str(session_id)),
            )
            payment = {
                "payment_reference": payment_reference,
                "amount_cents": amount_cents,
                "billable_days": days,
                "status": "PENDING",
            }

    return {
        "payment_reference": str(payment["payment_reference"]),
        "payment_status": payment["status"],
        "billable_days": payment["billable_days"],
        "amount_cents": payment["amount_cents"],
        "amount": f"SGD {payment['amount_cents'] / 100:.2f}",
        "rate": "SGD 10.00 per 24 hours; each started 24-hour block is charged",
        "payment_mode": "DEMO_ONLY",
        "demo_pay_endpoint": f"/api/luggage/payments/{payment['payment_reference']}/demo-pay",
        "message": "No real payment is collected. Press the demo payment button in the app to simulate payment.",
    }


@router.post("/sessions/{session_id}/checkout", tags=["payments"])
def checkout_from_app(session_id: UUID, user: dict = Depends(_current_user)):
    """Create a demo checkout for a session owned by this user."""
    return _create_demo_checkout(session_id, user["id"])


@router.post("/payments/{payment_reference}/demo-pay", tags=["payments", "development"])
def demo_confirm_payment(payment_reference: UUID, user: dict = Depends(_current_user)):
    """Simulate a successful payment; this endpoint does not charge real money."""
    if not DEMO_MODE:
        raise HTTPException(status_code=404, detail="Not found")
    with _db_connection() as conn, conn.cursor() as cur:
        _acquire_locker_lock(cur)
        cur.execute(
            """SELECT p.id, p.payment_reference, p.status AS payment_status,
                      p.amount_cents, p.billable_days, ls.id AS session_id, ls.status AS session_status
               FROM luggage_payments p
               JOIN luggage_sessions ls ON ls.id = p.luggage_session_id
               WHERE p.payment_reference = %s AND ls.user_id = %s
               FOR UPDATE OF p, ls""",
            (str(payment_reference), str(user["id"])),
        )
        payment = cur.fetchone()
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment reference not found")
        if payment["payment_status"] == "PAID":
            return {"status": "PAID", "duplicate": True, "simulated": True}
        if payment["payment_status"] != "PENDING" or payment["session_status"] != "PAYMENT_PENDING":
            raise HTTPException(status_code=409, detail="Payment is no longer pending")
        cur.execute(
            """UPDATE luggage_payments SET status = 'PAID', paid_at = NOW()
               WHERE payment_reference = %s""",
            (str(payment_reference),),
        )
        cur.execute(
            """UPDATE luggage_sessions SET status = 'PAID_AWAITING_PICKUP', updated_at = NOW()
               WHERE id = %s AND user_id = %s AND status = 'PAYMENT_PENDING'""",
            (str(payment["session_id"]), str(user["id"])),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="Luggage session could not be marked as paid")
    return {
        "status": "PAID",
        "simulated": True,
        "amount_cents": payment["amount_cents"],
        "message": "Demo payment successful. You can now unlock your locker in the app.",
    }


@router.post("/sessions/{session_id}/unlock", tags=["luggage"])
def unlock_my_luggage(session_id: UUID, user: dict = Depends(_current_user)):
    """Record a virtual unlock only for this user's paid luggage compartment."""
    with _db_connection() as conn, conn.cursor() as cur:
        _acquire_locker_lock(cur)
        cur.execute(
            """SELECT ls.id, ls.compartment_id, ls.status, c.reserved_by
               FROM luggage_sessions ls
               JOIN locker_compartments c ON c.id = ls.compartment_id
               WHERE ls.id = %s AND ls.user_id = %s
               FOR UPDATE OF ls, c""",
            (str(session_id), str(user["id"])),
        )
        session = cur.fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="Luggage session not found")
        if session["status"] != "PAID_AWAITING_PICKUP":
            raise HTTPException(status_code=409, detail="Complete demo payment before unlocking")
        if str(session["reserved_by"]) != str(session_id):
            raise HTTPException(status_code=409, detail="This locker is no longer assigned to your luggage session")
        event = _record_virtual_unlock(
            cur, user["id"], session_id, session["compartment_id"], "RETRIEVE"
        )
    return {
        "unlocked": True,
        "simulated": True,
        "event_id": str(event["id"]),
        "session_id": str(session_id),
        "message": "Virtual locker unlocked. Collect your luggage, then confirm collection in the app.",
    }


@router.post("/sessions/{session_id}/complete", tags=["luggage"])
def complete_luggage_retrieval(session_id: UUID, user: dict = Depends(_current_user)):
    """Release the XL compartment after the owner confirms collection in the app."""
    with _db_connection() as conn, conn.cursor() as cur:
        _acquire_locker_lock(cur)
        cur.execute(
            """SELECT ls.id, ls.compartment_id, ls.status, c.station_id,
                      EXISTS (
                          SELECT 1 FROM luggage_unlock_events e
                          WHERE e.luggage_session_id = ls.id AND e.action = 'RETRIEVE'
                      ) AS retrieve_unlock_recorded
               FROM luggage_sessions ls
               JOIN locker_compartments c ON c.id = ls.compartment_id
               WHERE ls.id = %s AND ls.user_id = %s
               FOR UPDATE OF ls, c""",
            (str(session_id), str(user["id"])),
        )
        session = cur.fetchone()
        if session is None:
            raise HTTPException(status_code=404, detail="Luggage session not found")
        if session["status"] != "PAID_AWAITING_PICKUP":
            raise HTTPException(status_code=409, detail="Only paid luggage sessions can be completed")
        if not session["retrieve_unlock_recorded"]:
            raise HTTPException(status_code=409, detail="Use the app unlock action before confirming collection")
        cur.execute(
            """UPDATE locker_compartments SET reserved_by = NULL
               WHERE id = %s AND reserved_by = %s""",
            (session["compartment_id"], str(session_id)),
        )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="Locker ownership changed; contact support")
        cur.execute(
            """UPDATE luggage_sessions
               SET status = 'RETRIEVED', retrieved_at = NOW(), updated_at = NOW()
               WHERE id = %s
               RETURNING id, status, retrieved_at""",
            (str(session_id),),
        )
        result = cur.fetchone()
        # The released XL compartment may now satisfy a waiting parcel reservation.
        cur.execute(
            """UPDATE locker_reservations
               SET last_attempt_at = NULL, queue_updated_at = clock_timestamp()
               WHERE status = 'CONDITIONAL' AND station_id = %s""",
            (session["station_id"],),
        )
    return result
