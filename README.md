# Smart Locker System - Infrastructure Scaffold

Docker and Kubernetes infrastructure for the smart locker coursework project. It gives the team a
working frame (containers, database volume, configuration, health checks, Kubernetes manifests)
that the frontend, backend and database code plugs into. **No locker business logic, UI,
API endpoints or database schema is included.**

```
Browser --> frontend (nginx :80) --/api/*--> backend (FastAPI :8000) --> postgres (:5432)
            serves the React build           /health/live, /health/ready      persistent volume
```

## 1. Status at a glance

| Area           | Provided by the scaffold (placeholder)                                                                                                                                             | Your team implements                                                                                                    |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Frontend       | Vite + React project, one placeholder page that calls `/api/ping` and shows whether the backend and database are reachable. Dockerfile, nginx serving + `/api` proxy.              | The real UI (replace `frontend/src/`).                                                                                  |
| Backend        | FastAPI app with `/health/live`, `/health/ready` (placeholder TCP check of the database) and a placeholder `/api/ping`. Settings read from environment variables. Dockerfile.      | Real readiness check, all API endpoints, locker logic, database access layer. Delete `/api/ping` when no longer needed. |
| Database       | PostgreSQL 16 container, persistent volume, credentials from environment, health check.                                                                                            | The schema (tables, migrations, seed data). **Nothing is created for you.**                                             |
| Docker Compose | Runs all three services, waits for healthy dependencies.                                                                                                                           | -                                                                                                                       |
| Kubernetes     | Namespace, ConfigMap, Secret template + script, PostgreSQL Deployment with a PVC, backend Deployment (**2 replicas**, readiness + liveness probes), frontend Deployment, Services. | Decide how the schema is created in Kubernetes (see `database/README.md`).                                              |

