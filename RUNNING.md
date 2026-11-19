# Running the Smart Locker System

How to get the project running locally, test it, and fix the usual problems. For what the cloud
setup demonstrates and a demo script, see [CLOUD.md](CLOUD.md).

## Status

Tested on a fresh database, everything below works:

- **Login and permissions:** admin login, user registration, and normal users are refused (403) on admin pages.
- **Parcel flow:** admin assigns a parcel, the user books a locker, the background worker allocates a compartment, then arrive, unlock and collected. Booking the same parcel twice is refused (409).
- **Luggage flow:** pick an XL slot, store, checkout, demo payment, unlock, complete.
- **Door unlocks over gRPC:** every unlock goes through the locker-controller service. With the controller down, unlocks return 503 and everything else keeps working.
- **Database restart:** with the database stopped, `/health/ready` returns 503, and it recovers about 2 seconds after the database comes back.
- **Tests:** `tests/test_init.py` (3) and `tests/test_end_to_end.py` (5) pass on both Compose and Kubernetes. `tests/test_recovery.py` only runs against Kubernetes.

## Before the first run

1. **Wipe any old database volume.** The schema changed (new tables `locker`, `user`, `luggage_*`, ...). The init scripts in `database/init/` only run on an empty volume, so if you ran the project before this change you have to reset it once. This deletes your local test data:
   ```bash
   docker compose down -v
   ```
2. **Check port 3000 is free.** If another app is using it, add this to `.env`:
   ```
   FRONTEND_HOST_PORT=3002
   ```
3. **Make sure `.env` exists:**
   ```bash
   cp .env.example .env
   ```

## Run with Docker (recommended)

```bash
git pull
docker compose down -v            # only needed once after a schema change
docker compose up -d --build      # build and start in the background
docker compose ps                 # wait until postgres, backend, locker-controller and frontend show "healthy"
```

| What | URL |
|---|---|
| App | http://localhost:3000 (or whatever `FRONTEND_HOST_PORT` is) |
| API docs (Swagger) | http://localhost:8000/docs |
| Health | http://localhost:8000/health/ready |
| Metrics (Prometheus format) | http://localhost:8000/metrics |

- **Admin login:** `admin` / `Admin@12345`. This account comes from `database/init/02-seed.sql`.
- **To try a parcel:** register a normal user, then log in as admin and assign them a parcel. Log back in as the user to book a locker, then tap arrive and unlock.

Day-to-day commands:

```bash
docker compose logs -f backend          # watch backend logs
docker compose up -d --build backend    # rebuild after backend code changes
docker compose up -d --build frontend   # rebuild after frontend changes
docker compose down                     # stop (keeps data)
docker compose down -v                  # stop and wipe the DB (needed after any schema/seed change)
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'   # SQL shell
```

Optional extras:

```bash
docker compose --profile monitoring up -d          # + Prometheus (:9090) and Grafana (http://localhost:3000/grafana/)
docker compose up -d --scale locker-controller=2   # two gRPC controllers, calls alternate between them
docker compose stop locker-controller              # unlocks now fail with 503, booking still works
```

## Run the pieces separately (faster when editing code)

```bash
# Terminal 1: just the database
docker compose up -d postgres

# Terminal 2: backend with auto-reload
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export $(grep -v '^#' ../.env | xargs) POSTGRES_HOST=localhost POSTGRES_PORT=5433
uvicorn app.main:app --reload --port 8000
# Door unlocks are simulated in-process here (no LOCKER_CONTROLLER_ADDR); that is expected.

# Terminal 3: frontend with hot reload
cd frontend
npm install
npm run dev        # http://localhost:5173, /api is proxied to :8000
```

## Run the tests

```bash
python3 -m pip install pytest requests
python3 -m pytest tests/test_init.py tests/test_end_to_end.py -v
# against Kubernetes: kubectl -n smart-locker port-forward svc/backend 8000:8000, then the same command
```

## Kubernetes

```bash
minikube start --cpus 4 --memory 6144    # needs room for 3 database instances
make k8s-addons                          # once per cluster: metrics-server + CloudNativePG operator
make k8s-images                          # build the 3 images and load them into the cluster
make k8s-up                              # Secrets from .env, then kubectl apply -k .
kubectl -n smart-locker get pods -w      # about 2 minutes until everything is 1/1 Running
make k8s-app                             # http://localhost:8080
make k8s-demo-on                         # let the admin dashboard run the demos
```

Demos: open the app, sign in as admin, **Testing System**: Load Balancing, Scalability and Fault
Tolerance have live graphs and buttons. From a terminal instead: `make k8s-loadtest` (autoscaling),
`make k8s-failover` (database failover), `make k8s-grafana` (http://localhost:3001/grafana/). Without `make`, the
plain commands are in README section 7. To clean up, use `kubectl delete -k .` (this also deletes the
database data) or `make k8s-down`.

## Troubleshooting

| What you see | Cause | Fix |
|---|---|---|
| API returns 500 on most pages, logs say `relation "locker" does not exist` | Old database volume | `docker compose down -v`, then `docker compose up -d --build` |
| `bind: address already in use` on 3000/8000/5433 | Another app has the port | Change `FRONTEND_HOST_PORT` / `BACKEND_HOST_PORT` / `POSTGRES_HOST_PORT` in `.env`, or find it with `lsof -nP -iTCP:3000 -sTCP:LISTEN` |
| `Cannot connect to the Docker daemon` | Docker Desktop isn't running | `open -a Docker` |
| Compose says `Set POSTGRES_PASSWORD in .env` | No `.env` file | `cp .env.example .env` |
| `/health/ready` returns 503 | Backend can't reach the database | `docker compose ps` and `docker compose logs postgres` |
| "This parcel belongs to another account" on arrive | Only the parcel's owner can mark it arrived | Log in as that user, not as admin |
| Frontend loads but API calls fail (502) | Backend restarting or crashed | `docker compose logs backend` |
| k8s pods show `ErrImageNeverPull` | Cluster can't see your local images | `make k8s-images` (Docker Desktop k8s needs nothing) |
| `no matches for kind "Cluster"` on `kubectl apply -k .` | CloudNativePG operator not installed | `make k8s-addons`, then apply again |
| `kubectl get hpa` shows `<unknown>` | metrics-server missing or starting | `make k8s-addons`, wait a minute |
| Unlock returns 503 "locker controller is not responding" | The gRPC controller is down | Compose: `docker compose ps locker-controller`; k8s: `kubectl -n smart-locker get pods -l app=locker-controller` |

## Notes

- **Admin concurrency test:** each run creates a throwaway `admin-test-...` user that owns all the test parcels and luggage. Requests beyond the free lockers stay `CONDITIONAL`, which is expected. Use Locker Status in the admin panel to release test allocations.
- **Database down:** API calls return 503 with "Database is temporarily unavailable" and work again within a couple of seconds of the database coming back. `/health/live` keeps returning 200.
- **Admin account:** `02-seed.sql` always creates `admin` / `Admin@12345`. If you set a different `ADMIN_USERNAME` in `.env`, the backend also creates that admin on startup using `ADMIN_INITIAL_PASSWORD`. Existing accounts are never changed, so editing the password later does not reset it.
- **`App.jsx` and `index.css` in the repo root are unused.** They're old uploads; the real ones are in `frontend/src/`.
- **Kubernetes database:** it's a CloudNativePG cluster, not a single pod. `kubectl -n smart-locker get cluster locker-db` shows its health and current primary.
