import time

import kopf
from airflow_client.client.api.pool_api import PoolApi
from airflow_client.client.model.pool import Pool

from config.base import OPERATOR_RECONCILE_INTERVAL
from config.client import api_client
from config.metrics import (
    MANAGED_RESOURCES,
    RECONCILIATION_FAILURES,
    RESOURCE_OPERATIONS,
    RESOURCE_RECONCILIATION_DURATION,
)

pools_api = PoolApi(api_client=api_client)


@kopf.on.create("airflow.drfaust92", "v1beta1", "pools")
def create_pool(meta, spec, namespace, logger, body, **kwargs):
    var_name = meta.get("name")
    slots = spec.get("slots")

    logger.info(f"Creating Airflow Pool: {var_name} with spec: {spec}")
    start_time = time.time()
    try:
        pool = Pool(
            name=var_name,
            description=spec.get("description"),
            include_deferred=spec.get("includeDeferred", False),
            slots=slots,
        )
        pools_api.post_pool(pool)

        duration = time.time() - start_time
        RESOURCE_RECONCILIATION_DURATION.labels(
            resource_type="pool", operation="create"
        ).observe(duration)
        RESOURCE_OPERATIONS.labels(
            resource_type="pool", operation="create", status="success"
        ).inc()
        MANAGED_RESOURCES.labels(resource_type="pool").inc()

        logger.info(f"Pool {var_name} created with value: {spec.get('value')}")
        return {"message": f"Pool {var_name} created successfully."}
    except Exception as e:
        duration = time.time() - start_time
        RESOURCE_RECONCILIATION_DURATION.labels(
            resource_type="pool", operation="create"
        ).observe(duration)
        RESOURCE_OPERATIONS.labels(
            resource_type="pool", operation="create", status="failure"
        ).inc()
        RECONCILIATION_FAILURES.labels(resource_type="pool").inc()

        logger.error(f"Failed to create Airflow Pool {var_name}: {e}")
        return {"error": f"Failed to create pool {var_name}: {e}"}


@kopf.on.delete("airflow.drfaust92", "v1beta1", "pools")
def delete_pool(meta, spec, namespace, logger, body, **kwargs):
    var_name = meta.get("name")

    logger.info(f"Deleting Airflow Pool: {var_name}")
    start_time = time.time()
    try:
        pools_api.delete_pool(pool_name=var_name)

        duration = time.time() - start_time
        RESOURCE_RECONCILIATION_DURATION.labels(
            resource_type="pool", operation="delete"
        ).observe(duration)
        RESOURCE_OPERATIONS.labels(
            resource_type="pool", operation="delete", status="success"
        ).inc()
        MANAGED_RESOURCES.labels(resource_type="pool").dec()

        return {"message": f"Pool {var_name} deleted successfully."}
    except Exception as e:
        duration = time.time() - start_time
        RESOURCE_RECONCILIATION_DURATION.labels(
            resource_type="pool", operation="delete"
        ).observe(duration)

        # Ignore 404 errors - pool already doesn't exist
        if "404" in str(e) or "Not Found" in str(e):
            RESOURCE_OPERATIONS.labels(
                resource_type="pool", operation="delete", status="success"
            ).inc()
            MANAGED_RESOURCES.labels(resource_type="pool").dec()
            logger.info(f"Pool {var_name} already deleted or doesn't exist")
            return {"message": f"Pool {var_name} already deleted or doesn't exist."}

        RESOURCE_OPERATIONS.labels(
            resource_type="pool", operation="delete", status="failure"
        ).inc()
        RECONCILIATION_FAILURES.labels(resource_type="pool").inc()
        logger.error(f"Failed to delete Airflow Pool {var_name}: {e}")
        return {"error": f"Failed to delete pool {var_name}: {e}"}


@kopf.on.timer(
    "airflow.drfaust92", "v1beta1", "pools", interval=OPERATOR_RECONCILE_INTERVAL
)
@kopf.on.update("airflow.drfaust92", "v1beta1", "pools")
def update_pool(meta, spec, namespace, logger, body, **kwargs):
    var_name = meta.get("name")
    slots = spec.get("slots")

    logger.info(f"Updating Airflow Pool: {var_name}")
    start_time = time.time()
    try:
        pool = Pool(
            name=var_name,
            description=spec.get("description"),
            include_deferred=spec.get("includeDeferred", False),
            slots=slots,
        )
        pools_api.patch_pool(pool_name=var_name, pool=pool)

        duration = time.time() - start_time
        RESOURCE_RECONCILIATION_DURATION.labels(
            resource_type="pool", operation="update"
        ).observe(duration)
        RESOURCE_OPERATIONS.labels(
            resource_type="pool", operation="update", status="success"
        ).inc()

        logger.info(f"Pool {var_name} updated with value: {spec}")
        return {"message": f"Pool {var_name} updated successfully."}
    except Exception as e:
        duration = time.time() - start_time
        RESOURCE_RECONCILIATION_DURATION.labels(
            resource_type="pool", operation="update"
        ).observe(duration)
        RESOURCE_OPERATIONS.labels(
            resource_type="pool", operation="update", status="failure"
        ).inc()
        RECONCILIATION_FAILURES.labels(resource_type="pool").inc()

        logger.error(f"Failed to update Airflow Pool {var_name}: {e}")
        return {"error": f"Failed to update pool {var_name}: {e}"}
