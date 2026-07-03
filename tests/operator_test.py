import json
import subprocess
import time

from airflow_client.client.api.connection_api import ConnectionApi
from airflow_client.client.api.pool_api import PoolApi
from airflow_client.client.api.variable_api import VariableApi
from airflow_client.client.exceptions import NotFoundException
from kopf.testing import KopfRunner

from config.client import api_client

CRD_PATH = "chart/airflow-k8s-operator/templates/crds"
TEST_PATH = "tests"

# Verify against Airflow using the SAME authenticated client the operator uses,
# so the check works identically for Airflow 2 (/api/v1) and 3 (/api/v2).
variable_api = VariableApi(api_client)
connection_api = ConnectionApi(api_client)
pool_api = PoolApi(api_client)


def _phase(kind, name):
    result = subprocess.run(
        f"kubectl get {kind} {name} -o jsonpath='{{.status.phase}}'",
        shell=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _wait_for_phase(kind, name, expected, timeout=180):
    """Wait until the CR's status.phase reaches `expected`. The operator retries
    until Airflow is ready, so a Synced wait also absorbs Airflow startup."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = _phase(kind, name)
        if last == expected:
            return
        time.sleep(3)
    raise AssertionError(
        f"{kind}/{name} did not reach phase={expected} within {timeout}s "
        f"(last {last!r})"
    )


def _wait_for_synced(kind, name, timeout=180):
    _wait_for_phase(kind, name, "Synced", timeout)


def _airflow_get(get_callable, *args):
    """Return the resource JSON from Airflow, or None if it does not exist.

    Uses _preload_content=False so the raw response is returned without
    deserializing against the v1 client models (works for v2 responses too).
    """
    try:
        resp = get_callable(*args, _preload_content=False)
    except NotFoundException:
        return None
    return json.loads(resp.data)


def _assert_absent(get_callable, *args, timeout=45):
    """Poll Airflow directly until the resource is gone (NotFound)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _airflow_get(get_callable, *args) is None:
            return
        time.sleep(2)
    raise AssertionError(f"{args} still present in Airflow after {timeout}s")


def test_operator():
    with KopfRunner(["run", "-A", "--verbose", "main.py"]) as runner:
        # create CRDs
        subprocess.run(f"kubectl apply -f {CRD_PATH}/", shell=True, check=True)
        time.sleep(1)

        # Variable: create -> Synced + exists in Airflow with value;
        # update -> value changes; delete -> gone from Airflow.
        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/variable.yaml", shell=True, check=True
        )
        _wait_for_synced("variable", "example-variable")
        var = _airflow_get(variable_api.get_variable, "example-variable")
        assert (
            var is not None and var.get("value") == "s3://example-bucket/data/path"
        ), var

        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/variable-updated.yaml",
            shell=True,
            check=True,
        )
        time.sleep(5)
        updated = _airflow_get(variable_api.get_variable, "example-variable")
        assert updated.get("value") != "s3://example-bucket/data/path", updated

        subprocess.run(
            f"kubectl delete -f {TEST_PATH}/variable.yaml", shell=True, check=True
        )
        _assert_absent(variable_api.get_variable, "example-variable")

        # Connection: create -> Synced + exists; delete -> gone.
        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/connection.yaml", shell=True, check=True
        )
        _wait_for_synced("connection", "example-connection")
        assert _airflow_get(connection_api.get_connection, "example-connection")

        subprocess.run(
            f"kubectl delete -f {TEST_PATH}/connection.yaml", shell=True, check=True
        )
        _assert_absent(connection_api.get_connection, "example-connection")

        # Pool: create -> Synced + exists; update; delete -> gone.
        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/pool.yaml", shell=True, check=True
        )
        _wait_for_synced("pool", "example-pool")
        assert _airflow_get(pool_api.get_pool, "example-pool")

        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/pool-updated.yaml",
            shell=True,
            check=True,
        )
        time.sleep(5)

        subprocess.run(
            f"kubectl delete -f {TEST_PATH}/pool.yaml", shell=True, check=True
        )
        _assert_absent(pool_api.get_pool, "example-pool")

        # delete CRDs
        subprocess.run(f"kubectl delete -f {CRD_PATH}/", shell=True, check=True)

    # The operator must not have abandoned a delete (finalizer released while
    # the Airflow object may still exist).
    assert "Giving up after" not in runner.stdout, (
        "operator gave up on a delete; the Airflow backend rejected the request"
    )


def test_failed_create_surfaces_error():
    """A reconcile failure must be visible and must not create the resource.

    Uses a Variable whose secretRef points at a nonexistent Secret, so the
    operator fails before ever calling Airflow -- independent of Airflow.
    """
    with KopfRunner(["run", "-A", "--verbose", "main.py"]):
        subprocess.run(f"kubectl apply -f {CRD_PATH}/", shell=True, check=True)
        time.sleep(1)

        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/variable-missing-secret.yaml",
            shell=True,
            check=True,
        )

        # The failure is surfaced on the CR ...
        _wait_for_phase("variable", "broken-variable", "Error", timeout=90)
        # ... and nothing was created in Airflow.
        assert _airflow_get(variable_api.get_variable, "broken-variable") is None

        # A failed-create CR still deletes cleanly (finalizer released, no wedge).
        subprocess.run(
            f"kubectl delete -f {TEST_PATH}/variable-missing-secret.yaml",
            shell=True,
            check=True,
            timeout=120,
        )

        subprocess.run(f"kubectl delete -f {CRD_PATH}/", shell=True, check=True)
