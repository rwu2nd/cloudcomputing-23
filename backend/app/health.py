"""Health endpoints used by Docker Compose and Kubernetes probes.

PLACEHOLDER - backend teammate: these work today so the infrastructure can be
verified, but you own the real behaviour. Keep the two URLs (the Kubernetes
manifests point at them) and keep their meaning:

  /health/live   Is the process running? Must NOT depend on the database,
                 otherwise a database outage makes Kubernetes restart every
                 backend pod for no benefit.
  /health/ready  Can this instance serve traffic right now? Return HTTP 503 when
                 it cannot (e.g. database down). Kubernetes then stops routing
                 requests to the pod without restarting it.
"""

import socket

from fastapi import APIRouter, Response, status

from app.config import get_settings

router = APIRouter(prefix="/health", tags=["health"])


def check_database_reachable(timeout: float = 2.0) -> bool:
    """PLACEHOLDER check: only tests that a TCP connection to PostgreSQL opens.

    TODO (backend teammate): replace with a real check once the database layer
    exists, e.g. run `SELECT 1` through your connection pool.
    """
    settings = get_settings()
    try:
        with socket.create_connection(
            (settings.postgres_host, settings.postgres_port), timeout=timeout
        ):
            return True
    except OSError:
        return False


@router.get("/live")
def liveness():
    """Liveness probe: the process is up. Add nothing slow or external here."""
    return {"status": "alive"}


@router.get("/ready")
def readiness(response: Response):
    """Readiness probe: ready only when the database is reachable."""
    if check_database_reachable():
        return {"status": "ready", "database": "reachable"}
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "not ready", "database": "unreachable"}
