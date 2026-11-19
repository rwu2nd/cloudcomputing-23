"""gRPC client for the locker-controller service (the simulated door hardware).

The browser talks REST to this backend; this module is the backend's internal
gRPC call to another service. It is called *outside* the database allocation
lock, so a slow or broken controller never blocks bookings at other lockers.

How the call is made resilient:
  * Deadline: every unlock must finish within LOCKER_CONTROLLER_TIMEOUT_SECONDS
    (retries included), otherwise the API answers 503 instead of hanging.
  * Retries: UNAVAILABLE (pod restarting, simulated fault) is retried by gRPC
    itself with exponential backoff, configured in the service config below.
  * Idempotency: one request_id per user action, reused by every retry, so
    the controller can tell a retry from a second door opening.
  * Load balancing: in Kubernetes the address is a headless Service, so DNS
    returns every controller pod and the round_robin policy spreads calls
    across them (plain ClusterIP + HTTP/2 would pin all calls to one pod).

LOCKER_CONTROLLER_ADDR unset (e.g. running uvicorn on a laptop without the
controller) falls back to an in-process simulation and logs a warning once.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from uuid import uuid4

import grpc
from fastapi import HTTPException
from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)

ADDRESS = os.getenv("LOCKER_CONTROLLER_ADDR", "").strip()
TIMEOUT_SECONDS = float(os.getenv("LOCKER_CONTROLLER_TIMEOUT_SECONDS", "3"))

REASONS = {
    "PARCEL_COLLECT": "UNLOCK_REASON_PARCEL_COLLECT",
    "LUGGAGE_STORE": "UNLOCK_REASON_LUGGAGE_STORE",
    "LUGGAGE_RETRIEVE": "UNLOCK_REASON_LUGGAGE_RETRIEVE",
}

_SERVICE_CONFIG = json.dumps({
    "loadBalancingConfig": [{"round_robin": {}}],
    # Client-side health checking: the channel watches every controller pod
    # over grpc.health.v1 and stops sending calls to a pod as soon as it
    # reports NOT_SERVING (which it does the moment it starts shutting down).
    "healthCheckConfig": {"serviceName": "smartlocker.v1.LockerController"},
    "methodConfig": [{
        "name": [{"service": "smartlocker.v1.LockerController"}],
        "retryPolicy": {
            "maxAttempts": 4,
            "initialBackoff": "0.1s",
            "maxBackoff": "1s",
            "backoffMultiplier": 2,
            "retryableStatusCodes": ["UNAVAILABLE"],
        },
    }],
})

CALLS = Counter(
    "smartlocker_controller_calls_total",
    "gRPC unlock calls from the backend to the locker-controller",
    ["reason", "outcome"],
)
CALL_SECONDS = Histogram(
    "smartlocker_controller_call_seconds",
    "Latency of gRPC unlock calls, retries included",
    ["reason"],
    buckets=(0.05, 0.1, 0.15, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0),
)

_stub = None
_pb = None
_stub_lock = threading.Lock()
_warned = False


def _get_stub():
    global _stub, _pb
    if _stub is None:
        with _stub_lock:
            if _stub is None:
                # Generated from proto/smartlocker/v1/locker_controller.proto at build time.
                from smartlocker.v1 import locker_controller_pb2 as pb
                from smartlocker.v1 import locker_controller_pb2_grpc as pb_grpc

                channel = grpc.insecure_channel(
                    ADDRESS,
                    options=[
                        ("grpc.service_config", _SERVICE_CONFIG),
                        ("grpc.enable_retries", 1),
                        # Re-resolve DNS at most every second (default 30s).
                        # During a rolling update the old pods disappear and
                        # the client must find the new ones quickly.
                        ("grpc.dns_min_time_between_resolutions_ms", 1_000),
                        ("grpc.keepalive_time_ms", 30_000),
                        ("grpc.keepalive_timeout_ms", 5_000),
                    ],
                )
                _pb = pb
                _stub = pb_grpc.LockerControllerStub(channel)
    return _stub, _pb


def unlock(reason: str, station_id: int, compartment_id: int, compartment_label: str = "") -> dict:
    """Ask the controller to open a door. Raises HTTPException(503) if it cannot."""
    global _warned
    request_id = str(uuid4())
    if not ADDRESS:
        if not _warned:
            logger.warning("LOCKER_CONTROLLER_ADDR is not set; door unlocks are simulated in-process")
            _warned = True
        CALLS.labels(reason.lower(), "in_process").inc()
        return {
            "opened": True,
            "controller": "in-process (LOCKER_CONTROLLER_ADDR not set)",
            "request_id": request_id,
            "via": "in-process",
        }

    stub, pb = _get_stub()
    request = pb.UnlockRequest(
        request_id=request_id,
        station_id=int(station_id),
        compartment_id=int(compartment_id),
        compartment_label=compartment_label or "",
        reason=pb.UnlockReason.Value(REASONS[reason]),
    )
    started = time.perf_counter()
    try:
        # wait_for_ready: if the controller is reconnecting (pod restart,
        # rolling update) wait for it, but never longer than the deadline.
        reply = stub.UnlockCompartment(request, timeout=TIMEOUT_SECONDS, wait_for_ready=True)
    except grpc.RpcError as exc:
        code = exc.code()
        CALLS.labels(reason.lower(), code.name.lower()).inc()
        CALL_SECONDS.labels(reason.lower()).observe(time.perf_counter() - started)
        logger.warning("Locker controller unlock failed for compartment %s: %s %s",
                       compartment_id, code.name, exc.details())
        if code in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
            raise HTTPException(
                status_code=503,
                detail="The locker controller is not responding, so the door was not opened. Please try again.",
            )
        raise HTTPException(status_code=502, detail=f"Locker controller error: {code.name}")

    CALLS.labels(reason.lower(), "opened").inc()
    CALL_SECONDS.labels(reason.lower()).observe(time.perf_counter() - started)
    return {
        "opened": reply.opened,
        "controller": reply.controller_id,
        "request_id": request_id,
        "actuation_ms": reply.actuation_ms,
        "via": "grpc",
    }


def controller_info(samples: int = 6) -> list[dict]:
    """Call GetControllerInfo a few times to show which replicas answer."""
    if not ADDRESS:
        return []
    stub, pb = _get_stub()
    replies = []
    for _ in range(samples):
        try:
            info = stub.GetControllerInfo(pb.ControllerInfoRequest(), timeout=TIMEOUT_SECONDS, wait_for_ready=True)
        except grpc.RpcError as exc:
            replies.append({"error": exc.code().name})
            continue
        replies.append({
            "controller": info.controller_id,
            "unlocks_handled": info.unlocks_handled,
            "failure_rate": info.failure_rate,
            "actuation_ms": info.actuation_ms,
        })
    return replies
