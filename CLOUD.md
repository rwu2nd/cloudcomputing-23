# Cloud computing in the Smart Locker system

This page explains which cloud and distributed-systems concepts the project shows, where each one
lives in the code, and how to demonstrate them. Setup instructions are in the [README](README.md)
(Compose: section 4, Kubernetes: section 7).

## Architecture

```
                         ┌──────────── Kubernetes namespace: smart-locker ─────────────┐
                         │                                                             │
 Browser ── REST/JSON ──►│ frontend (nginx, 2 pods)                                    │
                         │    │ /api/*                                                 │
                         │    ▼                                                        │
                         │ backend (FastAPI, 2-6 pods, autoscaled) ── gRPC ──► locker-controller
                         │    │                                    (round robin,   (2 pods, simulated
                         │    │                                     retries,        door hardware)
                         │    ▼                                     deadlines)                │
                         │ PgBouncer pooler (2 pods)                                   │
                         │    │                                                        │
                         │    ▼                                                        │
                         │ PostgreSQL: CloudNativePG cluster                           │
                         │    locker-db-1 (primary) ──streaming replication──► locker-db-2, -3
                         │                                                             │
                         │ Prometheus ── scrapes every pod ──► Grafana dashboard       │
                         └─────────────────────────────────────────────────────────────┘
```

Docker Compose runs the same services with one container each (and one PostgreSQL), which is
handy for development. Kubernetes adds the replication, autoscaling and self-healing.

## Concepts and where to find them

| Concept | How the project shows it | Where |
|---|---|---|
| Containers | Every service is an image built from a Dockerfile; the same images run in Compose and Kubernetes | `docker/` |
| Orchestration, declarative config | Kubernetes Deployments, Services, ConfigMaps and Secrets, applied with one `kubectl apply -k .` | `kustomization.yaml`, `kubernetes/` |
| Microservices, RPC | The backend calls a separate locker-controller service over **gRPC** with a typed contract | `proto/`, `locker-controller/`, `backend/app/locker_controller.py` |
| Service discovery, load balancing | HTTP traffic is balanced by Kubernetes Services. gRPC uses a **headless Service** so the client sees every controller pod and balances calls itself (`round_robin`) | `kubernetes/locker-controller/service.yaml` |
| Elasticity (autoscaling) | A HorizontalPodAutoscaler adds backend pods (2 to 6) when CPU passes 60% of the request and removes them afterwards. This works because the backend is stateless: sessions and queues live in PostgreSQL | `kubernetes/backend/hpa.yaml` |
| High availability, failover | PostgreSQL runs as 1 primary + 2 streaming replicas. If the primary dies, CloudNativePG promotes a replica and the `-rw` Service follows it | `kubernetes/database/cluster.yaml` |
| Operators and custom resources | The database cluster and the pooler are custom resources (`Cluster`, `Pooler`) managed by the CloudNativePG operator | `kubernetes/addons/install.sh` |
| Fault isolation | If the controller is down, only door unlocks fail (503 after a 3 s deadline). Browsing and booking keep working, and the gRPC call is made outside the database lock so it never blocks other bookings | `backend/app/main.py` `unlock_locker`, `backend/app/luggage.py` |
| Resilience patterns | Deadlines, automatic retries with exponential backoff, idempotency keys so a retried unlock is recognised, and a compensating action (saga style) that releases a luggage locker whose door failed to open | `backend/app/locker_controller.py`, `luggage.py` `_undo_luggage_claim` |
| Health checking | Liveness vs readiness probes for the backend; the controller uses the standard gRPC health protocol, which Kubernetes probes natively and the backend's gRPC client also watches | `kubernetes/*/deployment.yaml`, `locker-controller/controller/server.py` |
| Distributed coordination | Every backend replica runs the allocation worker; a PostgreSQL advisory lock makes sure only one allocates at a time, so no compartment is ever given out twice | `backend/app/main.py` (`ALLOCATION_LOCK_KEY`) |
| Connection pooling | PgBouncer in transaction mode lets up to 6 backend pods share a fixed number of database connections, and reconnects to the new primary after a failover | `kubernetes/database/pooler.yaml` |
| Zero-downtime deployments | Rolling updates with `maxUnavailable: 0`, readiness gates, `preStop` delays, and a gRPC server that reports NOT_SERVING and drains before exiting | deployments, `server.py` |
| Disruption budgets, spreading | PodDisruptionBudgets keep at least one pod of each service during node drains; topology spread puts replicas on different nodes | `kubernetes/*/pdb.yaml` |
| Observability | Every pod exposes Prometheus metrics; Prometheus discovers pods through the Kubernetes API (so autoscaled pods appear automatically); Grafana shows traffic, latency, pods, gRPC calls, lockers and database roles | `backend/app/metrics.py`, `monitoring/`, `kubernetes/monitoring/` |
| Load and stress testing | A k6 job runs **inside** the cluster, so its traffic is spread over all backend pods. The admin dashboard's Stress Test combines browsing, real parcel and luggage journeys and booking churn with chaos events (controller faults, controller outage, database failover), on 14 dedicated test lockers that users never see | `kubernetes/loadtest/smart-locker.js`, `backend/app/stress.py` |
| Demo controls, least privilege | The admin dashboard's System tests read cluster state with a read-only Role; the buttons that change the cluster need a second Role that is only applied for a demo (`make k8s-demo-on`). The backend checks its own permissions with a SelfSubjectAccessReview | `backend/app/demo.py`, `kubernetes/backend/rbac.yaml`, `kubernetes/demo-controls/` |
| CI/CD | GitHub Actions builds and tests with Compose, deploys to a throwaway 3-node kind cluster, tests again after killing the database primary, and publishes images to GitHub Container Registry from `main` | `.github/workflows/ci.yml` |

