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
import re
import time
from datetime import datetime, timedelta
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import locker_controller, stress
from app.luggage import require_admin_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/demo", tags=["admin demo"])

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
POD_NAME = os.getenv("HOSTNAME", "backend")
SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
K8S_HOST = os.getenv("KUBERNETES_SERVICE_HOST")
K8S_PORT = os.getenv("KUBERNETES_SERVICE_PORT", "443")
DB_CLUSTER = "locker-db"
CONFIG_ANNOTATION = "smartlocker.io/stress-config"
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
        content_type: str = "application/json", missing_ok: bool = False, raw: bool = False):
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
            payload = resp.read()
            return payload.decode(errors="replace") if raw else json.loads(payload or b"null")
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


def prom_at(query: str, at: float) -> Optional[float]:
    """Instant query evaluated at a past moment (unix seconds)."""
    url = f"{PROMETHEUS_URL}/api/v1/query?" + urllib.parse.urlencode({"query": query, "time": at})
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            result = json.loads(resp.read()).get("data", {}).get("result", [])
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    if not result or result[0]["value"][1] in ("NaN", "+Inf", "-Inf"):
        return None
    return float(result[0]["value"][1])


def prom_by_at(query: str, label: str, at: float) -> dict:
    url = f"{PROMETHEUS_URL}/api/v1/query?" + urllib.parse.urlencode({"query": query, "time": at})
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            result = json.loads(resp.read()).get("data", {}).get("result", [])
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return {}
    return {r["metric"].get(label, "?"): round(float(r["value"][1])) for r in result
            if r["value"][1] not in ("NaN", "+Inf", "-Inf") and float(r["value"][1]) >= 0.5}


def prom_range(query: str, start: float, end: float, step: int = 5) -> list[tuple[float, float]]:
    url = f"{PROMETHEUS_URL}/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": query, "start": start, "end": end, "step": step})
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            result = json.loads(resp.read()).get("data", {}).get("result", [])
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return []
    if not result:
        return []
    return [(float(t), float(v)) for t, v in result[0]["values"] if v not in ("NaN", "+Inf", "-Inf")]


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
    config = json.loads(job["metadata"].get("annotations", {}).get(CONFIG_ANNOTATION, "{}"))
    return {"name": job["metadata"]["name"], "state": state,
            "started": st.get("startTime"), "finished": st.get("completionTime"),
            "config": config}


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


# ---------------------------------------------------------------- stress test

SCENARIOS = ("browse", "parcel", "luggage", "churn")
CHAOS = ("faults", "controller", "failover")
# Virtual users per scenario for each intensity.
INTENSITY = {
    "low": {"browse": 10, "parcel": 4, "luggage": 2, "churn": 2},
    "medium": {"browse": 25, "parcel": 8, "luggage": 3, "churn": 4},
    "high": {"browse": 50, "parcel": 14, "luggage": 4, "churn": 6},
}


class StressRun(BaseModel):
    scenarios: list[str] = Field(min_length=1)
    chaos: list[str] = Field(default_factory=list)
    intensity: str = Field(default="medium", pattern="^(low|medium|high)$")
    minutes: int = Field(default=2, ge=1, le=5)
    # Overrides the browse users of the chosen intensity (Scalability page).
    browse_users: Optional[int] = Field(default=None, ge=5, le=80)


