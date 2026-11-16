
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
        f"{BASE_URL}/api/ping",
        timeout=TIMEOUT,
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "ok"
    assert data["service"] == "backend"
    assert "environment" in data
    assert isinstance(data["database_reachable"], bool)

    assert data["database_reachable"] is True