## Why gRPC, and where

The browser keeps using REST/JSON: browsers cannot call gRPC directly, and REST is the easiest
interface for the frontend. **Inside** the system, the backend talks to the locker-controller over
gRPC, the usual "REST at the edge, gRPC inside" split. gRPC fits here because:

- **The contract is explicit.** `proto/smartlocker/v1/locker_controller.proto` defines the calls and
  messages, and both sides generate their code from it, so they cannot drift apart.
- **Deadlines and retries are built in.** The backend gives every unlock 3 seconds in total. Calls that
  fail with `UNAVAILABLE` are retried with exponential backoff by the gRPC library itself
  (configured in the service config, not hand-written loops).
- **There is a standard health protocol.** `grpc.health.v1` is used by Kubernetes probes and by the
  client to skip pods that are shutting down.
- **Calls are cheap.** One long-lived HTTP/2 connection per pod carries many calls; messages are compact protobuf.

What happens when a user taps **Unlock locker**:

1. The backend checks the reservation (owner, status, compartment) without taking the allocation lock.
2. It calls `LockerController.UnlockCompartment` over gRPC with a new `request_id`. The round-robin
   policy picks one of the controller pods; if that call fails with `UNAVAILABLE`, gRPC retries it
   (possibly on the other pod) with the same `request_id`.
3. Only after the door has opened does the backend take the lock, re-check, and mark the parcel collected.
   If the controller cannot be reached within 3 seconds, nothing changes and the user gets a 503 to retry.

The controller replies with its pod name, so the API response (`door.controller`) and Grafana's
"Doors opened per controller pod" panel both show the load balancing.

Things learnt the hard way, now documented in the code:

- A normal ClusterIP Service would send **all** gRPC calls to one pod (kube-proxy balances
  connections, not calls), hence the headless Service and client-side `round_robin`.
- Servers set `max_connection_age` (30 s) so clients reconnect regularly and pick up new pods.
- During a rolling update the client must notice new pod IPs quickly: DNS re-resolution is allowed
  every second instead of gRPC's default 30 s. Together with client-side health checking and a
  `preStop` delay this made rolling updates lossless (0 of 756 calls failed).

## Demo script

About 10 minutes, all from the app's **Admin dashboard → Testing System**. Set up Kubernetes first
(README section 7), then:

```bash
make k8s-demo-on     # let the dashboard's buttons act on the cluster (demo only)
make k8s-app         # http://localhost:8080, sign in as admin / Admin@12345
```

Each test page shows live numbers, the pods involved, and the matching Grafana graphs (Grafana is
served by the app itself at `/grafana/`, so no extra window is needed).

1. **Stress Test.** Tick the load to run at the same time (browse & sign in, parcel journeys, luggage
   sessions, book & cancel churn) and any chaos events (40% controller faults, controller outage, kill the
   DB primary), pick an intensity and length, and press *Start*. It all runs on the 14 lockers of
   **Stress Test Bay**, which users never see, so real lockers stay free. Watch:
   - the test-locker grid filling and emptying as parcels and luggage come and go,
   - the console: every finished journey, chaos event, failed unlock and retry, as it happens,
   - the tiles: backend pods scaling out, requests/s, 5xx errors, p95 latency, doors opened.
   While it runs, sign in as a normal user in another window and book and collect a parcel at a real
   station: it still works. When the run ends the test lockers are freed automatically.
