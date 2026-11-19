"""HTTP bridge: browser <-> FastAPI <-> gRPC notification-service.

Mount with:  app.include_router(notifications_api.router)

Unary calls (send / list / mark-read / reminder delivery) go through
notification_client (sync gRPC, run in a worker thread so the event loop
isn't blocked). Only the server-streaming SSE endpoint talks to gRPC directly,
because the client module doesn't wrap StreamNotifications.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Literal

import grpc
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import notification_client  # adjust the import path if yours differs
from app.luggage import _current_user

# Reuse the proto modules already loaded by notification_client, so the proto
# is only loaded (and its path only configured) in one place.
pb2 = notification_client.pb2
pb2_grpc = notification_client.pb2_grpc

# Same env var the client uses, so both always point at the same service.
GRPC_TARGET = os.getenv("NOTIFICATION_SERVICE_URL", "notification-service:50051")
MAX_REMINDER_MINUTES = 7 * 24 * 60

log = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["notifications"])

_channel = None


def _stub():
    """Async stub, used only for the streaming RPC."""
    global _channel
    if _channel is None:
        _channel = grpc.aio.insecure_channel(GRPC_TARGET)
    return pb2_grpc.NotificationServiceStub(_channel)


async def current_user(account=Depends(_current_user)):
    # your dependency returns a dict; expose it with attribute access
    return SimpleNamespace(**account)


def _to_dict(n):
    return {
        "id": n.id,
        "user_id": n.user_id,
        "title": n.title,
        "message": n.message,
        "type": pb2.NotificationType.Name(n.type),
        "is_read": n.is_read,
        "created_at": n.created_at,
    }


def _http_error(e: grpc.RpcError):
    code = {
        grpc.StatusCode.INVALID_ARGUMENT: 400,
        grpc.StatusCode.NOT_FOUND: 404,
        grpc.StatusCode.UNAVAILABLE: 503,
    }.get(e.code(), 502)
    return HTTPException(code, e.details() or "notification service error")


# --------------------------------------------------------------------------
# Send / list / mark read
# --------------------------------------------------------------------------
class SendBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    message: str = ""
    type: Literal["INFO", "SUCCESS", "WARNING", "ERROR"] = "INFO"


@router.post("")
async def send(body: SendBody, user=Depends(current_user)):
    # user_id always comes from the token, never from the client.
    try:
        return await asyncio.to_thread(
            notification_client.send_notification,
            user.username, body.title, body.message, body.type,
        )
    except grpc.RpcError as e:
        raise _http_error(e)


@router.get("")
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=100),
    user=Depends(current_user),
):
    try:
        return await asyncio.to_thread(
            notification_client.list_notifications, user.username, unread_only, limit
        )
    except grpc.RpcError as e:
        raise _http_error(e)


@router.post("/{notification_id}/read")
async def mark_read(notification_id: str, user=Depends(current_user)):
    # MarkAsRead has no user_id, so verify ownership before calling it.
    try:
        owned = await asyncio.to_thread(
            notification_client.list_notifications, user.username, False, 100
        )
        if notification_id not in {n["id"] for n in owned.get("notifications", [])}:
            raise HTTPException(404, "notification not found")
        return await asyncio.to_thread(notification_client.mark_as_read, notification_id)
    except grpc.RpcError as e:
        raise _http_error(e)


# --------------------------------------------------------------------------
# Luggage reminders: "remind me in X minutes/hours"
#
# The delay is held server-side, so the reminder fires even if the browser tab
# is closed; when it fires it goes out through the notification service and
# reaches the page over the SSE stream below (or shows up in the unread list
# next time the page loads).
#
# NOTE: pending reminders live in this process's memory, so they are lost if
# the backend restarts and are not shared between multiple backend workers.
# For anything stronger, persist them in your DB and run a scheduler.
# --------------------------------------------------------------------------
_reminders: dict[tuple[str, str], tuple[datetime, asyncio.Task]] = {}


class ReminderBody(BaseModel):
    session_id: int | str  # accepts numeric ids or string/UUID ids
    minutes: int = Field(ge=1, le=MAX_REMINDER_MINUTES)
    label: str = Field(default="", max_length=100)


async def _fire_reminder(key: tuple[str, str], delay_seconds: float, label: str):
    try:
        await asyncio.sleep(delay_seconds)
        where = f" ({label})" if label else ""
        await asyncio.to_thread(
            notification_client.send_notification,
            key[0],
            "Luggage reminder",
            f"Time to retrieve your luggage{where}.",
            "INFO",
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("failed to deliver luggage reminder for %s", key)
    finally:
        entry = _reminders.get(key)
        if entry and entry[1] is asyncio.current_task():
            _reminders.pop(key, None)


@router.post("/reminders")
async def set_reminder(body: ReminderBody, user=Depends(current_user)):
    key = (user.username, str(body.session_id))
    old = _reminders.pop(key, None)
    if old:
        old[1].cancel()  # one reminder per session; the new one replaces it

    due_at = datetime.now(timezone.utc) + timedelta(minutes=body.minutes)
    task = asyncio.create_task(_fire_reminder(key, body.minutes * 60, body.label))
    _reminders[key] = (due_at, task)
    return {"session_id": body.session_id, "due_at": due_at.isoformat()}


@router.get("/reminders")
async def list_reminders(user=Depends(current_user)):
    return {
        "reminders": [
            {"session_id": sid, "due_at": due.isoformat()}
            for (username, sid), (due, _task) in _reminders.items()
            if username == user.username
        ]
    }


@router.delete("/reminders/{session_id}")
async def cancel_reminder(session_id: str, user=Depends(current_user)):
    entry = _reminders.pop((user.username, session_id), None)
    if entry:
        entry[1].cancel()
    return {"success": entry is not None}


# --------------------------------------------------------------------------
# Live stream
# --------------------------------------------------------------------------
@router.get("/stream")
async def stream(user=Depends(current_user)):
    """Server-sent events fed by the StreamNotifications server-streaming RPC."""

    async def events():
        call = _stub().StreamNotifications(
            pb2.StreamNotificationsRequest(user_id=user.username)
        )
        queue: asyncio.Queue = asyncio.Queue()

        async def pump():
            try:
                async for n in call:
                    await queue.put(f"data: {json.dumps(_to_dict(n))}\n\n")
            except (grpc.aio.AioRpcError, asyncio.CancelledError):
                pass
            finally:
                await queue.put(None)

        task = asyncio.create_task(pump())
        try:
            yield ": connected\n\n"
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # keep proxies from idling out
                    continue
                if frame is None:
                    break
                yield frame
        finally:
            call.cancel()
            task.cancel()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
