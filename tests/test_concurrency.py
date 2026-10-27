"""Concurrency tests for the Smart Locker API (pytest + requests).

Run (stack must be up: docker compose up -d --build):
    python -m pip install pytest requests
    python -m pytest tests/test_concurrency.py -v -s

Design
  * Every test builds its OWN station through the API, so results do not depend
    on seed data and the tests can be repeated any number of times.
  * Requests are released together with a threading.Barrier so they really hit
    the server at the same moment.
  * Reservations created by a test are cancelled afterwards (frees compartments).
    Test stations stay in the database with zone 'pytest'. To remove them:
        DELETE FROM reservation_events WHERE reservation_id IN
          (SELECT id FROM locker_reservations WHERE preferred_station_id IN
            (SELECT id FROM locker_stations WHERE zone='pytest'));
        DELETE FROM locker_reservations WHERE preferred_station_id IN
          (SELECT id FROM locker_stations WHERE zone='pytest');
        DELETE FROM locker_stations WHERE zone='pytest';
  * Set TEST_RESULTS_FILE=results.json to save the headline numbers for the report.
"""

import json
import os
import statistics
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = 30
RESULTS = {}


# ---------------------------------------------------------------- helpers
def call(method, path, **kwargs):
    return requests.request(method, f"{BASE_URL}{path}", timeout=TIMEOUT, **kwargs)


def ok(response, expected=(200, 201)):
    assert response.status_code in expected, (
        f"{response.request.method} {response.url} -> {response.status_code}: {response.text}"
    )
    return response.json()


class Lab:
    """Creates isolated stations and remembers reservations for clean-up."""

    def __init__(self):
        self.reservation_ids = []

    def station(self, **compartments_by_size):
        name = f"pytest-{uuid.uuid4().hex[:8]}"
        station = ok(call("POST", "/api/stations", json={"name": name, "zone": "pytest"}))
        for size, count in compartments_by_size.items():
            for i in range(count):
                ok(call("POST", f"/api/stations/{station['id']}/compartments",
                        json={"label": f"{size}{i + 1:02d}", "size": size}))
        return station["id"]

    def reserve(self, customer, station_id, backups=(), size="M", priority=0):
        body = ok(call("POST", "/api/reservations", json={
            "customer_id": customer, "preferred_station_id": station_id,
            "backup_station_ids": list(backups), "required_size": size, "priority": priority}))
        self.reservation_ids.append(body["id"])
        return body["id"]


def arrive(reservation_id):
    return call("POST", f"/api/reservations/{reservation_id}/arrive")


def arrive_together(reservation_ids):
    """Fire all arrivals at the same instant. Returns [(response, seconds), ...]."""
    barrier = threading.Barrier(len(reservation_ids), timeout=TIMEOUT)

    def one(rid):
        barrier.wait()
        start = time.perf_counter()
        response = arrive(rid)
        return response, time.perf_counter() - start

    with ThreadPoolExecutor(max_workers=len(reservation_ids)) as pool:
        return list(pool.map(one, reservation_ids))


def availability(station_id, size="M"):
    data = ok(call("GET", f"/api/stations/{station_id}/availability"))
    return next(s for s in data["sizes"] if s["size"] == size)


def assert_integrity():
    integrity = ok(call("GET", "/api/stats"))["integrity"]
    assert integrity == {"double_assignments": 0, "orphaned_compartments": 0}, integrity


# --------------------------------------------------------------- fixtures
@pytest.fixture(scope="module", autouse=True)
def api_ready():
    try:
        r = requests.get(f"{BASE_URL}/health/ready", timeout=3)
    except requests.RequestException as exc:
        pytest.skip(f"API not reachable at {BASE_URL}: {exc}")
    if r.status_code != 200:
        pytest.skip(f"API not ready ({r.status_code})")


@pytest.fixture
def lab():
    lab = Lab()
    yield lab
    for rid in lab.reservation_ids:  # frees any held compartment; 409 if already finished
        requests.delete(f"{BASE_URL}/api/reservations/{rid}", timeout=TIMEOUT)


@pytest.fixture(scope="module", autouse=True)
def save_results():
    yield
    path = os.getenv("TEST_RESULTS_FILE")
    if path and RESULTS:
        with open(path, "w") as fh:
            json.dump(RESULTS, fh, indent=2)


