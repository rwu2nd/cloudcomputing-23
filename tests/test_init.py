# run in powershell to test: python -m pytest tests/test_init.py -v -s

import os

import requests

BASE_URL = os.getenv(
    "API_BASE_URL",
    "http://localhost:8000",
)

TIMEOUT = 5

# test def need to start w the word 'test' to be detected by pytest
def test_is_backend_live():
    response = requests.get(
        f"{BASE_URL}/health/live",
        timeout=TIMEOUT,
    )

    assert response.status_code == 200


def test_is_backend_ready():
    response = requests.get(
        f"{BASE_URL}/health/ready",
        timeout=TIMEOUT,
    )

    # a healthy, fully started compose stack should be ready
    assert response.status_code == 200


def test_ping_endpoint():
    response = requests.get(
        f"{BASE_URL}/api",
        timeout=TIMEOUT,
    )

    assert response.status_code == 200

    data = response.json()

    assert data["service"] == "smart-locker-api"
    assert data["docs"] == "/docs"
    assert isinstance(data["workflow"], list)
    assert len(data["workflow"]) > 0
    assert "allocation_policy" in data
    assert "queue_policy" in data
    assert isinstance(data["queue_batch_window_ms"], (int, float))
