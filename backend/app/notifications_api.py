"""HTTP bridge: browser <-> FastAPI <-> gRPC notification-service.

Mount with:  app.include_router(notifications_api.router)
Adapt `current_user` to whatever dependency your /luggage/auth/me uses.
If backend/notification-client already wraps the stub, swap it in for
`_stub()` below.
"""
import asyncio
import json
import os
import sys
from typing import Literal
from types import SimpleNamespace

import grpc
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from app.luggage import _current_user

# Same runtime proto loading as the server (proto dir must exist in the backend image).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "proto"))
pb2 = grpc.protos("notification.proto")
pb2_grpc = grpc.services("notification.proto")

GRPC_TARGET = os.getenv("NOTIFICATION_GRPC_TARGET", "notification-service:50051")

router = APIRouter(prefix="/notifications", tags=["notifications"])

_channel = None


def _stub():
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


def _http_error(e: grpc.aio.AioRpcError):
    code = {
        grpc.StatusCode.INVALID_ARGUMENT: 400,
        grpc.StatusCode.NOT_FOUND: 404,
        grpc.StatusCode.UNAVAILABLE: 503,
    }.get(e.code(), 502)
    return HTTPException(code, e.details() or "notification service error")


class SendBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    message: str = ""
    type: Literal["INFO", "SUCCESS", "WARNING", "ERROR"] = "INFO"


@router.post("")
async def send(body: SendBody, user=Depends(current_user)):
    # user_id always comes from the token, never from the client.
    try:
        res = await _stub().SendNotification(
            pb2.SendNotificationRequest(
                user_id=user.username,
                title=body.title,
                message=body.message,
                type=pb2.NotificationType.Value(body.type),
            ),
            timeout=5,
        )
    except grpc.aio.AioRpcError as e:
        raise _http_error(e)
    return {"success": res.success, "notification": _to_dict(res.notification)}


@router.get("")
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=100),
    user=Depends(current_user),
):
    try:
        res = await _stub().ListNotifications(
            pb2.ListNotificationsRequest(
                user_id=user.username, unread_only=unread_only, limit=limit
            ),
            timeout=5,
        )
    except grpc.aio.AioRpcError as e:
        raise _http_error(e)
    return {"notifications": [_to_dict(n) for n in res.notifications], "total": res.total}


@router.post("/{notification_id}/read")
async def mark_read(notification_id: str, user=Depends(current_user)):
    # MarkAsRead has no user_id, so verify ownership before calling it.
    try:
        owned = await _stub().ListNotifications(
            pb2.ListNotificationsRequest(user_id=user.username, limit=100), timeout=5
        )
        if notification_id not in {n.id for n in owned.notifications}:
            raise HTTPException(404, "notification not found")
        res = await _stub().MarkAsRead(pb2.MarkAsReadRequest(id=notification_id), timeout=5)
    except grpc.aio.AioRpcError as e:
        raise _http_error(e)
    return {"success": res.success}


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
