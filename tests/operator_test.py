import json
import subprocess
import time

from airflow_client.client.api.connection_api import ConnectionApi
from airflow_client.client.api.pool_api import PoolApi
from airflow_client.client.api.variable_api import VariableApi
from airflow_client.client.exceptions import NotFoundException
from airflow_client.client.model.variable import Variable
from kopf.testing import KopfRunner

from config.client import api_client

TEST_PATH = "tests"

# CRDs live in their own chart now; render it with helm and pipe to kubectl.
CRD_RENDER = "helm template t chart/airflow-k8s-operator-crds"

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


def _wait_for_present(get_callable, *args, timeout=60):
    """Poll Airflow directly until the resource exists again."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _airflow_get(get_callable, *args) is not None:
            return
        time.sleep(2)
    raise AssertionError(f"{args} not present in Airflow within {timeout}s")


def test_operator():
    with KopfRunner(["run", "-A", "--verbose", "main.py"]):
        # create CRDs
        subprocess.run(f"{CRD_RENDER} | kubectl apply -f -", shell=True, check=True)
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

        # Drift-heal (out-of-band deletion): if the Airflow object is deleted
        # behind the operator's back, reconciliation must recreate it. Create
        # the variable, delete it directly against the Airflow API, then nudge
        # the CR (annotation bump) to fire the reconcile/update handler -- the
        # same code path the timer runs on its schedule -- and assert the
        # variable reappears in Airflow.
        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/variable.yaml", shell=True, check=True
        )
        _wait_for_synced("variable", "example-variable")
        assert _airflow_get(variable_api.get_variable, "example-variable") is not None

        variable_api.delete_variable("example-variable", _preload_content=False)
        _assert_absent(variable_api.get_variable, "example-variable")

        subprocess.run(
            "kubectl annotate variable example-variable "
            "airflow.drfaust92/drift-heal-test=1 --overwrite",
            shell=True,
            check=True,
        )
        _wait_for_present(variable_api.get_variable, "example-variable")

        subprocess.run(
            f"kubectl delete -f {TEST_PATH}/variable.yaml", shell=True, check=True
        )
        _assert_absent(variable_api.get_variable, "example-variable")

        # 409 adopt: pre-create a variable in Airflow, then a CR with the same
        # name must be adopted (create -> 409 -> patch) and reconciled to the
        # CR's value, not loop forever on the conflict.
        variable_api.post_variables(
            Variable(key="adopted-variable", value="pre-existing"),
            _preload_content=False,
        )
        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/variable-adopt.yaml",
            shell=True,
            check=True,
        )
        _wait_for_synced("variable", "adopted-variable")
        adopted = _airflow_get(variable_api.get_variable, "adopted-variable")
        assert adopted is not None and adopted.get("value") == "adopted-by-operator", (
            adopted
        )
        subprocess.run(
            f"kubectl delete -f {TEST_PATH}/variable-adopt.yaml",
            shell=True,
            check=True,
        )
        _assert_absent(variable_api.get_variable, "adopted-variable")

        # Failure path: a Variable whose secretRef points at a nonexistent
        # Secret must surface phase=Error, must NOT be created in Airflow, and
        # must still delete cleanly (finalizer released, no wedge). Folded into
        # this runner because a second KopfRunner in-process would re-bind the
        # Prometheus port. Independent of Airflow, so it also exercises v1/v2.
        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/variable-missing-secret.yaml",
            shell=True,
            check=True,
        )
        _wait_for_phase("variable", "broken-variable", "Error", timeout=90)
        assert _airflow_get(variable_api.get_variable, "broken-variable") is None
        subprocess.run(
            f"kubectl delete -f {TEST_PATH}/variable-missing-secret.yaml",
            shell=True,
            check=True,
            timeout=120,
        )

        # delete CRDs
        subprocess.run(f"{CRD_RENDER} | kubectl delete -f -", shell=True, check=True)
