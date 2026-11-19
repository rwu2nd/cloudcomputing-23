# Smart Locker System - Infrastructure Scaffold

Docker and Kubernetes infrastructure for the smart locker coursework project. It gives the team a
working frame (containers, database volume, configuration, health checks, Kubernetes manifests)
that the frontend, backend and database code plugs into. **No locker business logic, UI,
API endpoints or database schema is included.**

```
Browser --REST--> frontend (nginx :80) --/api/*--> backend (FastAPI :8000) --gRPC--> locker-controller (:50051)
                  serves the React build           2-6 pods (autoscaled)            simulated door hardware
                                                        |
                                                        +--> PgBouncer --> PostgreSQL
                                                                           Compose: 1 container
                                                                           Kubernetes: CloudNativePG,
                                                                           1 primary + 2 replicas
Prometheus scrapes the backend, the controller and the database; Grafana shows the dashboard.
```

**[CLOUD.md](CLOUD.md)** explains which cloud-computing concepts the project shows, where they live in
the code, and gives a step-by-step demo script with measured results.

## 1. Status at a glance

| Area | Provided by the scaffold (placeholder) | Your team implements |
|---|---|---|
| Frontend | Vite + React project, one placeholder page that calls `/api/ping` and shows whether the backend and database are reachable. Dockerfile, nginx serving + `/api` proxy. | The real UI (replace `frontend/src/`). |
| Backend | FastAPI app with `/health/live`, `/health/ready` (placeholder TCP check of the database) and a placeholder `/api/ping`. Settings read from environment variables. Dockerfile. | Real readiness check, all API endpoints, locker logic, database access layer. Delete `/api/ping` when no longer needed. |
| Database | PostgreSQL 16 container, persistent volume, credentials from environment, health check. | The schema (tables, migrations, seed data). **Nothing is created for you.** |
| Docker Compose | Runs all three services, waits for healthy dependencies, restarts them after a crash. | - |
| Kubernetes | Namespace, ConfigMap, Secrets from `.env`, PostgreSQL as a 3-instance CloudNativePG cluster with a PgBouncer pooler, backend Deployment autoscaled 2-6 pods (HPA), gRPC locker-controller (2 pods, headless Service), frontend (2 pods), PodDisruptionBudgets, Prometheus + Grafana, in-cluster k6 load test. | - |
| gRPC | `proto/` contract, `locker-controller/` service, backend client with deadlines, retries, round-robin load balancing and client-side health checking (see [CLOUD.md](CLOUD.md)). | - |
| Fault tolerance | Restart policies, replicas, probes, rolling updates, automatic database failover, graceful gRPC shutdown (see [section 8](#8-fault-tolerance)). | - |
| CI/CD | GitHub Actions: build + test with Compose, deploy to a 3-node kind cluster and test (including a database failover), publish images to GitHub Container Registry on `main`. | - |

Most of this has now been run on a real machine. **Read [Test status](#11-test-status-and-first-run-checklist) to see what is confirmed and what is not.**

## 2. Project structure

```
smart-locker-system/
├── README.md
├── docker-compose.yml        # frontend + backend + PostgreSQL for local development
├── .env.example              # copy to .env (git-ignored) and set the database password
├── Makefile                  # optional shortcuts (everything is also given as plain commands)
├── kustomization.yaml        # `kubectl apply -k .` (lives in the root so it can read database/init/ and monitoring/)
├── CLOUD.md                  # cloud concepts, architecture and demo script
├── proto/                    # gRPC contract (locker_controller.proto), shared by backend and controller
├── locker-controller/        # gRPC service: simulated door hardware
├── notification-service/     # gRPC service: notifications, streamed to the browser
├── monitoring/               # Prometheus scrape configs + Grafana dashboard (used by Compose and Kubernetes)
├── tests/                    # pytest API tests (test_end_to_end.py runs against Compose or Kubernetes)
├── .github/workflows/ci.yml  # CI/CD pipeline
├── frontend/                 # React + Vite            -> frontend teammate
│   ├── package.json, package-lock.json, vite.config.js, index.html
│   └── src/                  # PLACEHOLDER page: replace
├── backend/                  # FastAPI                 -> backend teammate
│   ├── requirements.txt
│   └── app/                  # main.py, luggage.py, locker_controller.py (gRPC client), metrics.py, ...
├── database/                 # PostgreSQL              -> database teammate
│   ├── README.md
│   └── init/                 # *.sql / *.sh run once on a fresh volume (Compose only)
├── docker/
│   ├── backend.Dockerfile, frontend.Dockerfile, locker-controller.Dockerfile
│   └── nginx/default.conf.template
└── kubernetes/
    ├── namespace.yaml, configmap.yaml
    ├── secret.yaml.example   # template only (no real values)
    ├── create-secret.sh      # creates the Secrets from your .env
    ├── addons/install.sh     # metrics-server + CloudNativePG operator (once per cluster)
    ├── database/             # CloudNativePG Cluster (3 instances) + PgBouncer Pooler
    ├── backend/              # deployment.yaml, service.yaml, hpa.yaml (2-6 pods), pdb.yaml
    ├── locker-controller/    # deployment.yaml (gRPC probes), headless service.yaml, pdb.yaml
    ├── notification-service/ # deployment.yaml (1 replica, see comment), service.yaml
    ├── frontend/             # deployment.yaml (2 replicas + probes), service.yaml, pdb.yaml
    ├── monitoring/           # prometheus.yaml, grafana.yaml
    ├── loadtest/             # k6 load test Job (`make k8s-loadtest`)
    └── kind-cluster.yaml     # 3-node kind cluster (used by CI)
```

`docker-compose.yml` sits in the repository root (the Dockerfiles are in `docker/`) so that Compose
finds your `.env` file automatically.

## 3. Required software

| Software | Needed for | Notes |
|---|---|---|
| Docker Desktop (Windows/macOS) or Docker Engine + Compose plugin (Linux) | Everything | Compose **v2.17 or newer** (`docker compose version`). Give Docker at least 4 GB RAM. |
| `kubectl` | Kubernetes only | Includes kustomize (`kubectl apply -k`). |
| A local cluster: Docker Desktop's Kubernetes, **minikube**, or **kind** | Kubernetes only | Pick one. |
| Git Bash or WSL | Windows users running `sh` scripts / `make` | Only needed for `create-secret.sh` and the Makefile. |
| `make` | Optional | Plain commands are always given. |
| Node.js 22 and Python 3.12 | Only if you run the frontend/backend outside Docker | Section 6. |

## 4. Quick start (Docker Compose)

```bash
# 1. Create your local configuration (the file is git-ignored)
cp .env.example .env          # Windows cmd: copy .env.example .env
#    then open .env and set POSTGRES_PASSWORD
#    (or run `make env`, which creates .env with a random password)

# 2. Build and start everything
docker compose up --build
# if this does not work, try: docker-compose up --build
```

First start takes a few minutes (it downloads base images). When it is up:

| What | URL |
|---|---|
| Placeholder web page (should show "Backend reachable" and "Database reachable") | http://localhost:3000 |
| Backend liveness / readiness | http://localhost:8000/health/live , http://localhost:8000/health/ready |
| Backend interactive API docs (FastAPI) | http://localhost:8000/docs |
| PostgreSQL from your machine | `localhost:5432` (user, password and database name from `.env`) |
| Prometheus / Grafana (only with `--profile monitoring`, see below) | http://localhost:9090 , http://localhost:3000/grafana/ (also http://localhost:3001/grafana/) |

Everyday commands:

```bash
docker compose up -d --build      # start in the background (rebuilds changed images)
docker compose ps                 # postgres, backend, locker-controller, frontend should become "healthy"
docker compose logs -f backend    # follow one service's logs
docker compose up -d --build backend   # rebuild and restart only the backend
docker compose down               # stop; database data is KEPT
docker compose down -v            # stop AND DELETE the database volume (fresh database)

docker compose --profile monitoring up -d          # also start Prometheus + Grafana
docker compose up -d --scale locker-controller=2   # two gRPC controllers, calls are load-balanced
```

Open a SQL shell: `docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'`

### Reaching the app from a phone on the same Wi-Fi

By default only your own computer can connect. To let a phone on the **same Wi-Fi** in:

1. In `.env` set `BIND_ADDRESS=0.0.0.0`, then `docker compose up -d` (it recreates the containers).
2. Find the laptop's Wi-Fi address: `ipconfig` (Windows, "IPv4 Address" of the Wi-Fi adapter), `ipconfig getifaddr en0` (macOS) or `hostname -I` (Linux). Example: `192.168.1.23`.
3. On the phone open `http://192.168.1.23:3000`. A mobile app can call the API through the same port: `http://192.168.1.23:3000/api/...` (nginx forwards it to the backend), or directly on `:8000`.
4. Windows: allow Docker through the firewall when prompted, and make sure the Wi-Fi network is set to **Private**.

Notes: only the frontend (3000) and backend (8000) ports are opened; the database port stays local. There is no
login yet, so anyone on that Wi-Fi can use the app: do this on a trusted network, and set `BIND_ADDRESS` back to
`127.0.0.1` afterwards. The laptop's address can change when it reconnects. Some campus, office and guest Wi-Fi
networks block device-to-device traffic ("client isolation"); if the phone cannot connect, use your home network or
a phone hotspot. On Kubernetes add `--address 0.0.0.0` to the `kubectl port-forward` commands.

## 5. Configuration contract

Nothing is hard-coded. Credentials exist only in your local `.env` (Compose) or in a Kubernetes
Secret created from it. Compose refuses to start while `POSTGRES_PASSWORD` is empty.

| Variable | Used by | Compose gets it from | Kubernetes gets it from |
|---|---|---|---|
| `POSTGRES_DB` | postgres, backend | `.env` | ConfigMap `locker-config` |
| `POSTGRES_USER` | postgres, backend | `.env` | Secret `locker-db-secret` |
| `POSTGRES_PASSWORD` | postgres, backend | `.env` | Secret `locker-db-secret` |
| `POSTGRES_HOST` | backend | `docker-compose.yml` (`postgres`) | ConfigMap (`locker-db-pooler-rw`, the PgBouncer pooler) |
| `POSTGRES_PORT` = `5432` | backend | `docker-compose.yml` | ConfigMap |
| `APP_ENV` | backend | `.env` (default `development`) | ConfigMap |
| `BIND_ADDRESS` (`127.0.0.1` or `0.0.0.0`) | Compose published ports of frontend and backend | `.env` | - |
| `BACKEND_URL` = `http://backend:8000` | frontend's nginx | `docker-compose.yml` | ConfigMap |
| `LOCKER_CONTROLLER_ADDR` | backend (gRPC client) | `docker-compose.yml` (`dns:///locker-controller:50051`) | ConfigMap (headless Service address). Unset = door unlocks simulated in-process |
| `CONTROLLER_FAILURE_RATE`, `CONTROLLER_ACTUATION_MS` | locker-controller | `.env` (defaults 0 and 80) | env in `kubernetes/locker-controller/deployment.yaml` |
| `VITE_API_BASE_URL` (default `/api`) | frontend build | Dockerfile build argument | - |

Service names are the same in Compose and Kubernetes (`backend`, `frontend`, `locker-controller`,
`prometheus`, `grafana`). The database is the exception: Kubernetes reaches it through the pooler
(`locker-db-pooler-rw`) instead of `postgres`. Host ports (`FRONTEND_HOST_PORT`, `BACKEND_HOST_PORT`,
`POSTGRES_HOST_PORT`) can be changed in `.env` if something on your laptop already uses them.

**Adding a new setting for the backend**

- Not secret: add it under `backend.environment` in `docker-compose.yml` and as a key in `kubernetes/configmap.yaml`. The Kubernetes backend loads every ConfigMap key automatically (restart with `kubectl -n smart-locker rollout restart deployment/backend`).
- Secret: follow how `POSTGRES_PASSWORD` is handled: blank entry in `.env.example`, `${VAR:?message}` in `docker-compose.yml`, key in `kubernetes/secret.yaml.example`, and extend the key list in `kubernetes/create-secret.sh`. The Kubernetes backend loads every Secret key automatically.

## 6. Working on one component

Fast iteration without rebuilding images: run the database in Docker and your own part natively.

**Backend** (Python 3.12):

```bash
docker compose up -d postgres
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
set -a; . ../.env; set +a                              # load .env into this shell (macOS/Linux/Git Bash)
uvicorn app.main:app --reload --port 8000
```

(`POSTGRES_HOST` defaults to `localhost` and `POSTGRES_PORT` to `5432`. If you changed `POSTGRES_HOST_PORT`, also `export POSTGRES_PORT=<that value>`.)

Without `LOCKER_CONTROLLER_ADDR` the backend simulates door unlocks in-process and logs a warning,
so you do not need the gRPC controller for most backend work. The gRPC stubs are generated inside
the Docker images; to use the real controller outside Docker, `pip install grpcio-tools==1.85.1`,
run `make proto`, and start the controller with `cd locker-controller && python -m controller.server`.

**Frontend** (Node 22):

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173 ; /api/* is proxied to http://localhost:8000
```

Use a different backend with `VITE_DEV_PROXY_TARGET=http://host:port npm run dev`.

## 7. Kubernetes deployment

Written for a laptop cluster (minikube, kind or Docker Desktop). The images are built locally and
**loaded into the cluster**; they are never pulled from a registry (`imagePullPolicy: IfNotPresent`,
tag `:local`). With `make` the whole thing is four commands:

```bash
minikube start --cpus 4 --memory 6144      # or Docker Desktop's Kubernetes, or kind (see below)
make k8s-addons                            # once per cluster: metrics-server + CloudNativePG operator
make k8s-images                            # build the 3 images and load them into the cluster
make k8s-up                                # create the Secrets from .env and apply everything
```

The same steps without `make`:

**1. Start a cluster** (one of). Give it at least 4 CPUs and 6 GB of memory, since it runs 3 database instances.

```bash
minikube start --cpus 4 --memory 6144
kind create cluster --name smart-locker --config kubernetes/kind-cluster.yaml   # 3 nodes
# Docker Desktop: Settings -> Kubernetes -> Enable Kubernetes
kubectl get nodes
```

**2. Install the add-ons** (once per cluster):

```bash
sh kubernetes/addons/install.sh
# Windows without Git Bash/WSL:
#   minikube addons enable metrics-server        (minikube)  or
#   kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.9.1/components.yaml
#   kubectl apply --server-side -f https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.30/releases/cnpg-1.30.1.yaml
```

metrics-server feeds CPU usage to the autoscaler. CloudNativePG is the operator that runs
PostgreSQL as a replicated cluster (`kubernetes/database/cluster.yaml`).

**3. Build the images and make them available to the cluster:**

```bash
docker compose build backend frontend locker-controller

# minikube
minikube image load smart-locker-backend:local
minikube image load smart-locker-frontend:local
minikube image load smart-locker-controller:local

# kind
kind load docker-image smart-locker-backend:local smart-locker-frontend:local smart-locker-controller:local --name smart-locker

# Docker Desktop Kubernetes: nothing to do (it sees your local images).
```

**4. Create the database Secrets from your `.env`** (no credentials ever go into a manifest):

```bash
sh kubernetes/create-secret.sh
```

It creates `locker-db-secret` (for the backend) and `locker-db-app` (for the database cluster).
`POSTGRES_USER` must be `locker_user`, the owner set in `kubernetes/database/cluster.yaml`.
Windows without Git Bash: see `kubernetes/secret.yaml.example` for the two manual commands.

**5. Apply the manifests:**

```bash
kubectl apply -k .
kubectl -n smart-locker get pods -w
```

Expected when ready (about 2 minutes, the database cluster starts its instances one after the other):
`locker-db-1/2/3` (1 primary, 2 replicas), 2 `locker-db-pooler-rw`, 2 `backend`, 2 `locker-controller`,
2 `frontend`, 1 `prometheus`, 1 `grafana`, all `1/1 Running`.
(Use `kubectl apply -k`, not `kubectl apply -f kubernetes/`: kustomize applies things in the right order and builds the ConfigMaps.)

**6. Open the app and the dashboards:**

```bash
kubectl -n smart-locker port-forward svc/frontend 8080:80      # app:      http://localhost:8080
kubectl -n smart-locker port-forward svc/grafana 3001:3000     # Grafana:  http://localhost:3001/grafana/ (also /grafana/ in the app)
kubectl -n smart-locker port-forward svc/prometheus 9090:9090  # Prometheus (optional)
kubectl -n smart-locker port-forward svc/backend 8000:8000     # API docs: http://localhost:8000/docs
```

**7. Optional: let the admin dashboard run the demos.** The Testing System pages (Stress Test, Load
Balancing, Scalability, Fault Tolerance) always show live state. Their buttons (start a stress test,
kill the database primary, stop the controller, inject faults) need extra permissions for the backend,
which are only granted while you demo:

```bash
make k8s-demo-on      # kubectl apply -k kubernetes/demo-controls
make k8s-demo-off     # kubectl delete -k kubernetes/demo-controls
```

**Useful checks**

```bash
make k8s-status                                   # pods with their node, HPA, database cluster
kubectl -n smart-locker get hpa backend -w        # watch the autoscaler
kubectl -n smart-locker get cluster locker-db     # database status and current primary
kubectl -n smart-locker logs deployment/backend
```

**After you change code:** `make k8s-images`, then
`kubectl -n smart-locker rollout restart deployment/backend` (or `deployment/locker-controller`, `deployment/frontend`).
The tag stays `:local`, so Kubernetes needs the restart to pick up the new image.

**Clean up:** `kubectl delete -k .` removes everything, **including the database data and
the Secrets**. Then `minikube delete` / `kind delete cluster --name smart-locker` if you want the cluster gone too.

## 8. Fault tolerance

The system keeps working, or recovers by itself, when a container, a pod or the database restarts.
This was tested by hand on a local Kubernetes cluster ([cluster type]); see "Demonstration" below to repeat it.

### What provides it

| Where | Mechanism | Effect |
|---|---|---|
| Docker Compose | `restart: unless-stopped` on every service | A crashed container is restarted, also after a reboot |
| Docker Compose | `depends_on` with `condition: service_healthy` | Services start in order and wait for a healthy dependency |
| Docker Compose | Named volume `postgres_data` | Database data survives restarts and `docker compose down` |
| Kubernetes | Backend (2-6, autoscaled), frontend and locker-controller run at least **2 replicas** each, spread across nodes when there are several | One pod can fail without users noticing |
| Kubernetes | Backend **readiness probe** (`/health/ready`) | A pod that cannot serve (e.g. database down) is taken out of the Service, not restarted |
| Kubernetes | Backend **liveness probe** (`/health/live`) | A hung container is restarted. It does not depend on the database, so a database outage does not restart every backend pod |
| Kubernetes | Startup probe | A slow-starting backend gets about 60 seconds before the other probes begin |
| Kubernetes | Rolling update with `maxUnavailable: 0` | New pods become ready before old ones are removed: no downtime on deploys |
| Kubernetes | `preStop` hook (`sleep 5`) | A pod being replaced stops receiving traffic before it shuts down |
| Kubernetes | Deployments | Deleted or crashed pods are recreated automatically |
| Kubernetes | CloudNativePG cluster: 1 primary + 2 streaming replicas, each with its own volume | If the primary dies a replica is promoted automatically (about 2 s of failed requests in testing) and the old primary rejoins as a replica |
| Kubernetes | PgBouncer pooler, `server_login_retry: 1` | Backend connections survive the failover; PgBouncer reconnects to the new primary within a second |
| Kubernetes | PodDisruptionBudgets (`minAvailable: 1`) | Node drains and cluster upgrades never take all pods of a service at once |
| Kubernetes | Tolerations `tolerationSeconds: 30` | Pods leave a dead node after 30 s instead of the default 5 minutes |
| Backend | Door unlocks are a gRPC call **outside** the database lock, with a 3 s deadline and retries | A slow or dead controller never blocks bookings; users get a 503 for unlocks only |
| locker-controller | gRPC health protocol + graceful drain + `preStop` | Rolling updates of the controller drop no calls (756 of 756 succeeded in testing) |

### Demonstration

Use three terminals. All commands use the `smart-locker` namespace.

**Terminal 1: watch the pods**

```bash
kubectl -n smart-locker get pods -w
```

**Terminal 2: constant traffic through the backend Service** (load-balanced across both backend pods; `kubectl port-forward` is not suitable because it sticks to one pod)

```bash
kubectl -n smart-locker exec deploy/frontend -- sh -c 'while true; do wget -q -T 2 -O- http://backend:8000/health/live >/dev/null 2>&1 && echo "$(date +%T) OK" || echo "$(date +%T) FAIL"; sleep 1; done'
```

**Terminal 3: run these**

| # | Failure | Command | Expected result |
|---|---|---|---|
| 1 | A backend pod dies | `kubectl -n smart-locker delete $(kubectl -n smart-locker get pod -l app=backend -o name \| head -1)` | One pod `Terminating`, a new one starts; Terminal 2 keeps printing `OK`, no `FAIL` |
| 2 | A container crashes | `kubectl -n smart-locker exec $(kubectl -n smart-locker get pod -l app=backend -o name \| head -1) -- sh -c 'kill 1'` | That pod's `RESTARTS` goes 0 to 1 within seconds; the other pod keeps serving |
| 3 | Deploying a new version | `kubectl -n smart-locker rollout restart deployment/backend` | Pods replaced one at a time; no `FAIL` in Terminal 2 |
| 4 | The database primary dies | `make k8s-failover` (deletes the current primary and watches the instances) | Within seconds another instance shows `role=primary`; the deleted one comes back as a replica. Terminal 2 keeps printing `OK` (liveness does not touch the database). `kubectl -n smart-locker get cluster locker-db` names the new primary |
| 5 | The locker-controller goes away | `kubectl -n smart-locker scale deployment/locker-controller --replicas=0` | Unlocking a locker in the app fails after 3 s with "The locker controller is not responding"; browsing and booking still work. Scale back to 2 and unlocking works again |
| 6 | The controller is flaky | `kubectl -n smart-locker set env deployment/locker-controller FAILURE_RATE=0.4` | Unlocks still succeed: gRPC retries absorb the injected faults (Grafana shows them). Set it back to `0` afterwards |
| 7 | Load spike | `make k8s-loadtest`, and in another terminal `kubectl -n smart-locker get hpa backend -w` | The autoscaler goes from 2 to up to 6 backend pods, then scales back down a minute or two after the test |

To check the data survived demo 4, list the tables on the new primary before and after:

```bash
kubectl -n smart-locker exec $(kubectl -n smart-locker get cluster locker-db -o jsonpath='{.status.currentPrimary}') -c postgres -- psql -d locker_db -c '\dt'
```

**Docker Compose (smaller demo)**

```bash
docker compose restart postgres          # then check your tables: the data is still there
docker compose exec backend sh -c 'kill 1'    # crash the backend process; `docker compose ps` shows it restarted
```

(The second Compose command has not been verified yet.)

A real, unplanned example also happened during testing: Docker Desktop restarted the Postgres
container (exit code 255, logs end without a shutdown message). Kubernetes restarted it, Postgres
ran crash recovery from its write-ahead log (`automatic recovery in progress`), and no data was lost.

### Limitations

- **One machine.** A laptop cluster cannot survive the laptop failing. A 3-node kind cluster (`kubernetes/kind-cluster.yaml`) shows pods and database replicas on different nodes, but they still share one computer; a real cloud cluster spreads nodes over availability zones.
- **No off-site backups yet.** Replicas protect against an instance failing, not against deleting the namespace (`kubectl delete -k .` removes all data). Backup with `pg_dump` on the primary:
  `kubectl -n smart-locker exec $(kubectl -n smart-locker get cluster locker-db -o jsonpath='{.status.currentPrimary}') -c postgres -- pg_dump -d locker_db > backup.sql` (run in Git Bash; PowerShell changes the file encoding). CloudNativePG can also stream backups to object storage (S3-compatible) once one is available.
- **Monitoring data is not persistent.** Prometheus keeps metrics in an `emptyDir`, so they are lost when its pod restarts.
- Compose has no replicas or zero-downtime updates; those come from Kubernetes.

## 9. How teammates plug in their code

### Frontend teammate (`frontend/`)

1. Replace `src/` (and `index.html` if needed). Keep `npm run build` producing `dist/`, and commit `package-lock.json` (the Docker build uses `npm ci`). Using yarn/pnpm? Change the two install lines in `docker/frontend.Dockerfile`.
2. Call the backend with relative URLs: `fetch("/api/...")`. nginx (Docker/Kubernetes) and the Vite dev server both forward `/api/*` to the backend, so no CORS setup and no hard-coded addresses.
3. Run: `npm run dev` for hot reload (section 6), or `docker compose up -d --build frontend` to test the production build.
4. Build-time settings must start with `VITE_`; pass them as `build.args` in `docker-compose.yml`.
5. Handle failed API calls: retry GET requests with a short backoff and show a clear "service unavailable" message instead of hanging.

### Backend teammate (`backend/`)

1. Keep: entrypoint `app.main:app`; `GET /health/live`; `GET /health/ready`; business routes under `/api/...` (nginx only forwards `/api/*`).
2. Implement the real readiness check in `app/health.py` (for example `SELECT 1` through your connection pool) and keep `/health/live` independent of the database. The Kubernetes probes call exactly these two paths.
3. Read configuration through `app/config.py`; `get_settings().database_url` builds the connection URL from the `POSTGRES_*` variables.
4. Add libraries to `requirements.txt`, then `docker compose up -d --build backend`. The image copies the whole `backend/` folder, so new folders (e.g. migrations) are included.
5. Delete the placeholder `/api/ping` when real endpoints exist, and tell the frontend teammate (their placeholder page calls it).
6. If your app connects to the database at startup, retry for a few seconds: Compose waits for a healthy database, Kubernetes does not (a crashing pod is restarted automatically, but retrying is cleaner).
7. API docs are at http://localhost:8000/docs (published backend port; not reachable through the frontend's `/api` proxy).
8. Resilience (see section 8): use a connection pool that survives database restarts and has timeouts (with SQLAlchemy: `pool_pre_ping=True`, a pool timeout and a connect timeout), return HTTP 503 rather than crashing when the database is down, and make commands such as "open locker" idempotent so retries are safe.

### Database teammate (`database/`)

1. Put schema SQL in `database/init/` (e.g. `01-schema.sql`). It runs **once**, on an empty volume. To re-run: `docker compose down -v`.
2. Kubernetes uses the same files: `kustomization.yaml` packs `database/init/*.sql` into the `postgres-init` ConfigMap and the CloudNativePG cluster runs them once, when it is first created (followed by `kubernetes/database/99-app-ownership.sql`, which hands the tables to `locker_user`). To re-run them, delete and recreate the cluster: `kubectl -n smart-locker delete cluster locker-db`, then `kubectl apply -k .` (this deletes the data).
3. Tell the team if you change `POSTGRES_DB` or `POSTGRES_USER`: keep `.env.example` and `kubernetes/configmap.yaml` consistent.

## 10. Troubleshooting

**Docker Compose**

| Symptom | Likely cause and fix |
|---|---|
| `required variable POSTGRES_PASSWORD is missing a value` | You have no `.env`, or the password is blank. `cp .env.example .env` and set it (or `make env`). |
| `port is already allocated` / `address already in use` / `forbidden by its access permissions` | Another program uses (or Windows has reserved) 3000, 8000 or 5432. A local PostgreSQL on 5432 is the usual cause. Change `*_HOST_PORT` in `.env`, e.g. `POSTGRES_HOST_PORT=5433`, then `docker compose down` and `up` again. |
| Phone cannot open `http://<laptop-ip>:3000` | Check `BIND_ADDRESS=0.0.0.0` in `.env` and that you re-ran `docker compose up -d`; both devices are on the same Wi-Fi; the Windows firewall allowed Docker and the network is "Private"; the Wi-Fi does not isolate clients (try a phone hotspot). |
| Backend log: `password authentication failed` | The password in `.env` differs from the one the database was first created with. PostgreSQL only reads `POSTGRES_PASSWORD` on an **empty** volume. Either restore the old password or wipe the data: `docker compose down -v`. |
| Scripts in `database/init/` did not run | They only run on an empty volume. `docker compose down -v`, then `up` again. |
| Web page loads but says backend not reachable / `502 Bad Gateway` | Backend is down or unhealthy. `docker compose ps` and `docker compose logs backend`. |
| `/health/ready` returns 503 | Backend cannot reach PostgreSQL: check `docker compose ps` (database healthy?) and the credentials. (503 here is the intended behaviour.) |
| `npm ci` fails: lock file out of sync | Run `npm install` in `frontend/` and commit the updated `package-lock.json`. |
| Image build fails while downloading base images | Check internet/VPN/proxy access to Docker Hub; Docker Hub also rate-limits anonymous pulls, so `docker login` can help. |
| Windows: `exec ... no such file or directory` or `\r` errors | Line endings were converted to CRLF. `.gitattributes` prevents this on a fresh clone; re-clone or run `git add --renormalize .`. |
| Everything is stale / strange | `docker compose down -v`, then `docker compose up --build` (deletes database data). |

**Kubernetes**

| Symptom | Likely cause and fix |
|---|---|
| Pod `CreateContainerConfigError` | The Secret is missing. Run step 4, then `kubectl -n smart-locker describe pod <pod>` to confirm. |
| `ErrImagePull` / `ImagePullBackOff` / `ErrImageNeverPull` on `smart-locker-*` | The image is not inside the cluster. Repeat step 3 for your cluster type. |
| `kubectl apply -k .` says `no matches for kind "Cluster"` / `"Pooler"` | The CloudNativePG operator is not installed. Run `make k8s-addons` (or `sh kubernetes/addons/install.sh`), then apply again. |
| Backend pods stay `0/1` Ready | Readiness is failing. `kubectl -n smart-locker logs deployment/backend`, check `kubectl -n smart-locker get cluster locker-db` says "Cluster in healthy state", and `describe pod` for probe messages. |
| `locker-db-*` pods or PVCs `Pending` | No default StorageClass (`kubectl get storageclass`) or not enough memory/CPU in the cluster: `kubectl -n smart-locker describe pod locker-db-1`. Give minikube `--cpus 4 --memory 6144`. |
| `kubectl get hpa` shows `cpu: <unknown>/60%` | metrics-server is missing or still starting. `make k8s-addons`, wait a minute. |
| Unlocking a locker returns 503 "locker controller is not responding" | The gRPC controller is down or scaled to 0: `kubectl -n smart-locker get pods -l app=locker-controller`. |
| Changed the password but the database still uses the old one | The cluster reads the password when it is first created. To start fresh: `kubectl -n smart-locker delete cluster locker-db`, re-run `create-secret.sh`, then `kubectl apply -k .`. This deletes the data. |
| Code changes do not appear | Rebuild, reload the image into the cluster, then `rollout restart` (see "After you change code"). |
| `port-forward` fails: address in use | Choose another local port, e.g. `8081:80`. |
| `unable to connect to the server` | The cluster is not running: start Docker Desktop's Kubernetes / `minikube start` / recreate the kind cluster, and check `kubectl config current-context`. |
| A `locker-db-*` pod shows `RESTARTS 1` with `Exit Code: 255` | Usually Docker Desktop or the Kubernetes node was restarted, or the laptop slept: not a Postgres fault (an out-of-memory kill would say `OOMKilled`, exit 137). If it was the primary, a replica has taken over. Check `kubectl -n smart-locker logs locker-db-1 --previous`. |
| Deleting a pod during a failover test still breaks your requests | `kubectl port-forward` sticks to one pod. Send test traffic through the Service from inside the cluster (see section 8, Demonstration). |

## 11. Test status and first-run checklist

This scaffold was first assembled in a sandbox where Docker Hub was blocked by network policy, so the
container images could not be built or started there and no Kubernetes cluster was available. Static
checks were done in that sandbox; the rest was later run on a real machine with Docker Desktop and a
local Kubernetes cluster ([cluster type], 2026-11-15).

| Item | Result |
|---|---|
| `docker compose config` (YAML, variable substitution, required-variable errors) | Verified: clear error without `.env`, correct resolved config with it |
| Placeholder backend run natively: `/health/live`, `/health/ready`, `/api/ping` | Verified: liveness stays 200 with the database down; readiness is 503 without and 200 with a reachable database; no credentials in responses |
| Placeholder frontend: `npm install`, `npm run build`; dev server proxying `/api` to the backend | Verified |
| nginx config syntax (rendered with `BACKEND_URL`) | Verified with a static parser |
| nginx template substitution by the official image (`envsubst`) | Verified in Kubernetes: the frontend pods start with the rendered config and pass their `/healthz` probe |
| Kubernetes manifests: schema validation (strict) against Kubernetes 1.29, 1.31 and 1.37 | Verified |
| Kubernetes manifests: cross-checks (labels/selectors, ports, ConfigMap/Secret keys, PVC name, 2 backend replicas, probe paths, no credentials in manifests) | Verified |
| `kubernetes/create-secret.sh` logic (missing file, empty password, CRLF files, only credential keys stored) | Verified with a stand-in `kubectl`; the Secret is also in use by the running cluster |
| `make env`, Makefile dry runs | Verified |
| `docker build` of the frontend and backend images | Verified on Docker Desktop (the images run in the Kubernetes pods) |
| `docker compose up` end to end (healthy containers, database volume persistence, nginx `/api` proxy in the container, the web page at :3000) | **To confirm** with the first-run checklist below (steps 2 to 4) |
| `kubectl apply -k` on a cluster, probes, 2 running backend replicas, 2 frontend replicas, PVC binding | Verified on [cluster type], 2026-11-15 |
| Loading images into the cluster | Verified for [cluster type]; the minikube and kind commands are the standard ones and not verified |
| Failure demonstrations (section 8): pod deleted, container crashed, rolling restart, database scaled to zero and back, database pod deleted with data intact | Verified on [cluster type], 2026-11-15 |
| Compose restart policy (`docker compose exec backend sh -c 'kill 1'`, then `docker compose ps`) | **Not yet verified** |
| Compose with the gRPC controller and the monitoring profile: `tests/test_end_to_end.py` (parcel and luggage unlocks over gRPC, concurrency, metrics), controller stopped (unlock 503 in 3 s, booking still works, luggage claim rolled back), `--scale locker-controller=2` (calls alternate between replicas), `CONTROLLER_FAILURE_RATE=0.4` (all unlocks succeed through retries), Prometheus targets and Grafana dashboard | Verified on Docker Desktop, 2026-11-19 |
| Kubernetes (minikube 1.39, Kubernetes 1.37, single node): add-ons script, CloudNativePG cluster + pooler, `tests/test_end_to_end.py` through the pooler, gRPC round-robin across 2 controller pods, Prometheus discovery of all pods incl. database roles, Grafana dashboard | Verified, 2026-11-19 |
| Database failover under traffic (primary deleted): about 2 s of failed requests, then a replica is primary and the tests pass | Verified on minikube, 2026-11-19 |
| Autoscaling: k6 job with 40 users, HPA 2 -> 4 -> 6 pods, back down after the test; 23,268 requests, 0 failures, p95 171 ms | Verified on minikube, 2026-11-19 |
| Rolling restart of locker-controller under gRPC traffic: 0 of 756 calls failed | Verified on minikube, 2026-11-19 |
| CI workflow (`.github/workflows/ci.yml`) | Lint-clean (actionlint). **Not yet run on GitHub**; the kind-specific steps (image loading into kind, metrics-server manifest) have not been run locally |

**First-run checklist for the first teammate with Docker (about 10 minutes):**

1. `cp .env.example .env`, set a password, `docker compose up --build`.
2. `docker compose ps`: `postgres`, `backend`, `locker-controller`, `frontend` all `healthy`.
3. http://localhost:3000 shows "Backend reachable" and "Database reachable".
4. `docker compose down` then `up -d`: the database volume still exists (`docker volume ls` shows `smart-locker_postgres_data`).
5. Kubernetes: follow section 7; expect the pods listed in step 5 there and the same page via port-forward.
6. Fix anything that differs from the above and note it here, so the README stays accurate.


## Luggage and admin demo workflows

The luggage page uses the backend API (not browser-local demo bookings). It requires a username/password account and uses XL compartments only. In the local Compose environment, demo payment simulation is enabled by default (`LUGGAGE_DEMO_MODE=true`); it does not contact a real payment provider.

The Admin dashboard is shown only after signing in with an account whose database `role` is `ADMIN`. All admin API routes validate the bearer token and database role; normal users cannot call the admin routes. Admin controls can still create real test allocations or release compartments, so use them carefully and restrict access to trusted administrators.

### Parcel workflow

1. **Admin assigns a parcel.** In the Admin dashboard open **Assign Parcels**, pick a user from the list (id and name come from the `user` table), choose the locker size the parcel needs and optionally add a description. This inserts a row in `parcels`.
2. **User chooses a locker.** **Parcel → Choose locker for parcel** lists only the parcels assigned to the signed-in user. A user with no assigned parcels cannot reserve a locker. Choosing a parcel opens **Choose your locker**, where the size is fixed by the parcel and the user picks a location. The API (`POST /api/reservations` with `station_id` and `parcel_id`) rejects parcels that belong to someone else, that already have a live reservation, or that were already collected.
3. **Allocation.** Reservations move from `CONDITIONAL` to `ALLOCATED` through the background queue worker. Once allocated, the frontend calls the arrival endpoint and the parcel becomes `ARRIVED`.
4. **Collection.** **Parcel → Collect parcel** lists arrived parcels; selecting one and tapping **Unlock locker** unlocks it and releases the compartment. No collection password is used.

If a reservation is cancelled (or an admin force-evicts it) the parcel goes back to *Awaiting locker* and the user can choose another locker for it.

Databases created before this change need `database/migrations/001-assigned-parcels.sql` (see `database/README.md`).


## Login and default administrator

The web app opens at a shared login page at `http://localhost:3000`. Sign in with either a normal user account or the seeded administrator account; the database role determines which dashboard appears. User accounts can be registered there; new registrations always receive the `USER` role. `database/init/02-seed.sql` creates the default administrator; if `ADMIN_USERNAME` names a different account, the backend creates that one on startup.

- Default username: `admin`
- Default password: `Admin@12345`

Set `ADMIN_USERNAME` and `ADMIN_INITIAL_PASSWORD` in `.env` before starting the backend to use different initial credentials. The password must be 10-128 characters. Once the account exists, changing the environment password does not reset its stored password; change it through a password-management feature or a controlled database operation. Do not use the demo default in a deployed environment.

Admin APIs require a valid bearer token belonging to a database account whose `role` is `ADMIN`. Normal registrations cannot assign themselves the administrator role.
