# End-to-end tests against a running stack (Docker Compose or Kubernetes).
#
#   docker compose up -d --build
#   python -m pytest tests/test_end_to_end.py -v
#
# Point API_BASE_URL somewhere else for Kubernetes, e.g. after
#   kubectl -n smart-locker port-forward svc/backend 8000:8000
#
# The tests create their own users and parcels, and release the lockers they
# use, so they can be run repeatedly against the same database.

import os
import time
import uuid

import pytest
import requests

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_INITIAL_PASSWORD", "Admin@12345")
TIMEOUT = 30


def call(method, path, token=None, expected=None, **kwargs):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = requests.request(method, BASE_URL + path, headers=headers, timeout=TIMEOUT, **kwargs)
    if expected is not None:
        assert response.status_code == expected, f"{method} {path}: {response.status_code} {response.text}"
    return response


@pytest.fixture(scope="module")
def admin_token():
    body = call("POST", "/api/luggage/auth/login", expected=200,
                json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}).json()
    return body["access_token"]


@pytest.fixture()
def user():
    username = "e2e-" + uuid.uuid4().hex[:10]
    body = call("POST", "/api/luggage/auth/register", expected=201,
                json={"username": username, "password": "Password123!"}).json()
    return {"id": body["user"]["id"], "token": body["access_token"]}


def wait_for_status(reservation_id, token, wanted, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        current = call("GET", f"/api/reservations/{reservation_id}", token, expected=200).json()
        if current["status"] == wanted:
            return current
        time.sleep(0.3)
    raise AssertionError(f"reservation {reservation_id} stayed {current['status']}, expected {wanted}")


def test_parcel_collection_opens_door_over_grpc(admin_token, user):
    stations = call("GET", "/api/stations", expected=200).json()
    station = next((s for s in stations if s["available_by_size"].get("S", 0) > 0), None)
    if station is None:
        pytest.skip("no free S compartment left to test with")

    parcel = call("POST", "/api/admin/parcels", admin_token, expected=201,
                  json={"user_id": user["id"], "size": "S", "description": "e2e"}).json()
    reservation = call("POST", "/api/reservations", user["token"], expected=202,
                       json={"station_id": station["id"], "parcel_id": parcel["id"]}).json()
    wait_for_status(reservation["id"], user["token"], "ALLOCATED")
    call("POST", f"/api/reservations/{reservation['id']}/arrive", user["token"], expected=200)

    unlocked = call("POST", f"/api/reservations/{reservation['id']}/unlock", user["token"], expected=200).json()
    assert unlocked["status"] == "FULFILLED"
    assert unlocked["door"]["opened"] is True
    assert unlocked["door"]["via"] == "grpc"
    assert unlocked["door"]["controller"]


def test_luggage_store_and_pickup_open_doors_over_grpc(user):
    slots = call("GET", "/api/luggage/slots", user["token"], expected=200).json()
    if not slots:
        pytest.skip("no free XL compartment left to test with")

    stored = call("POST", "/api/luggage/sessions", user["token"], expected=201,
                  json={"compartment_id": slots[0]["compartment_id"]}).json()
    assert stored["unlock"]["door"]["via"] == "grpc"
    session_id = stored["session_id"]

    checkout = call("POST", f"/api/luggage/sessions/{session_id}/checkout", user["token"], expected=200).json()
    call("POST", f"/api/luggage/payments/{checkout['payment_reference']}/demo-pay", user["token"], expected=200)
    unlocked = call("POST", f"/api/luggage/sessions/{session_id}/unlock", user["token"], expected=200).json()
    assert unlocked["door"]["via"] == "grpc"
    done = call("POST", f"/api/luggage/sessions/{session_id}/complete", user["token"], expected=200).json()
    assert done["status"] == "RETRIEVED"


def test_controller_is_reachable_over_grpc(admin_token):
    info = call("GET", "/api/admin/controller", admin_token, expected=200).json()
    assert info["replicas_seen"], info


def release_run(admin_token, reservation_ids):
    """Release every compartment held by this run's reservations.

    Releasing a compartment lets a waiting (CONDITIONAL) reservation from the
    same run take it a moment later, so keep going until none are left.
    """
    quiet_rounds = 0
    for _ in range(60):
        lockers = call("GET", "/api/admin/lockers", admin_token, expected=200).json()
        held = [c["id"] for loc in lockers["locations"] for c in loc["compartments"]
                if c.get("reservation_id") in reservation_ids]
        for compartment_id in held:
            call("DELETE", f"/api/admin/lockers/{compartment_id}/allocation", admin_token)
        quiet_rounds = quiet_rounds + 1 if not held else 0
        if quiet_rounds >= 3:
            return
        time.sleep(0.7)


def test_concurrent_bookings_never_share_a_compartment(admin_token):
    stations = call("GET", "/api/stations", expected=200).json()
    station = max(stations, key=lambda s: s["available_compartments"])
    if station["available_compartments"] == 0:
        pytest.skip("every compartment is in use")

    # More requests than free compartments, all sent at once.
    count = station["available_compartments"] + 5
    report = call("POST", "/api/admin/tests/concurrency", admin_token, expected=200, json={"groups": [
        {"kind": "PARCEL_COLLECT", "station_id": station["id"], "size": "S", "count": count},
    ]}).json()
    try:
        assert report["created"] == count, report["errors"]
        assert report["duplicate_compartments"] == []
        assert report["allocated"] == station["available_compartments"]
        assert report["conditional"] == 5
    finally:
        release_run(admin_token, {r["id"] for r in report["results"]})


def test_metrics_endpoint_exposes_app_metrics():
    text = call("GET", "/metrics", expected=200).text
    for name in ("smartlocker_http_requests_total", "smartlocker_reservations", "process_cpu_seconds_total"):
        assert name in text, name


def test_stress_test_lockers_are_hidden_from_users(user):
    # Stress-test accounts can only be created by the stress test itself.
    reply = call("POST", "/api/luggage/auth/register",
                 json={"username": "stress-" + uuid.uuid4().hex[:8], "password": "Password123!"})
    assert reply.status_code == 422, reply.text
    # The test lockers never appear in the normal station or luggage lists.
    stations = call("GET", "/api/stations", expected=200).json()
    assert all(s["zone"] != "STRESS_TEST" for s in stations)
    slots = call("GET", "/api/luggage/slots", user["token"], expected=200).json()
    assert all(s["zone"] != "STRESS_TEST" for s in slots)
