"""Admin "System tests" for the cloud demo: load balancing, scalability, fault tolerance.

Read-only views (always on, admin only):
  * numbers from Prometheus (fixed, named queries; no free-form PromQL)
  * state from the Kubernetes API: pods, the autoscaler, the database cluster,
    load-test jobs. Needs only the read-only Role in kubernetes/backend/rbac.yaml.

Actions (start a load test, kill the database primary, stop the controller,
inject faults) change the cluster. They only work when the caller is an ADMIN
and the backend runs in Kubernetes with the extra Role from
kubernetes/demo-controls/ (`make k8s-demo-on`). The backend asks Kubernetes
whether it currently has that permission, so the Role itself is the on/off
switch. A real deployment would never give the API these permissions; this
is for demonstrating the system on a local cluster.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import locker_controller
from app.luggage import require_admin_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/demo", tags=["admin demo"])

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
POD_NAME = os.getenv("HOSTNAME", "backend")
SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
K8S_HOST = os.getenv("KUBERNETES_SERVICE_HOST")
K8S_PORT = os.getenv("KUBERNETES_SERVICE_PORT", "443")
DB_CLUSTER = "locker-db"
K6_IMAGE = "grafana/k6:1.8.2"


# ---------------------------------------------------------------- helpers

def in_kubernetes() -> bool:
    return bool(K8S_HOST) and (SA_DIR / "token").exists()


def _namespace() -> str:
    try:
        return (SA_DIR / "namespace").read_text().strip()
    except OSError:
        return "smart-locker"


def k8s(method: str, path: str, body: Optional[dict] = None,
        content_type: str = "application/json", missing_ok: bool = False):
    """Minimal Kubernetes API call using the pod's service account."""
    if not in_kubernetes():
        raise HTTPException(status_code=409, detail="Only available when running in Kubernetes")
    url = f"https://{K8S_HOST}:{K8S_PORT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {(SA_DIR / 'token').read_text().strip()}",
        "Content-Type": content_type,
        "Accept": "application/json",
    })
    ctx = ssl.create_default_context(cafile=str(SA_DIR / "ca.crt"))
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
            return json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and missing_ok:
            return None
        detail = exc.read().decode(errors="replace")[:300]
        logger.warning("Kubernetes API %s %s -> %s %s", method, path, exc.code, detail)
        if exc.code == 403:
            raise HTTPException(status_code=403, detail="The backend's service account is not allowed to do this")
        raise HTTPException(status_code=502, detail=f"Kubernetes API error {exc.code}")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail=f"Kubernetes API unreachable: {exc}")


def prom(query: str) -> Optional[list]:
    """Instant Prometheus query. None if Prometheus is not running."""
    url = f"{PROMETHEUS_URL}/api/v1/query?" + urllib.parse.urlencode({"query": query})
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    return [
        {"labels": r["metric"], "value": float(r["value"][1])}
        for r in payload.get("data", {}).get("result", [])
        if r["value"][1] not in ("NaN", "+Inf", "-Inf")
    ]


def prom_scalar(query: str) -> Optional[float]:
    rows = prom(query)
    return rows[0]["value"] if rows else None


def prom_by(query: str, label: str) -> Optional[dict]:
    rows = prom(query)
    if rows is None:
        return None
    return {r["labels"].get(label, "?"): round(r["value"], 3) for r in rows}


_controls_cache = {"at": 0.0, "allowed": False}


def controls_enabled() -> bool:
    """True if the backend may delete pods here, i.e. the demo Role is bound.

    Uses a SelfSubjectAccessReview, which every service account may create.
    Cached for a few seconds.
    """
    if not in_kubernetes():
        return False
    now = time.monotonic()
    if now - _controls_cache["at"] < 5:
        return _controls_cache["allowed"]
    review = {
        "apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectAccessReview",
        "spec": {"resourceAttributes": {"namespace": _namespace(), "verb": "delete", "resource": "pods"}},
    }
    try:
        allowed = bool(k8s("POST", "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews",
                           review)["status"].get("allowed"))
    except HTTPException:
        allowed = False
    _controls_cache.update(at=now, allowed=allowed)
    return allowed