2. **Concurrency Test.** Send more bookings than there are free lockers at once: every compartment is
   given out exactly once, the rest wait as `CONDITIONAL`.
3. **Load Balancing.** *Send 30 HTTP requests*: the replies come from both backend pods (Kubernetes
   Service). *Send 30 gRPC calls*: the backend spreads them over both locker-controller pods itself
   (round robin over a headless Service).
4. **Scalability.** *Start load test* (40 users, 2 minutes at peak). Watch "CPU vs target" pass 60% and
   "Backend pods" go from 2 up to 6, with the per-pod graphs spreading out. It takes a minute or two to
   react because metrics-server samples once a minute. After the test the pods scale back down.
5. **Fault Tolerance.**
   - *Inject 40% faults*, then collect a parcel or store luggage in a user window: it still works,
     because gRPC retries the failed calls. *Clear faults* afterwards.
   - *Stop controller*: browsing and booking still work, unlocking fails after 3 s with "The locker
     controller is not responding". *Start controller* and it works again.
   - *Kill database primary*: the roles in "Database instances" change within seconds, a replica is
     now primary, and the old primary comes back as a replica. About 2 s of requests fail.
6. **Zero-downtime deploy.** `kubectl -n smart-locker rollout restart deployment/backend` while using
   the app: pods are replaced one at a time and nothing fails.
7. **CI/CD.** Open the repository's **Actions** tab: every pull request builds the images, runs the tests
   on Compose and on a 3-node Kubernetes cluster, and kills the database primary to prove failover.

Run `make k8s-demo-off` afterwards to take the extra permissions away again. On Docker Compose the
test pages still show the live numbers and graphs (with `--profile monitoring`), but the buttons that
change the cluster are disabled.

**Without the dashboard**, the same demos work from a terminal: `make k8s-loadtest`, `make k8s-failover`,
`kubectl -n smart-locker scale deployment/locker-controller --replicas=0`,
`kubectl -n smart-locker set env deployment/locker-controller FAILURE_RATE=0.4`, and Grafana via
`make k8s-grafana` (http://localhost:3001/grafana/).

## Measured results

Measured on 2026-11-19 on a laptop (minikube 1.39, Kubernetes 1.37, single node, 6 CPUs / 6 GB).

| Test | Result |
|---|---|
| k6 load test, 40 users at peak, about 4 minutes | 23,268 requests, **0 failed**, p95 latency 171 ms |
| Autoscaler during that test | CPU reached 285% of the request; backend pods 2 → 4 → 6 within 30 s, then back down one pod every 30 s after the test |
| Database primary deleted under steady read + write traffic | about **2 s** of failed requests, then a replica was primary and all tests passed |
| locker-controller rolling restart under continuous gRPC calls | **0 of 756** calls failed |
| Stress test, medium intensity, 2 min, all four load types plus all three chaos events, with a normal user booking and collecting a parcel at a real station mid-run | 155 parcels collected, 82 luggage sessions, 268 cancellations; backend scaled 2 → 6 pods; unlocks failed during the controller outage and succeeded on retry ~9 s after it came back; the real user's journey took 2.3 s; real lockers unchanged; all 14 test lockers freed at the end |
| locker-controller stopped (Compose) | unlocks fail with 503 after 3 s, booking and browsing unaffected, a luggage claim whose door did not open is released again |
| 40% injected controller faults | every unlock still succeeded through gRPC retries |
| 2 controller replicas | calls alternate between them |

## Not covered yet

- **Public cloud, on purpose.** The project runs entirely on local clusters (Compose, minikube, kind)
  and does not use a cloud provider. Because it only uses standard Kubernetes resources and an operator,
  the same manifests would also run on a managed cluster without changes to the app.
- **Event-driven messaging.** A message queue (for example a "parcel arrived" notification service) would
  show asynchronous communication next to the synchronous gRPC call.
- **Backups.** CloudNativePG can stream backups to S3-compatible object storage; not configured yet.
- **Multi-node on minikube.** minikube's default storage does not support moving database volumes between
  nodes; use the 3-node kind cluster (`kubernetes/kind-cluster.yaml`) for multi-node demos.
