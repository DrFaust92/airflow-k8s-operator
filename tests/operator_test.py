import time
import subprocess
from kopf.testing import KopfRunner

def test_operator():
    with KopfRunner(['run', '-A', '--verbose', 'main.py']) as runner:
        # do something while the operator is running.

        subprocess.run("kubectl apply -f crds/variable.yaml", shell=True, check=True)
        time.sleep(1)  # give it some time to react and to sleep and to retry
        
        subprocess.run("kubectl apply -f examples/variable.yaml", shell=True, check=True)
        time.sleep(5)  # give it some time to react
        
        subprocess.run("kubectl delete -f examples/variable.yaml", shell=True, check=True)
        time.sleep(1)  # give it some time to react and to sleep and to retry

        subprocess.run("kubectl delete -f crds/variable.yaml", shell=True, check=True)
        time.sleep(1)  # give it some time to react

    assert runner.exit_code == 0
    assert runner.exception is None
    assert 'And here we are!' in runner.output
    assert 'Deleted, really deleted' in runner.output