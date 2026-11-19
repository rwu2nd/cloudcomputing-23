"""Prometheus metrics for the backend, served at GET /metrics.

/metrics is deliberately outside /api/, so the frontend's nginx never exposes it
publicly; Prometheus scrapes each backend pod directly.

Three kinds of metrics:
  * HTTP traffic per route (rate, latency, errors, in-flight requests).
  * Process metrics from prometheus_client (CPU seconds, memory) per pod,
    which show the load that drives the HorizontalPodAutoscaler.
  * Business state read from PostgreSQL at scrape time (reservations by
    status, compartments in use, luggage sessions). Every replica reports the
    same numbers, so dashboards use max() across pods.
"""

from __future__ import annotations

import logging
import time

import psycopg2
from fastapi import FastAPI, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Gauge, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily

from app.config import get_settings

logger = logging.getLogger(__name__)

REQUESTS = Counter(
    "smartlocker_http_requests_total",
    "HTTP requests handled by this backend pod",
    ["method", "route", "status"],
)
LATENCY = Histogram(
    "smartlocker_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
IN_FLIGHT = Gauge("smartlocker_http_requests_in_flight", "Requests currently being handled")


class BusinessCollector:
    """Reads current locker state from PostgreSQL whenever Prometheus scrapes."""

    def collect(self):
        up = GaugeMetricFamily("smartlocker_database_up", "1 if this pod could query PostgreSQL during the scrape")
        reservations = GaugeMetricFamily("smartlocker_reservations", "Parcel reservations by status", labels=["status"])
        compartments = GaugeMetricFamily("smartlocker_compartments", "Active compartments by size and state", labels=["size", "state"])
        luggage = GaugeMetricFamily("smartlocker_luggage_sessions", "Luggage sessions by status", labels=["status"])
        s = get_settings()
        conn = None
        try:
            conn = psycopg2.connect(
                host=s.postgres_host, port=s.postgres_port, dbname=s.postgres_db,
                user=s.postgres_user, password=s.postgres_password,
                connect_timeout=2, application_name="smart-locker-metrics",
            )
            with conn.cursor() as cur:
                # SET LOCAL (not a connection option) so this also works
                # through PgBouncer in transaction-pooling mode.
                cur.execute("SET LOCAL statement_timeout = 2000")
                cur.execute("SELECT status, count(*) FROM locker_reservations GROUP BY status")
                for status, count in cur.fetchall():
                    reservations.add_metric([status], count)
                cur.execute(
                    """SELECT size, CASE WHEN reserved_by IS NULL THEN 'free' ELSE 'occupied' END, count(*)
                       FROM locker_compartments WHERE active GROUP BY 1, 2"""
                )
                for size, state, count in cur.fetchall():
                    compartments.add_metric([size, state], count)
                cur.execute("SELECT status, count(*) FROM luggage_sessions GROUP BY status")
                for status, count in cur.fetchall():
                    luggage.add_metric([status], count)
            up.add_metric([], 1)
        except psycopg2.Error as exc:
            logger.debug("Business metrics unavailable: %s", exc)
            up.add_metric([], 0)
        finally:
            if conn is not None:
                conn.close()
        yield up
        yield reservations
        yield compartments
        yield luggage


def setup_metrics(app: FastAPI) -> None:
    REGISTRY.register(BusinessCollector())

    @app.middleware("http")
    async def record_request(request: Request, call_next):
        if request.url.path == "/metrics":
            return await call_next(request)
        IN_FLIGHT.inc()
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            IN_FLIGHT.dec()
            # Label by route template (/api/reservations/{reservation_id}), not
            # the raw path, so ids do not create unlimited label values.
            route = request.scope.get("route")
            route_path = getattr(route, "path", None) or "unmatched"
            REQUESTS.labels(request.method, route_path, str(status)).inc()
            LATENCY.labels(request.method, route_path).observe(time.perf_counter() - started)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
