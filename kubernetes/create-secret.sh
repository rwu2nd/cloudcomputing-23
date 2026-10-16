#!/bin/sh
# Create (or update) the Kubernetes Secret "locker-db-secret" from your local .env,
# so the database credentials never appear in a manifest or in git.
#
# Usage (from the repository root):
#     sh kubernetes/create-secret.sh            # reads ./.env
#     sh kubernetes/create-secret.sh path/to/other.env
#     NAMESPACE=my-ns sh kubernetes/create-secret.sh
#
# Safe to re-run: it updates the Secret if it already exists.
# After changing the password, also restart the pods:
#     kubectl -n smart-locker rollout restart deployment/backend deployment/postgres
# (Postgres only reads POSTGRES_PASSWORD when it first initialises an empty volume;
#  see Troubleshooting in the README if you change it later.)

set -eu

ENV_FILE="${1:-.env}"
NAMESPACE="${NAMESPACE:-smart-locker}"
SECRET_NAME="locker-db-secret"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE not found. Copy .env.example to .env and set POSTGRES_PASSWORD first." >&2
  exit 1
fi

TMP_FILE="$(mktemp)"
trap 'rm -f "$TMP_FILE"' EXIT

# Take only the two credential lines (and drop Windows line endings).
grep -E '^(POSTGRES_USER|POSTGRES_PASSWORD)=' "$ENV_FILE" | tr -d '\r' > "$TMP_FILE" || true

for KEY in POSTGRES_USER POSTGRES_PASSWORD; do
  if ! grep -Eq "^${KEY}=.+" "$TMP_FILE"; then
    echo "ERROR: ${KEY} is missing or empty in $ENV_FILE." >&2
    exit 1
  fi
done

# Create the namespace if needed (harmless if it already exists).
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

# Build the Secret client-side and apply it, which also works for updates.
kubectl --namespace "$NAMESPACE" create secret generic "$SECRET_NAME" \
  --from-env-file="$TMP_FILE" --dry-run=client -o yaml | kubectl apply -f -

echo "Secret $SECRET_NAME is ready in namespace $NAMESPACE."
