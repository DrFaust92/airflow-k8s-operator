import time
import subprocess
from kopf.testing import KopfRunner

def test_operator():
    with KopfRunner(['run', '-A', '--verbose', 'main.py']) as runner: # noqa: F841

        # create CRDs
        subprocess.run("kubectl apply -f crds/variable.yaml", shell=True, check=True)
        time.sleep(1)  # give it some time to react and to sleep and to retry
        
        subprocess.run("kubectl apply -f crds/connection.yaml", shell=True, check=True)
        time.sleep(1)  # give it some time to react and to sleep and to retry
        
        # create, update, delete Variable
        subprocess.run("kubectl apply -f tests/variable.yaml", shell=True, check=True)
        time.sleep(3)  # give it some time to react
        
        subprocess.run("kubectl apply -f tests/variable-updated.yaml", shell=True, check=True)
        time.sleep(3)  # give it some time to react
        
        subprocess.run("kubectl delete -f tests/variable.yaml", shell=True, check=True)
        time.sleep(1)  # give it some time to react and to sleep and to retry

        # create, update, delete Connection
        subprocess.run("kubectl apply -f tests/connection.yaml", shell=True, check=True)
        time.sleep(3)  # give it some time to react and to sleep and to retry

        subprocess.run("kubectl delete -f tests/connection.yaml", shell=True, check=True)
        time.sleep(1)  # give it some time to react and to sleep and to retry

        # delete CRDs
        subprocess.run("kubectl delete -f crds/connection.yaml", shell=True, check=True)
        subprocess.run("kubectl delete -f crds/variable.yaml", shell=True, check=True)
        time.sleep(1)  # give it some time to react