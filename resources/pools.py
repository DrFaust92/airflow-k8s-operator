import kopf
from airflow_client.client.api.pool_api import PoolApi
from airflow_client.client.exceptions import NotFoundException
from airflow_client.client.model.pool import Pool

from config.base import OPERATOR_RECONCILE_INTERVAL, OPERATOR_RECONCILE_INTERVAL_DELAY
from config.client import api_client
from config.metrics import MANAGED_RESOURCES
from config.reconcile import track

pools_api = PoolApi(api_client=api_client)


def _build_pool(name, spec) -> Pool:
    return Pool(
        name=name,
        description=spec.get("description"),
        include_deferred=spec.get("includeDeferred", False),
        slots=spec.get("slots"),
    )


@kopf.on.create("airflow.drfaust92", "v1beta1", "pools")
def create_pool(spec, name, logger, **kwargs):
    logger.info(f"Creating Airflow Pool: {name}")
    with track("pool", "create", logger):
        pools_api.post_pool(_build_pool(name, spec))
        MANAGED_RESOURCES.labels(resource_type="pool").inc()
    return {"message": f"Pool {name} created successfully."}


@kopf.on.resume("airflow.drfaust92", "v1beta1", "pools")
@kopf.on.update("airflow.drfaust92", "v1beta1", "pools")
@kopf.on.timer(
    "airflow.drfaust92",
    "v1beta1",
    "pools",
    interval=OPERATOR_RECONCILE_INTERVAL,
    initial_delay=OPERATOR_RECONCILE_INTERVAL_DELAY,
)
def reconcile_pool(spec, name, logger, **kwargs):
    logger.info(f"Reconciling Airflow Pool: {name}")
    with track("pool", "update", logger):
        pool = _build_pool(name, spec)
        try:
            pools_api.patch_pool(pool_name=name, pool=pool)
        except NotFoundException:
            logger.info(f"Pool {name} missing in Airflow; recreating")
            pools_api.post_pool(pool)
    return {"message": f"Pool {name} reconciled successfully."}


@kopf.on.delete("airflow.drfaust92", "v1beta1", "pools")
def delete_pool(name, logger, **kwargs):
    logger.info(f"Deleting Airflow Pool: {name}")
    with track("pool", "delete", logger):
        try:
            pools_api.delete_pool(pool_name=name)
        except NotFoundException:
            logger.info(f"Pool {name} already absent in Airflow")
        MANAGED_RESOURCES.labels(resource_type="pool").dec()
    return {"message": f"Pool {name} deleted successfully."}
