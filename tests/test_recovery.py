# placeholder code for testing recovery if pod fails/is deleted. 
# this test is disabled by default to prevent accidental deletion of pods while running the system
# IMPORANT: before running test, ensure system is deployed into kubernetes first under the correct (local) cluster
# 1. ensure that you are in the right cluster (docker-desktop): kubectl config current-context
# 2. ensure that backend deployment has 2 ready replicas: kubectl -n smart-locker get deployments
# 3. ensure both backend pods are ready: kubectl -n smart-locker get pods
# 4. to run this test, set this in powershell (for this sesson only): $env:RUN_K8S_RECOVERY = "1"
# 5. run: python -m pytest tests/test_recovery.py -v -s
# 6. run cmds again to ensure amount of pods back to normal after test: 
# kubectl -n smart-locker get deployments
# kubectl -n smart-locker get pods
# 7. once finished, disable the test flag in your current PowerShell session by running to restore test's default behaviour: 
# Remove-Item Env:RUN_K8S_RECOVERY

import json
import os
import subprocess
import time

import pytest

NAMESPACE = "smart-locker"
DEPLOYMENT = "backend"
TIMEOUT = 120


def kubectl(*args):
    result = subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    if result.returncode != 0:
        pytest.fail(f"kubectl failed: {result.stderr}")
    return result.stdout.strip()


@pytest.mark.skipif(
    os.getenv("RUN_K8S_RECOVERY") != "1",
    reason="Set RUN_K8S_RECOVERY=1 to enable Pod deletion",
)
def test_backend_recovers_after_pod_deletion():
    # confirm that the deployment exists.
    deployment = json.loads(
        kubectl(
            "-n", NAMESPACE,
            "get", "deployment", DEPLOYMENT, "-o", "json",
        )
    )

    expected_replicas = deployment["spec"].get("replicas", 1)
    assert expected_replicas >= 2

    # Get the backend deployment's actual pod selector.
    selector = deployment["spec"]["selector"]["matchLabels"]

    pods = json.loads(
        kubectl(
            "-n", NAMESPACE, "get", "pods", "-o", "json"
        )
    )["items"]

    def matches_selector(pod):
        labels = pod["metadata"].get("labels", {})
        return all(labels.get(k) == v for k, v in selector.items())

    ready_pods = [
        pod for pod in pods
        if matches_selector(pod)
        and pod["status"].get("phase") == "Running"
        and any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in pod["status"].get("conditions", [])
        )
    ]

    assert len(ready_pods) == expected_replicas, (
        "Backend must start with all replicas ready"
    )

    old_pod = ready_pods[0]["metadata"]["name"]

    # delete one pod and measure the time until it is replaced.
    start = time.monotonic()

    kubectl(
        "-n", NAMESPACE,
        "delete", "pod", old_pod, "--wait=false",
    )

    deadline = time.monotonic() + TIMEOUT

    while time.monotonic() < deadline:
        current = json.loads(
            kubectl(
                "-n", NAMESPACE,
                "get", "pods", "-o", "json",
            )
        )["items"]

        ready = [
            pod for pod in current
            if matches_selector(pod)
            and pod["status"].get("phase") == "Running"
            and any(
                c.get("type") == "Ready"
                and c.get("status") == "True"
                for c in pod["status"].get("conditions", [])
            )
        ]

        replacement_exists = any(
            pod["metadata"]["name"] != old_pod
            for pod in ready
        )

        if len(ready) >= expected_replicas and replacement_exists:
            recovery_seconds = time.monotonic() - start
            print(f"Backend recovered in {recovery_seconds:.2f}s")
            return

        time.sleep(2)

    pytest.fail(
        f"Backend did not recover within {TIMEOUT} seconds"
    )
