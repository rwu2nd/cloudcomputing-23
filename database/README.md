# Database (PostgreSQL) - schema owned by the database teammate

The schema is in `database/init/01-schema.sql` and the demo data in `02-seed.sql`. Docker Compose
and Kubernetes both create the database from these same two files.

## Docker Compose
- Image `postgres:16-alpine`; data lives in the named volume `postgres_data`, so it survives restarts and `docker compose down`.
- Any `*.sql` / `*.sh` file you put in `database/init/` runs **once**, the first time the database starts with an empty volume, in alphabetical order (e.g. `01-schema.sql`, `02-seed.sql`).
- After changing init scripts, wipe the volume so they run again: `docker compose down -v` (this deletes all data).
- Connect from your machine: `psql -h localhost -p 5432 -U <POSTGRES_USER> <POSTGRES_DB>` (values from your `.env`).

## Kubernetes
- PostgreSQL 16 runs as a CloudNativePG cluster (`kubernetes/database/cluster.yaml`): 1 primary and 2 streaming replicas, each with its own volume, with automatic failover. The backend connects through a PgBouncer pooler (`locker-db-pooler-rw`).
- `kustomization.yaml` packs `database/init/*.sql` into the `postgres-init` ConfigMap. The cluster runs them **once**, when it is first created, followed by `kubernetes/database/99-app-ownership.sql` (CloudNativePG runs init scripts as the `postgres` superuser, so that file hands every table to `locker_user`).
- If you add a new init file, list it in `kustomization.yaml` (`configMapGenerator` -> `postgres-init`) and in `kubernetes/database/cluster.yaml` (`postInitApplicationSQLRefs`), before `99-app-ownership.sql`.
- To recreate the database from the init scripts: `kubectl -n smart-locker delete cluster locker-db`, then `kubectl apply -k .` (deletes the data).
- SQL shell on the primary: `kubectl -n smart-locker exec -it $(kubectl -n smart-locker get cluster locker-db -o jsonpath='{.status.currentPrimary}') -c postgres -- psql -d locker_db`

## Migrations for existing databases
`database/init/` only runs on an empty volume. If your database already exists, apply the SQL in `database/migrations/` instead of wiping the volume. The scripts are idempotent.
- `001-assigned-parcels.sql` adds the `parcels` table, links reservations to parcels and drops the unused `unlock_password_hash` column.
  `docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < database/migrations/001-assigned-parcels.sql`

## Changing the PostgreSQL major version
Data files are not compatible across major versions. Dump the data (`pg_dump`), change the image tag, then restore, or wipe the volume if the data is disposable.
