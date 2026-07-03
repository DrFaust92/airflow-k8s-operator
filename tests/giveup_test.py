"""End-to-end test of the bounded-delete give-up path.

Runs the operator against an UNREACHABLE Airflow (configured via env in its own
CI job), so every Airflow call fails. A delete must then give up after
DELETE_MAX_RETRIES and release the finalizer instead of wedging the resource
forever -- proving `kubectl delete` completes even when the backend is down.
"""

import subprocess
import time

from kopf.testing import KopfRunner

CRD_PATH = "chart/airflow-k8s-operator/templates/crds"
TEST_PATH = "tests"


def test_delete_gives_up_when_backend_unreachable():
    with KopfRunner(["run", "-A", "--verbose", "main.py"]) as runner:
        subprocess.run(f"kubectl apply -f {CRD_PATH}/", shell=True, check=True)
        time.sleep(1)

        # Create is attempted (and fails against the unreachable backend), but
        # kopf still attaches the finalizer because a delete handler exists.
        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/variable.yaml", shell=True, check=True
        )
        time.sleep(5)

        # Delete blocks on the finalizer while the delete handler keeps failing;
        # after DELETE_MAX_RETRIES kopf gives up and releases it, so this
        # completes (rather than hanging until the timeout). ~50s worst case.
        subprocess.run(
            f"kubectl delete -f {TEST_PATH}/variable.yaml",
            shell=True,
            check=True,
            timeout=180,
        )

        subprocess.run(f"kubectl delete -f {CRD_PATH}/", shell=True, check=True)

    # The give-up must have been emitted (Warning + metric path).
    assert "Giving up after" in runner.stdout, (
        "expected the delete handler to give up after exhausting retries"
    )