def require_controls(admin: dict = Depends(require_admin_user)) -> dict:
    if not in_kubernetes():
        raise HTTPException(status_code=409, detail="Demo actions only work in Kubernetes")
    if not controls_enabled():
        raise HTTPException(
            status_code=403,
            detail="Demo actions are switched off. Turn them on with: make k8s-demo-on",
        )
    return admin


def _pods(selector: str) -> list[dict]:
    data = k8s("GET", f"/api/v1/namespaces/{_namespace()}/pods?"
               + urllib.parse.urlencode({"labelSelector": selector}))
    pods = []
    for item in data.get("items", []):
        meta, status = item["metadata"], item.get("status", {})
        statuses = status.get("containerStatuses") or []
        ready = bool(statuses) and all(c.get("ready") for c in statuses)
        terminating = meta.get("deletionTimestamp") is not None
        pods.append({
            "name": meta["name"],
            "phase": "Terminating" if terminating else status.get("phase"),
            "ready": ready and not terminating,
            "restarts": sum(c.get("restartCount", 0) for c in statuses),
            "node": item.get("spec", {}).get("nodeName"),
            "role": meta.get("labels", {}).get("cnpg.io/instanceRole"),
            "created": meta.get("creationTimestamp"),
        })
    return sorted(pods, key=lambda p: p["name"])


# ---------------------------------------------------------------- general

@router.get("/status")
def demo_status(admin: dict = Depends(require_admin_user)):
    return {
        "kubernetes": in_kubernetes(),
        "demo_controls": controls_enabled(),
        "prometheus": prom_scalar("vector(1)") is not None,
        "grafana_path": "/grafana/",
        "backend_pod": POD_NAME,
    }


@router.get("/whoami")
def whoami(admin: dict = Depends(require_admin_user)):
    """Which backend pod served this request (used to show HTTP load balancing)."""
    return {"pod": POD_NAME}


# ---------------------------------------------------------------- load balancing

@router.get("/balancing")
def balancing(admin: dict = Depends(require_admin_user)):
    view = {
        "requests_per_backend_pod": prom_by(
            'sum by (pod) (rate(smartlocker_http_requests_total{route!~"/health.*"}[1m]))', "pod"),
        "unlocks_per_controller_pod": prom_by(
            'sum by (pod) (rate(locker_controller_unlocks_total{outcome="opened"}[5m]))', "pod"),
        "backend_pods": None,
        "controller_pods": None,
    }
    if in_kubernetes():
        view["backend_pods"] = _pods("app=backend")
        view["controller_pods"] = _pods("app=locker-controller")
    return view


class GrpcBurst(BaseModel):
    calls: int = Field(default=30, ge=1, le=100)


