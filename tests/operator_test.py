import os
import subprocess
import time

import requests
from kopf.testing import KopfRunner

CRD_PATH = "chart/airflow-k8s-operator/templates/crds"
TEST_PATH = "tests"


def _airflow_base():
    """Base URL of the Airflow REST API, including /api/vN, from the same env
    the operator uses."""
    host = os.environ["AIRFLOW_HOST"].rstrip("/")
    base = os.environ.get("AIRFLOW_API_BASE_URL", "/api/v1")
    return host if host.endswith(base) else host + base


def _airflow_session():
    """A requests session authenticated the same way as the operator, so the
    test can query Airflow directly to confirm real resource state."""
    session = requests.Session()
    token = os.environ.get("AIRFLOW_ACCESS_TOKEN")
    username = os.environ.get("AIRFLOW_USERNAME")
    password = os.environ.get("AIRFLOW_PASSWORD")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    elif username and password:
        session.auth = (username, password)
    return session


def _get(session, path):
    return session.get(f"{_airflow_base()}{path}", timeout=30)


def _assert_exists_in_airflow(session, path, timeout=45):
    """Poll Airflow directly until the resource exists (HTTP 200)."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = _get(session, path)
        if resp.status_code == 200:
            return resp.json()
        last = resp.status_code
        time.sleep(2)
    raise AssertionError(f"{path} not found in Airflow within {timeout}s (last {last})")


def _assert_absent_in_airflow(session, path, timeout=30):
    """Poll Airflow directly until the resource is gone (HTTP 404)."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = _get(session, path).status_code
        if last == 404:
            return
        time.sleep(2)
    raise AssertionError(
        f"{path} still present in Airflow after {timeout}s (last status {last})"
    )


def test_operator():
    session = _airflow_session()

    with KopfRunner(["run", "-A", "--verbose", "main.py"]) as runner:
        # create CRDs
        subprocess.run(f"kubectl apply -f {CRD_PATH}/", shell=True, check=True)
        time.sleep(1)

        # Variable: create -> exists with value; update -> value changes; delete -> gone
        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/variable.yaml", shell=True, check=True
        )
        var = _assert_exists_in_airflow(session, "/variables/example-variable")
        assert var.get("value") == "s3://example-bucket/data/path", var

        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/variable-updated.yaml",
            shell=True,
            check=True,
        )
        time.sleep(3)
        updated = _get(session, "/variables/example-variable").json()
        assert updated.get("value") != "s3://example-bucket/data/path", updated

        subprocess.run(
            f"kubectl delete -f {TEST_PATH}/variable.yaml", shell=True, check=True
        )
        _assert_absent_in_airflow(session, "/variables/example-variable")

        # Connection: create -> exists; delete -> gone
        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/connection.yaml", shell=True, check=True
        )
        _assert_exists_in_airflow(session, "/connections/example-connection")

        subprocess.run(
            f"kubectl delete -f {TEST_PATH}/connection.yaml", shell=True, check=True
        )
        _assert_absent_in_airflow(session, "/connections/example-connection")

        # Pool: create -> exists; update; delete -> gone
        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/pool.yaml", shell=True, check=True
        )
        _assert_exists_in_airflow(session, "/pools/example-pool")

        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/pool-updated.yaml",
            shell=True,
            check=True,
        )
        time.sleep(3)

        subprocess.run(
            f"kubectl delete -f {TEST_PATH}/pool.yaml", shell=True, check=True
        )
        _assert_absent_in_airflow(session, "/pools/example-pool")

        # delete CRDs
        subprocess.run(f"kubectl delete -f {CRD_PATH}/", shell=True, check=True)

    # The operator must not have abandoned a delete (finalizer released while
    # the Airflow object may still exist).
    assert "Giving up after" not in runner.stdout, (
        "operator gave up on a delete; the Airflow backend rejected the request"
    )
