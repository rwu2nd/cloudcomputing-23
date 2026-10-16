# Backend (FastAPI) - owned by the backend teammate

The code here is a **placeholder** so the containers can start. Replace it.

| Keep this | Why |
|---|---|
| ASGI entrypoint `app.main:app` | Dockerfile, Compose and Kubernetes start `uvicorn app.main:app` |
| `GET /health/live` and `GET /health/ready` | Compose and Kubernetes probes call them (see `app/health.py` for what each must mean) |
| Business routes under `/api/...` | The frontend's nginx only forwards `/api/*` to this service |
| Reading settings from environment variables | See `app/config.py`: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `APP_ENV` |

To do (yours): real `/health/ready` database check, API endpoints, database access
layer, delete the placeholder `/api/ping`.

Adding a library: add it to `requirements.txt`, then rebuild (`docker compose up --build`).
The Docker image copies this whole folder, so extra folders (e.g. `migrations/`) are included.

Run without Docker (database still in Docker): see "Working on one component" in the root README.
