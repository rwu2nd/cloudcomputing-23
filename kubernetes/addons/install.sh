#!/bin/sh
# Install the cluster add-ons the manifests rely on. Run once per cluster,
# from the repository root, before `kubectl apply -k .`:
#
#     sh kubernetes/addons/install.sh
#
#   metrics-server   CPU/memory metrics; the HorizontalPodAutoscaler needs it.
#   CloudNativePG    operator that runs PostgreSQL as a replicated cluster
#                    (kubernetes/database/cluster.yaml).
#
# Safe to re-run. Windows without Git Bash: run the kubectl lines by hand.

set -eu

METRICS_SERVER_VERSION="v0.9.1"
CNPG_VERSION="1.30.1"
CNPG_MANIFEST="https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-${CNPG_VERSION%.*}/releases/cnpg-${CNPG_VERSION}.yaml"

CONTEXT="$(kubectl config current-context)"
echo "Installing add-ons into kubectl context: $CONTEXT"

# --- metrics-server ---------------------------------------------------------
if kubectl get apiservice v1beta1.metrics.k8s.io >/dev/null 2>&1; then
  echo "metrics-server: already installed"
elif command -v minikube >/dev/null 2>&1 && minikube -p "$CONTEXT" status >/dev/null 2>&1; then
  # minikube ships metrics-server as an add-on.
  minikube -p "$CONTEXT" addons enable metrics-server
else
  kubectl apply -f "https://github.com/kubernetes-sigs/metrics-server/releases/download/${METRICS_SERVER_VERSION}/components.yaml"
  # Local clusters (Docker Desktop, kind) use self-signed kubelet certificates.
  kubectl -n kube-system patch deployment metrics-server --type=json \
    -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
fi

# --- CloudNativePG operator --------------------------------------------------
# Server-side apply: the CRDs are too large for a client-side apply annotation.
kubectl apply --server-side --force-conflicts -f "$CNPG_MANIFEST"

echo "Waiting for the add-ons to be ready..."
kubectl -n cnpg-system rollout status deployment/cnpg-controller-manager --timeout=180s
kubectl -n kube-system rollout status deployment/metrics-server --timeout=180s
echo "Add-ons ready."
