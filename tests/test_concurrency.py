"""Concurrency smoke test (stdlib only).

Needs a freshly seeded database: 'Load Test Depot' has exactly 30 free M
compartments. We fire 50 simultaneous arrivals for size M at that station
(no backups) and expect exactly 30 ARRIVED, 20 still CONDITIONAL, and no
compartment assigned twice.

"""

import json
import sys
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
N = 50


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


depot = next(s for s in call("GET", "/api/stations") if s["name"] == "Load Test Depot")
ids = [call("POST", "/api/reservations", {
    "customer_id": f"LOAD-{i:03d}", "preferred_station_id": depot["id"],
    "required_size": "M", "priority": 0})["id"] for i in range(N)]

with ThreadPoolExecutor(max_workers=N) as ex:
    results = list(ex.map(lambda rid: call("POST", f"/api/reservations/{rid}/arrive"), ids))

status = Counter(r["status"] for r in results)
comps = [r["compartment_id"] for r in results if r["compartment_id"]]
print("status counts      :", dict(status))
print("duplicate comps    :", len(comps) - len(set(comps)))
print("server integrity   :", call("GET", "/api/stats")["integrity"])
ok = status["ARRIVED"] == 30 and status["CONDITIONAL"] == 20 and len(comps) == len(set(comps))
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
