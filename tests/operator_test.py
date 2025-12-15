import subprocess
import time

from kopf.testing import KopfRunner

CRD_PATH = "chart/airflow-k8s-operator/templates/crds"
TEST_PATH = "tests"


def test_operator():
    with KopfRunner(["run", "-A", "--verbose", "main.py"]) as runner:  # noqa: F841
        # create CRDs
        subprocess.run(f"kubectl apply -f {CRD_PATH}/", shell=True, check=True)
        time.sleep(1)  # give it some time to react and to sleep and to retry

        # create, update, delete Variable
        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/variable.yaml", shell=True, check=True
        )
        time.sleep(3)  # give it some time to react

        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/variable-updated.yaml",
            shell=True,
            check=True,
        )
        time.sleep(3)  # give it some time to react

        subprocess.run(
            f"kubectl delete -f {TEST_PATH}/variable.yaml", shell=True, check=True
        )
        time.sleep(1)  # give it some time to react and to sleep and to retry

        # create, update, delete Connection
        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/connection.yaml", shell=True, check=True
        )
        time.sleep(3)  # give it some time to react and to sleep and to retry

        subprocess.run(
            f"kubectl delete -f {TEST_PATH}/connection.yaml", shell=True, check=True
        )
        time.sleep(1)  # give it some time to react and to sleep and to retry

        # create, update, delete Pool
        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/pool.yaml", shell=True, check=True
        )
        time.sleep(3)  # give it some time to react and to sleep and to retry
        subprocess.run(
            f"kubectl apply -f {TEST_PATH}/pool-updated.yaml",
            shell=True,
            check=True,
        )
        time.sleep(3)  # give it some time to react and to sleep and to retry
        subprocess.run(
            f"kubectl delete -f {TEST_PATH}/pool.yaml", shell=True, check=True
        )
        time.sleep(1)  # give it some time to react and to sleep and to retry

        # delete CRDs
        subprocess.run(f"kubectl delete -f {CRD_PATH}/", shell=True, check=True)
