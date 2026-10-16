# Database (PostgreSQL) - schema owned by the database teammate

The **infrastructure** (container, persistent volume, credentials, health check) is set up.
The **schema is not**: no tables, migrations or seed data exist yet.

## Docker Compose
- Image `postgres:16-alpine`; data lives in the named volume `postgres_data`, so it survives restarts and `docker compose down`.
- Any `*.sql` / `*.sh` file you put in `database/init/` runs **once**, the first time the database starts with an empty volume, in alphabetical order (e.g. `01-schema.sql`, `02-seed.sql`).
- After changing init scripts, wipe the volume so they run again: `docker compose down -v` (this deletes all data).
- Connect from your machine: `psql -h localhost -p 5432 -U <POSTGRES_USER> <POSTGRES_DB>` (values from your `.env`).

## Kubernetes
- Postgres runs as a Deployment with a PersistentVolumeClaim (`postgres-data`).
- `database/init/` is **not** mounted in Kubernetes. Agree with the backend teammate how the schema is created there, for example:
  - run migrations from the backend at startup or as a one-off command (Alembic is a common choice with FastAPI), or
  - load the init scripts into a ConfigMap and mount it at `/docker-entrypoint-initdb.d` in `kubernetes/postgres/deployment.yaml`.

Whichever you pick, tell the team so Compose and Kubernetes create the same schema.

## Changing the PostgreSQL major version
Data files are not compatible across major versions. Dump the data (`pg_dump`), change the image tag, then restore, or wipe the volume if the data is disposable.
