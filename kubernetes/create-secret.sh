#!/bin/sh
# Create (or update) the database Secrets from your local .env, so the
# credentials never appear in a manifest or in git:
#   locker-db-secret  POSTGRES_USER / POSTGRES_PASSWORD, read by the backend
#   locker-db-app     the same values as a basic-auth Secret, read by the
#                     CloudNativePG cluster to create the application user
#
# Usage (from the repository root):
#     sh kubernetes/create-secret.sh            # reads ./.env
#     sh kubernetes/create-secret.sh path/to/other.env
#     NAMESPACE=my-ns sh kubernetes/create-secret.sh
#
# Safe to re-run: it updates the Secret if it already exists.
# The database cluster reads the password when it is first created. To change
# it later, see Troubleshooting in the README.

set -eu

ENV_FILE="${1:-.env}"
NAMESPACE="${NAMESPACE:-smart-locker}"
SECRET_NAME="locker-db-secret"
APP_SECRET_NAME="locker-db-app"
# Must match `owner:` in kubernetes/database/cluster.yaml.
EXPECTED_DB_USER="locker_user"

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

DB_USER="$(sed -n 's/^POSTGRES_USER=//p' "$TMP_FILE")"
if [ "$DB_USER" != "$EXPECTED_DB_USER" ]; then
  echo "ERROR: POSTGRES_USER is '$DB_USER' but Kubernetes expects '$EXPECTED_DB_USER'" >&2
  echo "       (the database owner in kubernetes/database/cluster.yaml). Change one to match." >&2
  exit 1
fi

# Create the namespace if needed (harmless if it already exists).
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

# Build the Secret client-side and apply it, which also works for updates.
kubectl --namespace "$NAMESPACE" create secret generic "$SECRET_NAME" \
  --from-env-file="$TMP_FILE" --dry-run=client -o yaml | kubectl apply -f -

# Same credentials as a basic-auth Secret for CloudNativePG. Written to files
# so the password never appears on a command line.
CRED_DIR="$(mktemp -d)"
trap 'rm -f "$TMP_FILE"; rm -rf "$CRED_DIR"' EXIT
printf '%s' "$DB_USER" > "$CRED_DIR/username"
sed -n 's/^POSTGRES_PASSWORD=//p' "$TMP_FILE" | tr -d '\n' > "$CRED_DIR/password"
kubectl --namespace "$NAMESPACE" create secret generic "$APP_SECRET_NAME" \
  --type=kubernetes.io/basic-auth \
  --from-file=username="$CRED_DIR/username" --from-file=password="$CRED_DIR/password" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Secrets $SECRET_NAME and $APP_SECRET_NAME are ready in namespace $NAMESPACE."