@router.post("/stress")
def start_stress(payload: StressRun, admin: dict = Depends(require_controls)):
    unknown = set(payload.scenarios) - set(SCENARIOS) | set(payload.chaos) - set(CHAOS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown option(s): {', '.join(sorted(unknown))}")
    current = _latest_loadtest()
    if current and current["state"] in ("running", "starting"):
        raise HTTPException(status_code=409, detail="A stress test is already running")

    vus = {name: INTENSITY[payload.intensity][name] for name in payload.scenarios}
    if payload.browse_users and "browse" in vus:
        vus["browse"] = payload.browse_users
    # A few more accounts than users so each journey has its own parcels.
    prepared = stress.prepare_run({name: n + 2 for name, n in vus.items()})
    duration = payload.minutes * 60
    config = {"scenarios": payload.scenarios, "chaos": payload.chaos, "intensity": payload.intensity,
              "minutes": payload.minutes, "vus": vus, "station_id": prepared["station_id"]}
    name = f"stress-{prepared['run']}"
    env = {
        "BASE_URL": f"http://frontend.{_namespace()}.svc.cluster.local",
        "SCENARIOS": ",".join(payload.scenarios),
        "CHAOS": ",".join(payload.chaos),
        "DURATION_S": str(duration),
        "VUS_JSON": json.dumps(vus),
        "USERS_JSON": json.dumps(prepared["users"]),
        "LABELS_JSON": json.dumps(prepared["labels"]),
        "STATION_ID": str(prepared["station_id"]),
        "ADMIN_TOKEN": prepared["runner_token"],
        "PASSWORD": prepared["password"],
    }
    job = {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": name, "labels": {"app": "k6", "started-by": "admin-dashboard"},
                     "annotations": {CONFIG_ANNOTATION: json.dumps(config)}},
        "spec": {
            # Kept for a day: the run report is built from this pod's log.
            "backoffLimit": 0, "ttlSecondsAfterFinished": 86400,
            "activeDeadlineSeconds": duration + 300,
            "template": {
                "metadata": {"labels": {"app": "k6", "stress-run": name}},
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [{
                        "name": "k6", "image": K6_IMAGE,
                        "args": ["run", "--no-usage-report", "--quiet", "/scripts/smart-locker.js"],
                        "env": [{"name": k, "value": v} for k, v in env.items()],
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
    logger.warning("Admin %s started stress test %s: %s", admin.get("username"), name, config)
    return {"started": name, **config}


@router.delete("/stress")
def stop_stress(admin: dict = Depends(require_controls)):
    """Stop the run, put any chaos back the way it was, and free the test lockers."""
    k8s("DELETE", f"/apis/batch/v1/namespaces/{_namespace()}/jobs?labelSelector=app%3Dk6",
        {"kind": "DeleteOptions", "apiVersion": "v1", "propagationPolicy": "Background"})
    controller = _controller_deployment()
    if controller["failure_rate"]:
        inject_faults(Faults(failure_rate=0), admin)
    if controller["replicas"] == 0:
        scale_controller(ControllerScale(replicas=2), admin)
    return {"stopped": True, **stress.reset()}


@router.post("/stress/reset")
def reset_stress(admin: dict = Depends(require_admin_user)):
    """Free every test locker (also called by the k6 job when it finishes)."""
    return stress.reset()


_LOGFMT_MSG = re.compile(r'msg="((?:[^"\\]|\\.)*)"')
_LOGFMT_TIME = re.compile(r'time="[^"]*T(\d\d:\d\d:\d\d)')


def _console(job_name: str, lines: int = 300) -> list[str]:
    """Readable lines from the k6 pod: its console.log output plus the summary."""
    pods = k8s("GET", f"/api/v1/namespaces/{_namespace()}/pods?"
               + urllib.parse.urlencode({"labelSelector": f"job-name={job_name}"})).get("items", [])
    if not pods:
        return []
    text = k8s("GET", f"/api/v1/namespaces/{_namespace()}/pods/{pods[0]['metadata']['name']}/log?tailLines={lines}",
               raw=True, missing_ok=True) or ""
    out = []
    for line in text.splitlines():
        line = line.rstrip()
        msg = _LOGFMT_MSG.search(line)
        if msg:
            when = _LOGFMT_TIME.search(line)
            body = msg.group(1).replace('\\"', '"')
            level = "ERROR " if "level=error" in line else ""
            out.append(f"{when.group(1) if when else ''} {level}{body}".strip())
        elif line.strip() and not line.lstrip().startswith(("running (", "default ", "execution:", "script:", "output:")):
            out.append(line)  # end-of-run summary
    return out[-120:]


@router.get("/stress")
def stress_status(admin: dict = Depends(require_admin_user)):
    run = _latest_loadtest() if in_kubernetes() else None
    since = None
    if run and run.get("started"):
        since = datetime.fromisoformat(run["started"].replace("Z", "+00:00"))
    elapsed = (datetime.now(since.tzinfo) - since).total_seconds() if since else None
    window = f"{max(60, int(elapsed or 60))}s"
    hpa = None
    if in_kubernetes():
        hpa = k8s("GET", f"/apis/autoscaling/v2/namespaces/{_namespace()}/horizontalpodautoscalers/backend",
                  missing_ok=True)
    return {
        "kubernetes": in_kubernetes(),
        "run": run,
        "elapsed_seconds": round(elapsed) if elapsed is not None else None,
        "lockers": stress.snapshot(since),
        "metrics": {
            "requests_per_second": prom_scalar('sum(rate(smartlocker_http_requests_total{route!~"/health.*"}[30s]))'),
            "error_rate_percent": prom_scalar(
                '100 * (sum(rate(smartlocker_http_requests_total{status=~"5.."}[30s])) or vector(0)) '
                '/ clamp_min(sum(rate(smartlocker_http_requests_total[30s])), 0.001)'),
            "p95_latency_seconds": prom_scalar(
                'histogram_quantile(0.95, sum by (le) (rate(smartlocker_http_request_duration_seconds_bucket{route!~"/health.*"}[30s])))'),
            "doors_opened": prom_scalar(f'sum(increase(locker_controller_unlocks_total{{outcome="opened"}}[{window}]))') if since else None,
            "backend_pods": (hpa or {}).get("status", {}).get("currentReplicas"),
            "backend_pods_desired": (hpa or {}).get("status", {}).get("desiredReplicas"),
        },
        "console": _console(run["name"]) if run else [],
    }


# ---------------------------------------------------------------- run reports
#
# A report for a finished stress run, built from three independent sources:
#   * k6 (the clients): its end-of-run summary and its timestamped console
#     lines, which give per-second timings of every failure and chaos step
#   * Prometheus (the servers): requests, 5xx, latency and pod counts over the
#     run and over each chaos window
#   * PostgreSQL: correctness checks (no compartment ever held twice, no lost
#     journeys, test lockers freed)
# and scored against the SLOs below. Saved in the stress_reports table.

SLOS = {
    "availability_normal": 99.9,      # % of requests OK outside chaos windows
    "availability_chaos": 97.0,       # % of requests OK during chaos windows
    "max_outage_seconds": 10,         # longest user-visible outage per chaos event
    "p95_normal_ms": 1000,            # p95 latency outside chaos windows
}

_LOG_LINE = re.compile(r'time="([^"]+)".*?msg="((?:[^"\\]|\\.)*)"')
_FAIL = re.compile(r"FAILED|failed \(|sign-in failed|booking failed|ERROR")
_OK = re.compile(r"-> collected|signed in and browsed|booked and cancelled \(ok\)|worked after")
_DURATION = re.compile(r"([\d.]+)(µs|us|ms|s|m)")


def _ms(text: str) -> Optional[float]:
    m = _DURATION.fullmatch(text.strip())
    if not m:
        return None
    value, unit = float(m.group(1)), m.group(2)
    return round(value * {"µs": 0.001, "us": 0.001, "ms": 1, "s": 1000, "m": 60000}[unit], 1)


def _k6_summary(text: str) -> dict:
    out = {}
    if m := re.search(r"http_reqs\.*:\s*(\d+)\s+([\d.]+)/s", text):
        out["requests"], out["requests_per_second"] = int(m.group(1)), round(float(m.group(2)), 1)
    if m := re.search(r"http_req_failed\.*:\s*([\d.]+)%\s+(\d+) out of (\d+)", text):
        out["failed"], out["failed_percent"] = int(m.group(2)), float(m.group(1))
        out["availability_percent"] = round(100 - float(m.group(1)), 2)
    if m := re.search(r"http_req_duration\.*:\s*avg=(\S+)\s+p\(50\)=(\S+)\s+p\(95\)=(\S+)\s+p\(99\)=(\S+)\s+max=(\S+)", text):
        out.update(avg_ms=_ms(m.group(1)), p50_ms=_ms(m.group(2)), p95_ms=_ms(m.group(3)),
                   p99_ms=_ms(m.group(4)), max_ms=_ms(m.group(5)))
    if m := re.search(r"checks_succeeded\.*:\s*([\d.]+)%", text):
        out["checks_percent"] = float(m.group(1))
    if m := re.search(r"iterations\.*:\s*(\d+)", text):
        out["iterations"] = int(m.group(1))
    return out


def _log_events(text: str) -> list[tuple[datetime, str]]:
    events = []
    for line in text.splitlines():
        m = _LOG_LINE.search(line)
        if m:
            events.append((datetime.fromisoformat(m.group(1).replace("Z", "+00:00")),
                           m.group(2).replace('\\"', '"')))
    return events


def _chaos_events(events) -> list[dict]:
    kinds = [("fails 40%", "faults_on"), ("faults cleared", "faults_off"),
             ("controller stopped", "controller_down"), ("started again", "controller_up"),
             ("killing the database primary", "failover")]
    out = []
    for t, text in events:
        if not text.startswith("[chaos]") or "retrying" in text or "FAILED" in text:
            continue
        for needle, kind in kinds:
            if needle in text:
                out.append({"t": t, "kind": kind})
    return out


def _window_stats(events, a: datetime, b: datetime, settle: float = 60) -> dict:
    """User-visible impact in [a, b], from the k6 console's per-second timestamps."""
    fails = [t for t, text in events if a <= t <= b and _FAIL.search(text)]
    if not fails:
        return {"failures": 0, "outage_seconds": 0, "recovered_after_seconds": 0}
    last = max(fails)
    # First successful journey after the last failure: service is back.
    recovered = next((t for t, text in events
                      if t > last and (t - b).total_seconds() <= settle and _OK.search(text)), None)
    return {
        "failures": len(fails),
        "outage_seconds": round((last - min(fails)).total_seconds()) + 1,
        "first_failure_after_seconds": round((min(fails) - a).total_seconds()),
        "recovered_after_seconds": round((recovered - a).total_seconds()) if recovered else None,
    }


def _server_window(a: datetime, b: datetime) -> dict:
    seconds = max(1, int((b - a).total_seconds()))
    end = b.timestamp()
    total = prom_at(f'sum(increase(smartlocker_http_requests_total{{route!~"/health.*"}}[{seconds}s]))', end)
    errors = prom_at(f'sum(increase(smartlocker_http_requests_total{{route!~"/health.*",status=~"5.."}}[{seconds}s]))', end) or 0
    p95 = prom_at('histogram_quantile(0.95, sum by (le) (increase('
                  f'smartlocker_http_request_duration_seconds_bucket{{route!~"/health.*"}}[{seconds}s])))', end)
    return {
        "requests": round(total) if total is not None else None,
        "errors_5xx": round(errors),
        "availability_percent": round(100 * (1 - errors / total), 2) if total else None,
        "p95_ms": round(p95 * 1000) if p95 is not None else None,
    }


def build_report(run: dict) -> dict:
    started = datetime.fromisoformat(run["started"].replace("Z", "+00:00"))
    finished = datetime.fromisoformat((run.get("finished") or run["started"]).replace("Z", "+00:00"))
    config = run.get("config") or {}
    text = ""
    pods = k8s("GET", f"/api/v1/namespaces/{_namespace()}/pods?"
               + urllib.parse.urlencode({"labelSelector": f"job-name={run['name']}"})).get("items", [])
    if pods:
        text = k8s("GET", f"/api/v1/namespaces/{_namespace()}/pods/{pods[0]['metadata']['name']}/log",
                   raw=True, missing_ok=True) or ""
    events = _log_events(text)
    chaos = _chaos_events(events)
    first = lambda kind: next((c["t"] for c in chaos if c["kind"] == kind), None)

    # ---- incidents, one per chaos event
    incidents = []
    if t := first("faults_on"):
        off = first("faults_off") or finished
        injected = prom_at(f'sum(increase(locker_controller_unlocks_total{{outcome="injected_failure"}}[{max(1, int((off - t).total_seconds()))}s]))', off.timestamp())
        unlock_fail = [x for x in events if t <= x[0] <= off and "unlock" in x[1] and ("FAILED" in x[1] or "failed (" in x[1])]
        incidents.append({"kind": "faults", "label": "40% controller faults", "at_seconds": round((t - started).total_seconds()),
                          "duration_seconds": round((off - t).total_seconds()),
                          "injected_faults": round(injected) if injected is not None else None,
                          "unlocks_failed_for_users": len(unlock_fail),
                          **{k: v for k, v in _window_stats(events, t, off).items() if k == "failures"}})
    if t := first("controller_down"):
        up = first("controller_up") or finished
        # Stop at the next chaos event, so a later failover is not counted here.
        next_event = min([c["t"] for c in chaos if c["t"] > up] + [finished, up + timedelta(seconds=45)])
        stats = _window_stats(events, t, next_event)
        # Recovery = from restore to the first door that opens again.
        reopened = next((x for x, text in events
                         if x > up and ("door opened by" in text or "worked after" in text)), None)
        seconds = max(1, int((up - t).total_seconds()))
        blast = prom_by_at(f'sum by (route) (increase(smartlocker_http_requests_total{{status=~"5.."}}[{seconds}s]))', "route", up.timestamp())
        door_routes = ("/unlock", "/api/luggage/sessions")
        incidents.append({"kind": "controller", "label": "Door controller outage", "at_seconds": round((t - started).total_seconds()),
                          "duration_seconds": round((up - t).total_seconds()), **stats,
                          "recovered_after_restore_seconds": round((reopened - up).total_seconds()) if reopened else None,
                          "errors_by_route": blast,
                          "isolated": all(any(d in r for d in door_routes) for r in blast)})
    if t := first("failover"):
        later = [c["t"] for c in chaos if c["t"] > t]
        end = min([finished] + later + [t + timedelta(seconds=90)])
        stats = _window_stats(events, t, end)
        incidents.append({"kind": "failover", "label": "Database primary killed", "at_seconds": round((t - started).total_seconds()),
                          **stats, "errors_by_route": prom_by_at(
                              f'sum by (route) (increase(smartlocker_http_requests_total{{status=~"5.."}}[{max(1, int((end - t).total_seconds()))}s]))',
                              "route", end.timestamp())})

    # ---- phases: before / during / after chaos (server side)
    phases = []
    if chaos:
        chaos_start = min(c["t"] for c in chaos)
        chaos_end = min(finished, max(c["t"] for c in chaos) + timedelta(seconds=30))
        for name, a, b in (("before chaos", started, chaos_start), ("during chaos", chaos_start, chaos_end),
                           ("after chaos", chaos_end, finished)):
            if (b - a).total_seconds() >= 15:
                phases.append({"name": name, "from_seconds": round((a - started).total_seconds()),
                               "to_seconds": round((b - started).total_seconds()), **_server_window(a, b)})
    else:
        phases.append({"name": "whole run", "from_seconds": 0,
                       "to_seconds": round((finished - started).total_seconds()), **_server_window(started, finished)})

    # ---- scaling
    pods_series = prom_range('count(up{app="backend"} == 1)', started.timestamp(), finished.timestamp())
    scaling = None
    if pods_series:
        initial = pods_series[0][1]
        peak = max(v for _, v in pods_series)
        scaling = {"initial_pods": int(initial), "peak_pods": int(peak),
                   "first_scale_out_seconds": next((round(t - started.timestamp()) for t, v in pods_series if v > initial), None),
                   "peak_reached_seconds": next((round(t - started.timestamp()) for t, v in pods_series if v == peak), None)}
    rps_series = prom_range('sum(rate(smartlocker_http_requests_total{route!~"/health.*"}[30s]))',
                            started.timestamp(), finished.timestamp())

    # ---- journeys and correctness
    seen_parcels = sum(1 for _, x in events if x.startswith("[parcel]") and "-> collected" in x)
    seen_luggage = sum(1 for _, x in events if x.startswith("[luggage]") and "-> collected" in x)
    db = stress.correctness(started, finished)
    journeys = {
        "parcels_collected_seen_by_clients": seen_parcels,
        "parcels_collected_in_database": db.get("parcels_collected"),
        "luggage_collected_seen_by_clients": seen_luggage,
        "luggage_collected_in_database": db.get("luggage_collected"),
        "lost": max(0, seen_parcels - (db.get("parcels_collected") or 0)) + max(0, seen_luggage - (db.get("luggage_collected") or 0)),
        "unlock_retries": sum(1 for _, x in events if "retrying" in x and "unlock" in x),
        "unlocks_recovered_by_retry": sum(1 for _, x in events if "worked after" in x),
        "unlocks_given_up": sum(1 for _, x in events if "FAILED for 45 s" in x),
    }

    # ---- SLO scorecard
    normal = [p for p in phases if p["name"] != "during chaos"]
    normal_req = sum(p["requests"] or 0 for p in normal)
    normal_err = sum(p["errors_5xx"] or 0 for p in normal)
    slo = []

    def add(name, target, measured, ok, unit=""):
        slo.append({"name": name, "target": target, "measured": measured, "unit": unit,
                    "pass": None if measured is None else bool(ok)})

    avail_normal = round(100 * (1 - normal_err / normal_req), 2) if normal_req else None
    add("Availability outside chaos", f"≥ {SLOS['availability_normal']}%", avail_normal,
        avail_normal is not None and avail_normal >= SLOS["availability_normal"], "%")
    during = next((p for p in phases if p["name"] == "during chaos"), None)
    if during:
        add("Availability during chaos", f"≥ {SLOS['availability_chaos']}%", during["availability_percent"],
            during["availability_percent"] is not None and during["availability_percent"] >= SLOS["availability_chaos"], "%")
    outages = [i["outage_seconds"] for i in incidents if i["kind"] in ("controller", "failover") and "outage_seconds" in i]
    if outages:
        worst = max(outages)
        # The controller outage lasts as long as the test keeps it down; what
        # matters is how fast things work again once it is back.
        ctl = next((i for i in incidents if i["kind"] == "controller"), None)
        fo = next((i for i in incidents if i["kind"] == "failover"), None)
        if fo:
            add("Outage after DB primary killed", f"≤ {SLOS['max_outage_seconds']} s", fo.get("outage_seconds"),
                fo.get("outage_seconds", 0) <= SLOS["max_outage_seconds"], "s")
        if ctl:
            r = ctl.get("recovered_after_restore_seconds")
            add("Unlocks working again after controller restore", f"≤ {SLOS['max_outage_seconds']} s", r,
                r is not None and r <= SLOS["max_outage_seconds"], "s")
            add("Controller outage only affected door unlocks", "yes", "yes" if ctl["isolated"] else "no", ctl["isolated"])
    f = next((i for i in incidents if i["kind"] == "faults"), None)
    if f:
        add("Unlock failures seen by users during 40% faults", "0", f["unlocks_failed_for_users"], f["unlocks_failed_for_users"] == 0)
    p95_normal = max((p["p95_ms"] for p in normal if p["p95_ms"] is not None), default=None)
    add("p95 latency outside chaos", f"≤ {SLOS['p95_normal_ms']} ms", p95_normal,
        p95_normal is not None and p95_normal <= SLOS["p95_normal_ms"], "ms")
    add("Compartments ever held by two bookings", "0", db.get("double_allocations"), db.get("double_allocations") == 0)
    add("Completed journeys missing from the database", "0", journeys["lost"], journeys["lost"] == 0)
    add("Test lockers still held after the run", "0", db.get("lockers_still_held"), db.get("lockers_still_held") == 0)

    client = _k6_summary(text)
    report = {
        "run": run["name"], "started": started.isoformat(), "finished": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds()),
        "config": config, "client": client, "phases": phases, "incidents": incidents,
        "scaling": scaling,
        "peak_requests_per_second": round(max(v for _, v in rps_series), 1) if rps_series else None,
        "journeys": journeys, "correctness": db, "slo": slo,
    }
    scored = [x for x in slo if x["pass"] is not None]
    report["summary"] = {
        "availability_percent": client.get("availability_percent"),
        "requests": client.get("requests"),
        "p95_ms": client.get("p95_ms"),
        "peak_pods": scaling["peak_pods"] if scaling else None,
        "slo_passed": sum(1 for x in scored if x["pass"]), "slo_total": len(scored),
    }
    return report


@router.get("/stress/report")
def stress_report(run: Optional[str] = None, refresh: bool = False, admin: dict = Depends(require_admin_user)):
    """Report for a finished run (the latest one by default). Built once, then stored."""
    if run and not refresh:
        stored = stress.get_report(run)
        if stored:
            return stored
    latest = _latest_loadtest() if in_kubernetes() else None
    if latest is None or (run and latest["name"] != run):
        if run:
            stored = stress.get_report(run)
            if stored:
                return stored
        raise HTTPException(status_code=404, detail="No report for that run (reports are built for runs on Kubernetes)")
    if latest["state"] not in ("succeeded", "failed"):
        raise HTTPException(status_code=409, detail="The run has not finished yet")
    if not refresh and (stored := stress.get_report(latest["name"])):
        return stored
    report = build_report(latest)
    stress.save_report(latest["name"], datetime.fromisoformat(latest["started"].replace("Z", "+00:00")),
                       datetime.fromisoformat(latest["finished"].replace("Z", "+00:00")) if latest.get("finished") else None,
                       report)
    return report


@router.get("/stress/reports")
def stress_reports(admin: dict = Depends(require_admin_user)):
    return stress.list_reports()