@router.post("/balancing/grpc")
def grpc_burst(payload: GrpcBurst, admin: dict = Depends(require_admin_user)):
    """Make N gRPC calls to the controller and count which replica answered each."""
    started = time.perf_counter()
    replies = locker_controller.controller_info(samples=payload.calls)
    counts: dict[str, int] = {}
    errors = 0
    for reply in replies:
        if "controller" in reply:
            counts[reply["controller"]] = counts.get(reply["controller"], 0) + 1
        else:
            errors += 1
    return {
        "calls": payload.calls,
        "per_replica": counts,
        "errors": errors,
        "via": "grpc" if locker_controller.ADDRESS else "in-process",
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


# ---------------------------------------------------------------- scalability

def _latest_loadtest():
    data = k8s("GET", f"/apis/batch/v1/namespaces/{_namespace()}/jobs?labelSelector=app%3Dk6")
    jobs = sorted(data.get("items", []), key=lambda j: j["metadata"]["creationTimestamp"])
    if not jobs:
        return None
    job = jobs[-1]
    st = job.get("status", {})
    state = ("running" if st.get("active") else
             "succeeded" if st.get("succeeded") else
             "failed" if st.get("failed") else "starting")
    env = {e["name"]: e.get("value") for e in job["spec"]["template"]["spec"]["containers"][0].get("env", [])}
    return {"name": job["metadata"]["name"], "state": state,
            "started": st.get("startTime"), "finished": st.get("completionTime"),
            "peak_users": env.get("PEAK_USERS"), "hold": env.get("HOLD")}


@router.get("/scalability")
def scalability(admin: dict = Depends(require_admin_user)):
    view = {
        "requests_per_second": prom_scalar('sum(rate(smartlocker_http_requests_total{route!~"/health.*"}[1m]))'),
        "p95_latency_seconds": prom_scalar(
            'histogram_quantile(0.95, sum by (le) (rate(smartlocker_http_request_duration_seconds_bucket{route!~"/health.*"}[1m])))'),
        "error_rate_percent": prom_scalar(
            '100 * (sum(rate(smartlocker_http_requests_total{status=~"5.."}[1m])) or vector(0)) '
            '/ clamp_min(sum(rate(smartlocker_http_requests_total[1m])), 0.001)'),
        "cpu_per_pod": prom_by('rate(process_cpu_seconds_total{app="backend"}[1m])', "pod"),
        "hpa": None, "backend_pods": None, "load_test": None,
    }
    if in_kubernetes():
        hpa = k8s("GET", f"/apis/autoscaling/v2/namespaces/{_namespace()}/horizontalpodautoscalers/backend",
                  missing_ok=True)
        if hpa:
            current_cpu = next((m["resource"]["current"].get("averageUtilization")
                                for m in hpa.get("status", {}).get("currentMetrics") or []
                                if m.get("type") == "Resource"), None)
            view["hpa"] = {
                "min": hpa["spec"].get("minReplicas"), "max": hpa["spec"]["maxReplicas"],
                "current": hpa.get("status", {}).get("currentReplicas"),
                "desired": hpa.get("status", {}).get("desiredReplicas"),
                "cpu_percent": current_cpu,
                "target_percent": hpa["spec"]["metrics"][0]["resource"]["target"].get("averageUtilization"),
            }
        view["backend_pods"] = _pods("app=backend")
        view["load_test"] = _latest_loadtest()
    return view


class LoadTest(BaseModel):
    peak_users: int = Field(default=40, ge=5, le=80)
    hold_minutes: int = Field(default=2, ge=1, le=5)


@router.post("/scalability/loadtest")
def start_loadtest(payload: LoadTest, admin: dict = Depends(require_controls)):
    current = _latest_loadtest()
    if current and current["state"] in ("running", "starting"):
        raise HTTPException(status_code=409, detail="A load test is already running")
    name = f"k6-load-test-{int(time.time())}"
    job = {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": name, "labels": {"app": "k6", "started-by": "admin-dashboard"}},
        "spec": {
            "backoffLimit": 0, "ttlSecondsAfterFinished": 1800,
            "template": {
                "metadata": {"labels": {"app": "k6"}},
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [{
                        "name": "k6", "image": K6_IMAGE,
                        "args": ["run", "--no-usage-report", "/scripts/smart-locker.js"],
                        "env": [
                            {"name": "BASE_URL", "value": f"http://frontend.{_namespace()}.svc.cluster.local"},
                            {"name": "PEAK_USERS", "value": str(payload.peak_users)},
                            {"name": "HOLD", "value": f"{payload.hold_minutes}m"},
                        ],
                        "volumeMounts": [{"name": "scripts", "mountPath": "/scripts"}],
                        "resources": {"requests": {"cpu": "200m", "memory": "128Mi"},
                                      "limits": {"cpu": "1", "memory": "512Mi"}},
                    }],
                    "volumes": [{"name": "scripts", "configMap": {"name": "k6-script"}}],
                },
            },
        },
    }
    k8s("POST", f"/apis/batch/v1/namespaces/{_namespace()}/jobs", job)
    logger.warning("Admin %s started load test %s", admin.get("username"), name)
    return {"started": name, "peak_users": payload.peak_users, "hold_minutes": payload.hold_minutes}


