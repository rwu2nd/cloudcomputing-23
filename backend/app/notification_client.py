import os
import sys

import grpc
from google.protobuf.json_format import MessageToDict

_here = os.path.dirname(os.path.abspath(__file__))
for _d in (os.getenv("PROTO_DIR"), os.path.join(_here, "proto"), "/app/proto"):
    if _d and os.path.isfile(os.path.join(_d, "notification.proto")):
        sys.path.insert(0, _d)
        break
pb2 = grpc.protos("notification.proto")
pb2_grpc = grpc.services("notification.proto")

_channel = grpc.insecure_channel(os.getenv("NOTIFICATION_SERVICE_URL", "notification-service:50051"))
_stub = pb2_grpc.NotificationServiceStub(_channel)


def _dict(msg):
    return MessageToDict(msg, preserving_proto_field_name=True, always_print_fields_with_no_presence=True)


def send_notification(user_id, title, message="", type="INFO"):
    return _dict(_stub.SendNotification(pb2.SendNotificationRequest(
        user_id=user_id, title=title, message=message,
        type=pb2.NotificationType.Value(type))))  # raises ValueError on a bad type name


def list_notifications(user_id, unread_only=False, limit=20):
    return _dict(_stub.ListNotifications(pb2.ListNotificationsRequest(
        user_id=user_id, unread_only=unread_only, limit=limit)))


def mark_as_read(notification_id):
    return _dict(_stub.MarkAsRead(pb2.MarkAsReadRequest(id=str(notification_id))))