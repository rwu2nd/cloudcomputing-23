"""Locker controller: a gRPC service that simulates the compartment door hardware.

The backend calls UnlockCompartment whenever a door must open. Everything about
the "hardware" is simulated, but the service behaves like a real dependency:

  * it takes time to answer (ACTUATION_MS, with some jitter),
  * it can fail on purpose (FAILURE_RATE), returning UNAVAILABLE so the
    backend's gRPC retry policy kicks in,
  * it speaks the standard gRPC health protocol, which Kubernetes probes use,
  * it shuts down gracefully, so rolling updates do not drop calls,
  * it exposes Prometheus metrics on METRICS_PORT.

It is stateless apart from a short-lived cache of handled request IDs, so any
number of replicas can run behind the same address.

Environment variables (all optional):
    GRPC_PORT        default 50051
    METRICS_PORT     default 9100
    ACTUATION_MS     default 80     simulated door opening time
    FAILURE_RATE     default 0      0.0-1.0, share of calls that fail on purpose
    CONTROLLER_ID    default $HOSTNAME (the pod name in Kubernetes)
"""

from __future__ import annotations

import logging
import os
import random
import signal
import threading
import time
from collections import OrderedDict
from concurrent import futures

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from smartlocker.v1 import locker_controller_pb2 as pb
from smartlocker.v1 import locker_controller_pb2_grpc as pb_grpc

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("locker-controller")

SERVICE_NAME = pb.DESCRIPTOR.services_by_name["LockerController"].full_name
GRPC_PORT = int(os.getenv("GRPC_PORT", "50051"))
METRICS_PORT = int(os.getenv("METRICS_PORT", "9100"))
ACTUATION_MS = int(os.getenv("ACTUATION_MS", "80"))
FAILURE_RATE = min(1.0, max(0.0, float(os.getenv("FAILURE_RATE", "0"))))
CONTROLLER_ID = os.getenv("CONTROLLER_ID") or os.getenv("HOSTNAME") or "locker-controller"

# Remember handled request IDs for a while so retries are recognised.
DEDUP_TTL_SECONDS = 600
DEDUP_MAX_ENTRIES = 10_000

UNLOCKS = Counter(
    "locker_controller_unlocks_total",
    "Unlock calls handled by this replica",
    ["reason", "outcome"],
)
UNLOCK_SECONDS = Histogram(
    "locker_controller_unlock_seconds",
    "Time spent handling UnlockCompartment",
    buckets=(0.025, 0.05, 0.1, 0.15, 0.25, 0.5, 1.0, 2.5),
)
FAILURE_RATE_GAUGE = Gauge(
    "locker_controller_failure_rate",
    "Configured share of calls that fail on purpose",
)


def _reason_name(reason: int) -> str:
    return pb.UnlockReason.Name(reason).removeprefix("UNLOCK_REASON_").lower()


