import os
import queue
import sys
import threading
from concurrent import futures

import grpc

import db

# Load the .proto at runtime (no generated stubs to maintain)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "proto"))
pb2 = grpc.protos("notification.proto")
pb2_grpc = grpc.services("notification.proto")

PORT = os.getenv("GRPC_PORT", "50051")

# --- tiny in-process pub/sub for the streaming RPC ---
_subs = {}
_subs_lock = threading.Lock()


def _subscribe(user_id):
    q = queue.Queue()
    with _subs_lock:
        _subs.setdefault(user_id, set()).add(q)
    return q


def _unsubscribe(user_id, q):
    with _subs_lock:
        _subs.get(user_id, set()).discard(q)


def _publish(user_id, msg):
    with _subs_lock:
        for q in list(_subs.get(user_id, ())):
            q.put(msg)


def to_message(row):
    return pb2.Notification(
        id=str(row["id"]),
        user_id=row["user_id"],
        title=row["title"],
        message=row["message"],
        type=pb2.NotificationType.Value(row["type"]),
        is_read=row["is_read"],
        created_at=row["created_at"].isoformat(),
    )


class NotificationService(pb2_grpc.NotificationServiceServicer):
    def SendNotification(self, request, context):
        user_id = request.user_id.strip()
        title = request.title.strip()

        if not user_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_id is required")
        if not title:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "title is required")
        if len(title) > 200:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "title must be <= 200 chars")
        try:
            type_name = pb2.NotificationType.Name(request.type)
        except ValueError:
            valid = ", ".join(pb2.NotificationType.keys())
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"type must be one of {valid}")

        try:
            with db.query() as cur:
                cur.execute(
                    "INSERT INTO notifications (user_id, title, message, type) "
                    "VALUES (%s, %s, %s, %s) RETURNING *",
                    (user_id, title, request.message, type_name),
                )
                row = cur.fetchone()
        except Exception as e:  # noqa: BLE001
            print(f"[SendNotification] error: {e}", flush=True)
            context.abort(grpc.StatusCode.INTERNAL, "failed to store notification")

        msg = to_message(row)
        print(f"[SendNotification] id={msg.id} user={user_id} type={type_name}", flush=True)
        _publish(user_id, msg)
        return pb2.SendNotificationResponse(success=True, notification=msg)

    def ListNotifications(self, request, context):
        if not request.user_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_id is required")
        limit = min(max(request.limit or 20, 1), 100)

        with db.query() as cur:
            cur.execute(
                "SELECT * FROM notifications "
                "WHERE user_id = %s AND (%s = FALSE OR is_read = FALSE) "
                "ORDER BY created_at DESC LIMIT %s",
                (request.user_id, request.unread_only, limit),
            )
            rows = cur.fetchall()
        return pb2.ListNotificationsResponse(
            notifications=[to_message(r) for r in rows], total=len(rows)
        )

    def MarkAsRead(self, request, context):
        if not request.id.isdigit():
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "id must be a numeric string")
        with db.query() as cur:
            cur.execute("UPDATE notifications SET is_read = TRUE WHERE id = %s", (request.id,))
            updated = cur.rowcount
        if updated == 0:
            context.abort(grpc.StatusCode.NOT_FOUND, f"notification {request.id} not found")
        print(f"[MarkAsRead] id={request.id}", flush=True)
        return pb2.MarkAsReadResponse(success=True)

    def StreamNotifications(self, request, context):
        if not request.user_id:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "user_id is required")
        q = _subscribe(request.user_id)
        print(f"[StreamNotifications] subscribed user={request.user_id}", flush=True)
        try:
            while context.is_active():
                try:
                    yield q.get(timeout=1)
                except queue.Empty:
                    continue
        finally:
            _unsubscribe(request.user_id, q)
            print(f"[StreamNotifications] unsubscribed user={request.user_id}", flush=True)


def serve():
    db.init()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=20))
    pb2_grpc.add_NotificationServiceServicer_to_server(NotificationService(), server)
    server.add_insecure_port(f"0.0.0.0:{PORT}")
    server.start()
    print(f"[grpc] NotificationService listening on :{PORT}", flush=True)
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
