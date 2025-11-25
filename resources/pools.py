import kopf
from airflow_client.client.api.pool_api import PoolApi
from airflow_client.client.model.pool import Pool
from config.client import api_client
from config.base import OPERATOR_RECONCILE_INTERVAL

pools_api = PoolApi(api_client=api_client)


@kopf.on.create("airflow.drfaust92", "v1beta1", "pools")
def create_pool(meta, spec, namespace, logger, body, **kwargs):
    var_name = meta.get("name")

    logger.info(f"Creating Airflow Pool: {var_name} with spec: {spec}")
    try:
        pool = Pool(
            name=var_name,
            description=spec.get("description"),
            include_deferred=spec.get("includeDeferred", False),
            slots=spec.get("slots"),
        )
        pools_api.post_pool(pool)
        logger.info(f"Pool {var_name} created with value: {spec.get('value')}")
        return {"message": f"Pool {var_name} created successfully."}
    except Exception as e:
        logger.error(f"Failed to create Airflow Pool {var_name}: {e}")
        return {"error": f"Failed to create pool {var_name}: {e}"}


@kopf.on.delete("airflow.drfaust92", "v1beta1", "pools")
def delete_pool(meta, spec, namespace, logger, body, **kwargs):
    var_name = meta.get("name")

    logger.info(f"Deleting Airflow Pool: {var_name}")
    try:
        pools_api.delete_pool(pool_name=var_name)
        return {"message": f"Pool {var_name} deleted successfully."}
    except Exception as e:
        # Ignore 404 errors - pool already doesn't exist
        if "404" in str(e) or "Not Found" in str(e):
            logger.info(f"Pool {var_name} already deleted or doesn't exist")
            return {"message": f"Pool {var_name} already deleted or doesn't exist."}
        logger.error(f"Failed to delete Airflow Pool {var_name}: {e}")
        return {"error": f"Failed to delete pool {var_name}: {e}"}


@kopf.on.timer(
    "airflow.drfaust92", "v1beta1", "pools", interval=OPERATOR_RECONCILE_INTERVAL
)
@kopf.on.update("airflow.drfaust92", "v1beta1", "pools")
def update_pool(meta, spec, namespace, logger, body, **kwargs):
    var_name = meta.get("name")

    logger.info(f"Updating Airflow Pool: {var_name}")
    try:
        pool = Pool(
            name=var_name,
            description=spec.get("description"),
            include_deferred=spec.get("includeDeferred", False),
            slots=spec.get("slots"),
        )
        pools_api.patch_pool(pool_name=var_name, pool=pool)
        logger.info(f"Pool {var_name} updated with value: {spec}")
        return {"message": f"Pool {var_name} updated successfully."}
    except Exception as e:
        logger.error(f"Failed to update Airflow Pool {var_name}: {e}")
        return {"error": f"Failed to update pool {var_name}: {e}"}