# ------------------------------------------------------------------ tests
def test_no_double_assignment_when_demand_exceeds_supply(lab):
    """25 simultaneous arrivals, 5 compartments: exactly 5 win, nobody shares."""
    supply, demand = 5, 25
    station = lab.station(M=supply)
    ids = [lab.reserve(f"C{i:02d}", station) for i in range(demand)]

    started = time.perf_counter()
    results = arrive_together(ids)
    elapsed = time.perf_counter() - started

    bodies = [ok(r) for r, _ in results]
    status = Counter(b["status"] for b in bodies)
    compartments = [b["compartment_id"] for b in bodies if b["compartment_id"]]

    assert status["ARRIVED"] == supply
    assert status["CONDITIONAL"] == demand - supply
    assert len(compartments) == len(set(compartments)), "a compartment was assigned twice"
    m = availability(station)
    assert (m["total"], m["available"], m["occupied"]) == (supply, 0, supply)
    assert_integrity()

    latencies = sorted(t for _, t in results)
    RESULTS["contention"] = {
        "requests": demand, "compartments": supply, "placed": status["ARRIVED"],
        "waiting": status["CONDITIONAL"], "duplicate_assignments": 0,
        "total_seconds": round(elapsed, 3),
        "latency_p50_ms": round(statistics.median(latencies) * 1000),
        "latency_p95_ms": round(latencies[int(0.95 * (len(latencies) - 1))] * 1000),
    }
    print("\ncontention:", RESULTS["contention"])


def test_same_reservation_arriving_twice_gets_one_compartment(lab):
    """Ten simultaneous arrivals for ONE reservation must not consume ten lockers."""
    station = lab.station(M=3)
    rid = lab.reserve("DUPLICATE", station)

    bodies = [ok(r) for r, _ in arrive_together([rid] * 10)]

    assert {b["status"] for b in bodies} == {"ARRIVED"}
    assert len({b["compartment_id"] for b in bodies}) == 1
    assert availability(station)["occupied"] == 1
    assert_integrity()


def test_higher_priority_wins_scarce_compartments(lab):
    """Two lockers, four competing reservations: the two highest priorities win."""
    station = lab.station(M=2)
    ids = {name: lab.reserve(name, station, priority=p)
           for name, p in [("low", 60), ("top", 99), ("mid", 80), ("other", 70)]}

    processed = ok(call("POST", "/api/arrivals/process", params={"limit": 4}))
    assert processed["processed"] == 4
    status = {r["id"]: r["status"] for r in processed["results"]}

    assert status[ids["top"]] == "ARRIVED"
    assert status[ids["mid"]] == "ARRIVED"
    assert status[ids["other"]] == "CONDITIONAL"
    assert status[ids["low"]] == "CONDITIONAL"
    assert_integrity()


def test_equal_priority_is_first_come_first_served(lab):
    station = lab.station(M=1)
    first = lab.reserve("first", station, priority=95)
    time.sleep(0.05)
    second = lab.reserve("second", station, priority=95)

    processed = ok(call("POST", "/api/arrivals/process", params={"limit": 2}))
    status = {r["id"]: r["status"] for r in processed["results"]}

    assert status[first] == "ARRIVED"
    assert status[second] == "CONDITIONAL"


def test_overflow_is_redirected_to_backup_station(lab):
    """10 requests, 3 lockers at the preferred station + 3 at the backup."""
    main, backup = lab.station(M=3), lab.station(M=3)
    ids = [lab.reserve(f"C{i}", main, backups=[backup]) for i in range(10)]

    bodies = [ok(r) for r, _ in arrive_together(ids)]
    placed = [b for b in bodies if b["status"] == "ARRIVED"]
    compartments = [b["compartment_id"] for b in placed]

    assert len(placed) == 6
    assert Counter(b["allocated_station_id"] for b in placed) == {main: 3, backup: 3}
    assert sum(b["status"] == "CONDITIONAL" for b in bodies) == 4
    assert len(compartments) == len(set(compartments))
    assert_integrity()


def test_compartment_is_reusable_after_collection(lab):
    """Regression: a collected locker must be allocatable again."""
    station = lab.station(M=1)
    a, b = lab.reserve("A", station), lab.reserve("B", station)

    first = ok(arrive(a))
    assert first["status"] == "ARRIVED"
    assert ok(arrive(b))["status"] == "CONDITIONAL"       # no space yet

    ok(call("POST", f"/api/reservations/{a}/fulfill"))     # A collects
    second = ok(arrive(b))
    assert second["status"] == "ARRIVED"
    assert second["compartment_id"] == first["compartment_id"]
    assert_integrity()


def test_connection_pool_survives_a_burst():
    """300 requests, 50 at a time: no errors, and the API is still ready afterwards."""
    def hit(_):
        return call("GET", "/api/availability").status_code

    with ThreadPoolExecutor(max_workers=50) as pool:
        codes = Counter(pool.map(hit, range(300)))

    assert codes == {200: 300}, codes
    assert call("GET", "/health/ready").status_code == 200