Not everything has been run end to end yet. **Read [Test status](#10-test-status-and-first-run-checklist) before you start.**

## 2. Project structure

```
smart-locker-system/
├── README.md
├── docker-compose.yml        # frontend + backend + PostgreSQL for local development
├── .env.example              # copy to .env (git-ignored) and set the database password
├── Makefile                  # optional shortcuts (everything is also given as plain commands)
├── frontend/                 # React + Vite            -> frontend teammate
│   ├── package.json, package-lock.json, vite.config.js, index.html
│   └── src/                  # PLACEHOLDER page: replace
├── backend/                  # FastAPI                 -> backend teammate
│   ├── requirements.txt
│   └── app/                  # main.py, config.py, health.py (PLACEHOLDERS): replace/extend
├── database/                 # PostgreSQL              -> database teammate
│   ├── README.md
│   └── init/                 # *.sql / *.sh run once on a fresh volume (Compose only)
├── docker/
│   ├── backend.Dockerfile
│   ├── frontend.Dockerfile
│   └── nginx/default.conf.template
└── kubernetes/
    ├── kustomization.yaml    # `kubectl apply -k kubernetes/`
    ├── namespace.yaml, configmap.yaml
    ├── secret.yaml.example   # template only (no real values)
    ├── create-secret.sh      # creates the Secret from your .env
    ├── postgres/             # pvc.yaml, deployment.yaml, service.yaml
    ├── backend/              # deployment.yaml (2 replicas + probes), service.yaml
    └── frontend/             # deployment.yaml, service.yaml
```

`docker-compose.yml` sits in the repository root (the Dockerfiles are in `docker/`) so that Compose
finds your `.env` file automatically.

## 3. Required software

| Software                                                                 | Needed for                                          | Notes                                                                                 |
| ------------------------------------------------------------------------ | --------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Docker Desktop (Windows/macOS) or Docker Engine + Compose plugin (Linux) | Everything                                          | Compose **v2.17 or newer** (`docker compose version`). Give Docker at least 4 GB RAM. |
| `kubectl`                                                                | Kubernetes only                                     | Includes kustomize (`kubectl apply -k`).                                              |
| A local cluster: Docker Desktop's Kubernetes, **minikube**, or **kind**  | Kubernetes only                                     | Pick one.                                                                             |
| Git Bash or WSL                                                          | Windows users running `sh` scripts / `make`         | Only needed for `create-secret.sh` and the Makefile.                                  |
| `make`                                                                   | Optional                                            | Plain commands are always given.                                                      |
| Node.js 22 and Python 3.12                                               | Only if you run the frontend/backend outside Docker | Section 6.                                                                            |

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

| What                                                                            | URL                                                                    |
| ------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Placeholder web page (should show "Backend reachable" and "Database reachable") | http://localhost:3000                                                  |
| Backend liveness / readiness                                                    | http://localhost:8000/health/live , http://localhost:8000/health/ready |
| Backend interactive API docs (FastAPI)                                          | http://localhost:8000/docs                                             |
| PostgreSQL from your machine                                                    | `localhost:5432` (user, password and database name from `.env`)        |

Everyday commands:

```bash
docker compose up -d --build      # start in the background (rebuilds changed images)
docker compose ps                 # all three services should become "healthy"
docker compose logs -f backend    # follow one service's logs
docker compose up -d --build backend   # rebuild and restart only the backend
docker compose down               # stop; database data is KEPT
docker compose down -v            # stop AND DELETE the database volume (fresh database)
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

| Variable                                  | Used by                                         | Compose gets it from           | Kubernetes gets it from   |
| ----------------------------------------- | ----------------------------------------------- | ------------------------------ | ------------------------- |
| `POSTGRES_DB`                             | postgres, backend                               | `.env`                         | ConfigMap `locker-config` |
| `POSTGRES_USER`                           | postgres, backend                               | `.env`                         | Secret `locker-db-secret` |
| `POSTGRES_PASSWORD`                       | postgres, backend                               | `.env`                         | Secret `locker-db-secret` |
| `POSTGRES_HOST` = `postgres`              | backend                                         | `docker-compose.yml`           | ConfigMap                 |
| `POSTGRES_PORT` = `5432`                  | backend                                         | `docker-compose.yml`           | ConfigMap                 |
| `APP_ENV`                                 | backend                                         | `.env` (default `development`) | ConfigMap                 |
| `BIND_ADDRESS` (`127.0.0.1` or `0.0.0.0`) | Compose published ports of frontend and backend | `.env`                         | -                         |
| `BACKEND_URL` = `http://backend:8000`     | frontend's nginx                                | `docker-compose.yml`           | ConfigMap                 |
| `VITE_API_BASE_URL` (default `/api`)      | frontend build                                  | Dockerfile build argument      | -                         |

Service names are the same in Compose and Kubernetes (`postgres`, `backend`, `frontend`), so the
same hostnames work in both. Host ports (`FRONTEND_HOST_PORT`, `BACKEND_HOST_PORT`,
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

**Frontend** (Node 22):

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173 ; /api/* is proxied to http://localhost:8000
```

Use a different backend with `VITE_DEV_PROXY_TARGET=http://host:port npm run dev`.

## 7. Kubernetes deployment

Written for a single-laptop cluster. The images are built locally and **loaded into the cluster**;
they are never pulled from a registry (`imagePullPolicy: IfNotPresent`, tag `:local`).

**1. Start a cluster** (one of):

```bash
# Docker Desktop: Settings -> Kubernetes -> Enable Kubernetes
minikube start
kind create cluster --name smart-locker
kubectl get nodes        # should list one Ready node
```

**2. Build the images** (Compose builds them with the exact tags the manifests use):

```bash
docker compose build
```

**3. Make the images available to the cluster:**

```bash
# minikube
minikube image load smart-locker-backend:local
minikube image load smart-locker-frontend:local

# kind
kind load docker-image smart-locker-backend:local --name smart-locker
kind load docker-image smart-locker-frontend:local --name smart-locker

# Docker Desktop Kubernetes: usually nothing to do (it sees your local images).
# If pods later show ErrImagePull / ErrImageNeverPull, check which cluster type you enabled.
```

The `postgres:16-alpine` image is pulled by the cluster itself.

**4. Create the database Secret from your `.env`** (no credentials ever go into a manifest):

```bash
sh kubernetes/create-secret.sh
# Windows without Git Bash/WSL, do it by hand instead:
#   kubectl apply -f kubernetes/namespace.yaml
#   kubectl -n smart-locker create secret generic locker-db-secret --from-literal=POSTGRES_USER=<user> --from-literal=POSTGRES_PASSWORD=<password>
```

**5. Apply the manifests:**

```bash
kubectl apply -k .
kubectl -n smart-locker get pods -w
```

Expected when ready: `postgres` 1/1, `backend` **two** pods 1/1, `frontend` 1/1.
(Use `kubectl apply -k`, not `kubectl apply -f kubernetes/`: kustomize applies the namespace first.)

**6. Open the app:**

```bash
kubectl -n smart-locker port-forward svc/frontend 8080:80     # then http://localhost:8080
kubectl -n smart-locker port-forward svc/backend 8000:8000    # then http://localhost:8000/health/ready
```

**Useful checks**

```bash
kubectl -n smart-locker get deploy,pods,svc,pvc
kubectl -n smart-locker describe pod <pod-name>
kubectl -n smart-locker logs deployment/backend
```

**Optional demo: readiness vs liveness.** Scale the database to zero:
`kubectl -n smart-locker scale deployment/postgres --replicas=0`. The backend pods should go to
`0/1` Ready (removed from the Service) but **not restart**, because liveness does not depend on the
database. Scale back with `--replicas=1` and they become Ready again.

**After you change code:** rebuild (`docker compose build backend`), load the image into the
cluster again (step 3), then `kubectl -n smart-locker rollout restart deployment/backend`
(the tag stays `:local`, so Kubernetes needs the restart to pick up the new image).

**Clean up:** `kubectl delete -k kubernetes/` removes everything, **including the database data and
the Secret**. Then `minikube delete` / `kind delete cluster --name smart-locker` if you want the cluster gone too.

## 8. How teammates plug in their code

### Frontend teammate (`frontend/`)

1. Replace `src/` (and `index.html` if needed). Keep `npm run build` producing `dist/`, and commit `package-lock.json` (the Docker build uses `npm ci`). Using yarn/pnpm? Change the two install lines in `docker/frontend.Dockerfile`.
2. Call the backend with relative URLs: `fetch("/api/...")`. nginx (Docker/Kubernetes) and the Vite dev server both forward `/api/*` to the backend, so no CORS setup and no hard-coded addresses.
3. Run: `npm run dev` for hot reload (section 6), or `docker compose up -d --build frontend` to test the production build.
4. Build-time settings must start with `VITE_`; pass them as `build.args` in `docker-compose.yml`.

### Backend teammate (`backend/`)

1. Keep: entrypoint `app.main:app`; `GET /health/live`; `GET /health/ready`; business routes under `/api/...` (nginx only forwards `/api/*`).
2. Implement the real readiness check in `app/health.py` (for example `SELECT 1` through your connection pool) and keep `/health/live` independent of the database. The Kubernetes probes call exactly these two paths.
3. Read configuration through `app/config.py`; `get_settings().database_url` builds the connection URL from the `POSTGRES_*` variables.
4. Add libraries to `requirements.txt`, then `docker compose up -d --build backend`. The image copies the whole `backend/` folder, so new folders (e.g. migrations) are included.
5. Delete the placeholder `/api/ping` when real endpoints exist, and tell the frontend teammate (their placeholder page calls it).
6. If your app connects to the database at startup, retry for a few seconds: Compose waits for a healthy database, Kubernetes does not (a crashing pod is restarted automatically, but retrying is cleaner).
7. API docs are at http://localhost:8000/docs (published backend port; not reachable through the frontend's `/api` proxy).

### Database teammate (`database/`)

1. Put schema SQL in `database/init/` (e.g. `01-schema.sql`). It runs **once**, on an empty volume. To re-run: `docker compose down -v`.
2. Kubernetes does not mount `database/init/`. Agree with the backend teammate how the schema gets created there (migrations from the backend, or a ConfigMap mounted at `/docker-entrypoint-initdb.d`). Details in `database/README.md`.
3. Tell the team if you change `POSTGRES_DB` or `POSTGRES_USER`: keep `.env.example` and `kubernetes/configmap.yaml` consistent.

## 9. Troubleshooting

**Docker Compose**

| Symptom                                                                                        | Likely cause and fix                                                                                                                                                                                                                               |
| ---------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `required variable POSTGRES_PASSWORD is missing a value`                                       | You have no `.env`, or the password is blank. `cp .env.example .env` and set it (or `make env`).                                                                                                                                                   |
| `port is already allocated` / `address already in use` / `forbidden by its access permissions` | Another program uses (or Windows has reserved) 3000, 8000 or 5432. A local PostgreSQL on 5432 is the usual cause. Change `*_HOST_PORT` in `.env`, e.g. `POSTGRES_HOST_PORT=5433`, then `docker compose down` and `up` again.                       |
| Phone cannot open `http://<laptop-ip>:3000`                                                    | Check `BIND_ADDRESS=0.0.0.0` in `.env` and that you re-ran `docker compose up -d`; both devices are on the same Wi-Fi; the Windows firewall allowed Docker and the network is "Private"; the Wi-Fi does not isolate clients (try a phone hotspot). |
| Backend log: `password authentication failed`                                                  | The password in `.env` differs from the one the database was first created with. PostgreSQL only reads `POSTGRES_PASSWORD` on an **empty** volume. Either restore the old password or wipe the data: `docker compose down -v`.                     |
| Scripts in `database/init/` did not run                                                        | They only run on an empty volume. `docker compose down -v`, then `up` again.                                                                                                                                                                       |
| Web page loads but says backend not reachable / `502 Bad Gateway`                              | Backend is down or unhealthy. `docker compose ps` and `docker compose logs backend`.                                                                                                                                                               |
| `/health/ready` returns 503                                                                    | Backend cannot reach PostgreSQL: check `docker compose ps` (database healthy?) and the credentials. (503 here is the intended behaviour.)                                                                                                          |
| `npm ci` fails: lock file out of sync                                                          | Run `npm install` in `frontend/` and commit the updated `package-lock.json`.                                                                                                                                                                       |
| Image build fails while downloading base images                                                | Check internet/VPN/proxy access to Docker Hub; Docker Hub also rate-limits anonymous pulls, so `docker login` can help.                                                                                                                            |
| Windows: `exec ... no such file or directory` or `\r` errors                                   | Line endings were converted to CRLF. `.gitattributes` prevents this on a fresh clone; re-clone or run `git add --renormalize .`.                                                                                                                   |
| Everything is stale / strange                                                                  | `docker compose down -v`, then `docker compose up --build` (deletes database data).                                                                                                                                                                |

**Kubernetes**

| Symptom                                                                       | Likely cause and fix                                                                                                                                                                                                                                                                                              |
| ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Pod `CreateContainerConfigError`                                              | The Secret is missing. Run step 4, then `kubectl -n smart-locker describe pod <pod>` to confirm.                                                                                                                                                                                                                  |
| `ErrImagePull` / `ImagePullBackOff` / `ErrImageNeverPull` on `smart-locker-*` | The image is not inside the cluster. Repeat step 3 for your cluster type.                                                                                                                                                                                                                                         |
| Backend pods stay `0/1` Ready                                                 | Readiness is failing. `kubectl -n smart-locker logs deployment/backend`, check the postgres pod is Ready, and `describe pod` for probe messages.                                                                                                                                                                  |
| `postgres` pod `Pending`, PVC `Pending`                                       | No default StorageClass: `kubectl get storageclass`. minikube, kind and Docker Desktop normally have one.                                                                                                                                                                                                         |
| Changed the password but the database still uses the old one                  | Same rule as Compose: the data volume keeps the old password. To start fresh: `kubectl -n smart-locker scale deployment/postgres --replicas=0`, `kubectl -n smart-locker delete pvc postgres-data`, re-apply (`kubectl apply -k kubernetes/`), re-run `create-secret.sh`, scale back to 1. This deletes the data. |
| Code changes do not appear                                                    | Rebuild, reload the image into the cluster, then `rollout restart` (see "After you change code").                                                                                                                                                                                                                 |
| `port-forward` fails: address in use                                          | Choose another local port, e.g. `8081:80`.                                                                                                                                                                                                                                                                        |
| `unable to connect to the server`                                             | The cluster is not running: start Docker Desktop's Kubernetes / `minikube start` / recreate the kind cluster, and check `kubectl config current-context`.                                                                                                                                                         |

## 10. Test status and first-run checklist

This scaffold was assembled in a sandbox where Docker Hub was blocked by network policy, so the
**container images could not be built or started and no Kubernetes cluster was available**.
Everything that could be checked without them was.

| Item                                                                                                                                                        | Result                                                                                                                                       |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `docker compose config` (YAML, variable substitution, required-variable errors)                                                                             | Verified: clear error without `.env`, correct resolved config with it                                                                        |
| Placeholder backend run natively: `/health/live`, `/health/ready`, `/api/ping`                                                                              | Verified: liveness stays 200 with the database down; readiness is 503 without and 200 with a reachable database; no credentials in responses |
| Placeholder frontend: `npm install`, `npm run build`; dev server proxying `/api` to the backend                                                             | Verified                                                                                                                                     |
| nginx config syntax (rendered with `BACKEND_URL`)                                                                                                           | Verified with a static parser only, **not** by real nginx                                                                                    |
| Kubernetes manifests: schema validation (strict) against Kubernetes 1.29, 1.31 and 1.37                                                                     | Verified                                                                                                                                     |
| Kubernetes manifests: cross-checks (labels/selectors, ports, ConfigMap/Secret keys, PVC name, 2 backend replicas, probe paths, no credentials in manifests) | Verified                                                                                                                                     |
| `kubernetes/create-secret.sh` logic (missing file, empty password, CRLF files, only credential keys stored)                                                 | Verified with a stand-in `kubectl`, **not** against a real cluster                                                                           |
| `make env`, Makefile dry runs                                                                                                                               | Verified                                                                                                                                     |
| `docker build` of the frontend and backend images                                                                                                           | **Not verified**                                                                                                                             |
| `docker compose up` end to end (healthy containers, database volume persistence, nginx `/api` proxy in the container, the web page at :3000)                | **Not verified**                                                                                                                             |
| nginx template substitution by the official image (`envsubst`)                                                                                              | **Not verified**                                                                                                                             |
| `kubectl apply -k kubernetes/` on a cluster, probes, 2 running backend replicas, PVC binding                                                                | **Not verified** (kubectl and a cluster were not available)                                                                                  |
| Loading images into minikube / kind / Docker Desktop                                                                                                        | **Not verified** (commands are the standard ones)                                                                                            |

**First-run checklist for the first teammate with Docker (about 10 minutes):**

1. `cp .env.example .env`, set a password, `docker compose up --build`.
2. `docker compose ps`: `postgres`, `backend`, `frontend` all `healthy`.
3. http://localhost:3000 shows "Backend reachable" and "Database reachable".
4. `docker compose down` then `up -d`: the database volume still exists (`docker volume ls` shows `smart-locker_postgres_data`).
5. Kubernetes: follow section 7; expect 2 ready backend pods and the same page via port-forward.
6. Fix anything that differs from the above and note it here, so the README stays accurate.
