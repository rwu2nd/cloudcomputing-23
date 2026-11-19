
# Tests whether Kubernetes automatically replaces a deleted backend pod.
#
# DISABLED BY DEFAULT:
# This test deliberately deletes a running backend pod.
# Only run it against the intended local Kubernetes cluster.
#
# Before running:
# 1. Check the Kubernetes context:
#    kubectl config current-context
#
# 2. Check the backend deployment:
#    kubectl -n smart-locker get deployments
#
# 3. Check that both backend pods are ready:
#    kubectl -n smart-locker get pods
#
# 4. Enable the test in PowerShell:
#    $env:RUN_K8S_RECOVERY = "1"
#
# 5. Run:
#    python -m pytest tests/test_recovery.py -v -s
#
# 6. After testing, check the deployment and pods again:
#    kubectl -n smart-locker get deployments
#    kubectl -n smart-locker get pods
#
# 7. Disable the test flag:
#    Remove-Item Env:RUN_K8S_RECOVERY

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


def is_ready(pod):
    return (
        pod["status"].get("phase") == "Running"
        and any(
            condition.get("type") == "Ready"
            and condition.get("status") == "True"
            for condition in pod["status"].get("conditions", [])
        )
    )


@pytest.mark.skipif(
    os.getenv("RUN_K8S_RECOVERY") != "1",
    reason="Set RUN_K8S_RECOVERY=1 to enable Pod deletion",
)
def test_backend_recovers_after_pod_deletion():

    # Confirm the expected Kubernetes context.
    context = kubectl("config", "current-context")
    assert context == "docker-desktop", (
        f"Expected docker-desktop context, but found: {context}"
    )

    # Confirm that the backend deployment exists.
    deployment = json.loads(
        kubectl(
            "-n", NAMESPACE,
            "get", "deployment", DEPLOYMENT,
            "-o", "json",
        )
    )

    expected_replicas = deployment["spec"].get("replicas", 1)
    assert expected_replicas >= 2, (
        "Backend deployment must have at least 2 replicas"
    )

    # Get the deployment's actual pod selector.
    selector = deployment["spec"]["selector"]["matchLabels"]

    pods = json.loads(
        kubectl(
            "-n", NAMESPACE,
            "get", "pods",
            "-o", "json",
        )
    )["items"]

    def matches_selector(pod):
        labels = pod["metadata"].get("labels", {})
        return all(
            labels.get(key) == value
            for key, value in selector.items()
        )

    ready_pods = [
        pod for pod in pods
        if matches_selector(pod) and is_ready(pod)
    ]

    assert len(ready_pods) == expected_replicas, (
        "Backend must start with all replicas ready"
    )

    # Remember the original pod names.
    original_pod_names = {
        pod["metadata"]["name"]
        for pod in ready_pods
    }

    # Delete one backend pod.
    old_pod = ready_pods[0]["metadata"]["name"]

    print(f"\nDeleting backend pod: {old_pod}")

    start = time.monotonic()

    kubectl(
        "-n", NAMESPACE,
        "delete", "pod", old_pod,
        "--wait=false",
    )

    deadline = time.monotonic() + TIMEOUT

    while time.monotonic() < deadline:

        current = json.loads(
            kubectl(
                "-n", NAMESPACE,
                "get", "pods",
                "-o", "json",
            )
        )["items"]

        ready = [
            pod for pod in current
            if matches_selector(pod) and is_ready(pod)
        ]

        # Find a ready pod that did not exist before deletion.
        replacement_pods = [
            pod for pod in ready
            if pod["metadata"]["name"] not in original_pod_names
        ]

        if (
            len(ready) >= expected_replicas
            and replacement_pods
        ):
            recovery_seconds = time.monotonic() - start

            print(
                f"Replacement pod: "
                f"{replacement_pods[0]['metadata']['name']}"
            )
            print(
                f"Backend recovered in {recovery_seconds:.2f}s"
            )

            return

        time.sleep(2)

    pytest.fail(
        f"Backend did not recover within {TIMEOUT} seconds"
    )