class LockerControllerService(pb_grpc.LockerControllerServicer):
    def __init__(self) -> None:
        self.started_at_ms = int(time.time() * 1000)
        self._handled: OrderedDict[str, tuple[float, pb.UnlockReply]] = OrderedDict()
        self._lock = threading.Lock()
        self._unlocks_handled = 0

    def _cached_reply(self, request_id: str):
        now = time.monotonic()
        with self._lock:
            # Drop expired entries from the oldest end.
            while self._handled:
                oldest_id, (stored_at, _) = next(iter(self._handled.items()))
                if now - stored_at < DEDUP_TTL_SECONDS:
                    break
                self._handled.popitem(last=False)
            entry = self._handled.get(request_id)
            return entry[1] if entry else None

    def _remember(self, request_id: str, reply: pb.UnlockReply) -> None:
        with self._lock:
            self._handled[request_id] = (time.monotonic(), reply)
            self._handled.move_to_end(request_id)
            while len(self._handled) > DEDUP_MAX_ENTRIES:
                self._handled.popitem(last=False)
            self._unlocks_handled += 1

    def UnlockCompartment(self, request, context):
        reason = _reason_name(request.reason)
        with UNLOCK_SECONDS.time():
            if not request.request_id or request.compartment_id <= 0 or request.reason == pb.UNLOCK_REASON_UNSPECIFIED:
                UNLOCKS.labels(reason, "invalid").inc()
                context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                              "request_id, compartment_id and reason are required")

            cached = self._cached_reply(request.request_id)
            if cached is not None:
                UNLOCKS.labels(reason, "duplicate").inc()
                reply = pb.UnlockReply()
                reply.CopyFrom(cached)
                reply.duplicate = True
                return reply

            if FAILURE_RATE and random.random() < FAILURE_RATE:
                UNLOCKS.labels(reason, "injected_failure").inc()
                log.warning("Injected failure for %s (compartment %s)", request.request_id, request.compartment_id)
                # UNAVAILABLE is the "transient, safe to retry" status code.
                context.abort(grpc.StatusCode.UNAVAILABLE, "door actuator did not respond (simulated fault)")

            actuation_ms = max(1, int(random.gauss(ACTUATION_MS, ACTUATION_MS * 0.2)))
            time.sleep(actuation_ms / 1000)
            reply = pb.UnlockReply(
                opened=True,
                controller_id=CONTROLLER_ID,
                opened_at_unix_ms=int(time.time() * 1000),
                duplicate=False,
                actuation_ms=actuation_ms,
            )
            self._remember(request.request_id, reply)
            UNLOCKS.labels(reason, "opened").inc()
            log.info("Opened compartment %s (%s) at station %s for %s [%s]",
                     request.compartment_id, request.compartment_label or "?",
                     request.station_id, reason, request.request_id)
            return reply

    def GetControllerInfo(self, request, context):
        with self._lock:
            handled = self._unlocks_handled
        return pb.ControllerInfo(
            controller_id=CONTROLLER_ID,
            started_at_unix_ms=self.started_at_ms,
            unlocks_handled=handled,
            failure_rate=FAILURE_RATE,
            actuation_ms=ACTUATION_MS,
        )


def serve() -> None:
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=32),
        options=[
            # Make clients reconnect (and re-resolve DNS) every ~30s. Without
            # this, long-lived HTTP/2 connections would keep sending all calls
            # to the replicas that existed when the client first connected,
            # and newly scaled-out pods would get no traffic.
            ("grpc.max_connection_age_ms", 30_000),
            ("grpc.max_connection_age_grace_ms", 5_000),
            ("grpc.keepalive_permit_without_calls", 1),
            ("grpc.http2.min_ping_interval_without_data_ms", 10_000),
        ],
    )
    pb_grpc.add_LockerControllerServicer_to_server(LockerControllerService(), server)

    health_servicer = health.HealthServicer(experimental_non_blocking=True)
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)

    # Reflection lets tools such as grpcurl list and call the service without the .proto file.
    reflection.enable_server_reflection(
        (SERVICE_NAME, health_pb2.DESCRIPTOR.services_by_name["Health"].full_name, reflection.SERVICE_NAME),
        server,
    )

    server.add_insecure_port(f"[::]:{GRPC_PORT}")
    start_http_server(METRICS_PORT)
    FAILURE_RATE_GAUGE.set(FAILURE_RATE)
    server.start()
    for name in ("", SERVICE_NAME):
        health_servicer.set(name, health_pb2.HealthCheckResponse.SERVING)
    log.info("Locker controller %s listening on :%s (metrics :%s, actuation %sms, failure rate %.0f%%)",
             CONTROLLER_ID, GRPC_PORT, METRICS_PORT, ACTUATION_MS, FAILURE_RATE * 100)

    stopping = threading.Event()

    def _shutdown(signum, _frame):
        if stopping.is_set():
            return
        stopping.set()
        log.info("Received signal %s, draining", signum)
        # Report NOT_SERVING first so new calls go to other replicas, then let
        # in-flight calls finish before the process exits.
        health_servicer.enter_graceful_shutdown()
        server.stop(grace=10)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    server.wait_for_termination()
    log.info("Stopped")


if __name__ == "__main__":
    serve()
