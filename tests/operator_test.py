import subprocess
import time

from kopf.testing import KopfRunner

CRD_PATH = "chart/airflow-k8s-operator/templates/crds"
TEST_PATH = "tests"


def _phase(kind, name):
    result = subprocess.run(
        f"kubectl get {kind} {name} -o jsonpath='{{.status.phase}}'",
        shell=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _wait_for_synced(kind, name, timeout=45):
    """Poll the resource's status.phase until it is Synced.

    This is the real assertion that the operator actually reconciled the
    resource against Airflow -- not merely that ``kubectl apply`` succeeded. If
    the operator failed (e.g. an incompatible API), the phase stays "Error" and
    this fails the test instead of passing silently.
    """
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = _phase(kind, name)
        if last == "Synced":
            return
        time.sleep(2)
    raise AssertionError(
        f"{kind}/{name} did not reach phase=Synced within {timeout}s "
        f"(last phase={last!r})"
    )


def test_operator():
    with KopfRunner(["run", "-A", "--verbose", "main.py"]) as runner:
        # create CRDs
        subprocess.run(f"kubectl apply -f {CRD_PATH}/", shell=True, check=True)
        time.sleep(1)  # give it some time to react and to sleep and to retry

        # create, update, delete Variable
        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/variable.yaml", shell=True, check=True
        )
        _wait_for_synced("variable", "example-variable")

        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/variable-updated.yaml",
            shell=True,
            check=True,
        )
        _wait_for_synced("variable", "example-variable")

        subprocess.run(
            f"kubectl delete -f {TEST_PATH}/variable.yaml", shell=True, check=True
        )
        time.sleep(1)  # give it some time to react and to sleep and to retry

        # create, delete Connection
        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/connection.yaml", shell=True, check=True
        )
        _wait_for_synced("connection", "example-connection")

        subprocess.run(
            f"kubectl delete -f {TEST_PATH}/connection.yaml", shell=True, check=True
        )
        time.sleep(1)  # give it some time to react and to sleep and to retry

        # create, update, delete Pool
        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/pool.yaml", shell=True, check=True
        )
        _wait_for_synced("pool", "example-pool")

        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/pool-updated.yaml",
            shell=True,
            check=True,
        )
        _wait_for_synced("pool", "example-pool")

        subprocess.run(
            f"kubectl delete -f {TEST_PATH}/pool.yaml", shell=True, check=True
        )
        time.sleep(1)  # give it some time to react and to sleep and to retry

        # delete CRDs
        subprocess.run(f"kubectl delete -f {CRD_PATH}/", shell=True, check=True)

    # The operator must not have abandoned a delete (which would release the
    # finalizer while the Airflow object still exists).
    assert "Giving up after" not in runner.stdout, (
        "operator gave up on a delete; the Airflow backend rejected the request"
    )
