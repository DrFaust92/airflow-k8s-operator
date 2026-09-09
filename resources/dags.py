import kopf
from airflow_client.client.api.dag_api import DAGApi
from airflow_client.client.exceptions import NotFoundException
from airflow_client.client.model.dag import DAG

from config.base import (
    OPERATOR_RECONCILE_INTERVAL,
    OPERATOR_RECONCILE_INTERVAL_DELAY,
)
from config.client import api_client
from config.metrics import MANAGED_RESOURCES
from config.reconcile import DELETE_MAX_RETRIES, track
from resources.schemas import DagSpec

dags_api = DAGApi(api_client=api_client)


def _dag_id(name, spec) -> str:
    return DagSpec.model_validate(spec).dag_id or name


def _patch_dag(name, spec):
    # DAGs cannot be created via the API (they come from DAG files), so both
    # create and reconcile are the same operation: adopt the existing DAG and
    # align its paused state. A missing DAG means the file has not been parsed
    # yet (or was removed) -- retry until it shows up.
    parsed = DagSpec.model_validate(spec)
    dag_id = parsed.dag_id or name
    try:
        dags_api.patch_dag(
            dag_id=dag_id,
            dag=DAG(is_paused=parsed.is_paused),
            update_mask=["is_paused"],
            _preload_content=False,
        )
    except NotFoundException as e:
        raise kopf.TemporaryError(
            f"DAG {dag_id} not found in Airflow; waiting for it to be parsed",
            delay=30,
        ) from e


@kopf.on.create("airflow.drfaust92", "v1beta1", "dags")
def create_dag(spec, name, logger, patch, **kwargs):
    logger.info(f"Adopting Airflow DAG: {name}")
    with track("dag", "create", logger, patch=patch):
        _patch_dag(name, spec)
        MANAGED_RESOURCES.labels(resource_type="dag").inc()
    return {"message": f"DAG {name} adopted successfully."}


@kopf.on.resume("airflow.drfaust92", "v1beta1", "dags")
@kopf.on.update("airflow.drfaust92", "v1beta1", "dags")
@kopf.on.timer(
    "airflow.drfaust92",
    "v1beta1",
    "dags",
    interval=OPERATOR_RECONCILE_INTERVAL,
    initial_delay=OPERATOR_RECONCILE_INTERVAL_DELAY,
)
def reconcile_dag(spec, name, logger, patch, **kwargs):
    logger.info(f"Reconciling Airflow DAG: {name}")
    with track("dag", "update", logger, patch=patch):
        _patch_dag(name, spec)
    return {"message": f"DAG {name} reconciled successfully."}


@kopf.on.delete("airflow.drfaust92", "v1beta1", "dags", retries=DELETE_MAX_RETRIES)
def delete_dag(spec, name, logger, retry=0, **kwargs):
    # retries caps how long a failing delete blocks: after DELETE_MAX_RETRIES
    # attempts kopf gives up and releases the finalizer instead of wedging the
    # resource forever. The final attempt emits a Warning event + metric.
    logger.info(f"Deleting Airflow DAG: {name}")
    with track(
        "dag", "delete", logger, delay=10, retry=retry, max_retries=DELETE_MAX_RETRIES
    ):
        try:
            dags_api.delete_dag(dag_id=_dag_id(name, spec), _preload_content=False)
        except NotFoundException:
            logger.info(f"DAG {name} already absent in Airflow")
        MANAGED_RESOURCES.labels(resource_type="dag").dec()
    return {"message": f"DAG {name} deleted successfully."}
