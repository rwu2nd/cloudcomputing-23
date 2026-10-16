"""Smart Locker backend - infrastructure placeholder.

PLACEHOLDER - backend teammate: this file only exists so the containers have
something to run. Replace or extend it freely. To keep the infrastructure
working, preserve three things:

  1. The ASGI entrypoint stays `app.main:app` (Dockerfile / Compose / Kubernetes use it).
  2. The /health/live and /health/ready routes stay available (see health.py).
  3. Business routes live under /api/... because the frontend's nginx only
     forwards /api/* to this service.
"""

from fastapi import FastAPI

from app.config import get_settings
from app.health import check_database_reachable
from app.health import router as health_router

app = FastAPI(title="Smart Locker API", version="0.0.0")

app.include_router(health_router)

# Add your routers here, for example:
#   from app.routers import lockers
#   app.include_router(lockers.router, prefix="/api")


@app.get("/api/ping", tags=["placeholder"])
def ping():
    """PLACEHOLDER used by the placeholder frontend page to prove the
    browser -> nginx -> backend -> database path works.

    TODO (backend teammate): delete this route once real endpoints exist
    (and tell the frontend teammate, whose placeholder page calls it).
    """
    settings = get_settings()
    return {
        "status": "ok",
        "service": "backend",
        "environment": settings.app_env,
        "database_reachable": check_database_reachable(),
    }