@router.delete("/scalability/loadtest")
def stop_loadtest(admin: dict = Depends(require_controls)):
    k8s("DELETE", f"/apis/batch/v1/namespaces/{_namespace()}/jobs?labelSelector=app%3Dk6",
        {"kind": "DeleteOptions", "apiVersion": "v1", "propagationPolicy": "Background"})
    return {"stopped": True}


# ---------------------------------------------------------------- fault tolerance

def _controller_deployment() -> dict:
    dep = k8s("GET", f"/apis/apps/v1/namespaces/{_namespace()}/deployments/locker-controller")
    container = dep["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container.get("env", [])}
    return {"replicas": dep["spec"].get("replicas"),
            "ready": dep.get("status", {}).get("readyReplicas", 0),
            "failure_rate": float(env.get("FAILURE_RATE") or 0)}


@router.get("/fault")
def fault(admin: dict = Depends(require_admin_user)):
    view = {
        "database_reachable": prom_scalar("max(smartlocker_database_up)"),
        "error_rate_percent": prom_scalar(
            '100 * (sum(rate(smartlocker_http_requests_total{status=~"5.."}[1m])) or vector(0)) '
            '/ clamp_min(sum(rate(smartlocker_http_requests_total[1m])), 0.001)'),
        "unlock_results": prom_by('sum by (outcome) (rate(smartlocker_controller_calls_total[5m]))', "outcome"),
        "injected_faults_per_second": prom_scalar(
            'sum(rate(locker_controller_unlocks_total{outcome="injected_failure"}[1m]))'),
        "database": None, "database_pods": None, "controller": None, "controller_pods": None,
    }
    if in_kubernetes():
        cluster = k8s("GET", f"/apis/postgresql.cnpg.io/v1/namespaces/{_namespace()}/clusters/{DB_CLUSTER}",
                      missing_ok=True)
        if cluster:
            st = cluster.get("status", {})
            view["database"] = {"phase": st.get("phase"), "primary": st.get("currentPrimary"),
                                "ready": st.get("readyInstances"), "instances": st.get("instances")}
        view["database_pods"] = _pods(f"cnpg.io/cluster={DB_CLUSTER},cnpg.io/podRole=instance")
        view["controller"] = _controller_deployment()
        view["controller_pods"] = _pods("app=locker-controller")
    return view


@router.post("/fault/failover")
def kill_primary(admin: dict = Depends(require_controls)):
    cluster = k8s("GET", f"/apis/postgresql.cnpg.io/v1/namespaces/{_namespace()}/clusters/{DB_CLUSTER}")
    primary = cluster.get("status", {}).get("currentPrimary")
    if not primary:
        raise HTTPException(status_code=409, detail="The database cluster has no primary right now")
    k8s("DELETE", f"/api/v1/namespaces/{_namespace()}/pods/{primary}")
    logger.warning("Admin %s deleted database primary %s", admin.get("username"), primary)
    return {"deleted": primary}


class ControllerScale(BaseModel):
    replicas: int = Field(ge=0, le=4)


@router.post("/fault/controller")
def scale_controller(payload: ControllerScale, admin: dict = Depends(require_controls)):
    k8s("PATCH", f"/apis/apps/v1/namespaces/{_namespace()}/deployments/locker-controller/scale",
        {"spec": {"replicas": payload.replicas}}, content_type="application/merge-patch+json")
    logger.warning("Admin %s scaled locker-controller to %s", admin.get("username"), payload.replicas)
    return {"replicas": payload.replicas}


class Faults(BaseModel):
    failure_rate: float = Field(ge=0, le=0.9)


@router.post("/fault/faults")
def inject_faults(payload: Faults, admin: dict = Depends(require_controls)):
    patch = {"spec": {"template": {"spec": {"containers": [{
        "name": "locker-controller",
        "env": [{"name": "FAILURE_RATE", "value": str(payload.failure_rate)}],
    }]}}}}
    k8s("PATCH", f"/apis/apps/v1/namespaces/{_namespace()}/deployments/locker-controller",
        patch, content_type="application/strategic-merge-patch+json")
    logger.warning("Admin %s set controller FAILURE_RATE=%s", admin.get("username"), payload.failure_rate)
    return {"failure_rate": payload.failure_rate}
