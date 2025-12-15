import time

import kopf
from airflow_client.client.api.variable_api import VariableApi
from airflow_client.client.model.variable import Variable

from config.base import OPERATOR_RECONCILE_INTERVAL
from config.client import api_client
from config.k8s_secret import resolve_value
from config.metrics import (
    MANAGED_RESOURCES,
    RECONCILIATION_FAILURES,
    RESOURCE_OPERATIONS,
    RESOURCE_RECONCILIATION_DURATION,
)

variables_api = VariableApi(api_client=api_client)


@kopf.on.create("airflow.drfaust92", "v1beta1", "variables")
def create_variable(meta, spec, namespace, logger, body, **kwargs):
    var_name = meta.get("name")

    logger.info(f"Creating Airflow Variable: {var_name} with spec: {spec}")
    start_time = time.time()
    try:
        logger.debug(f"Passing spec to resolve_value: {spec}")
        var_value = resolve_value(spec, namespace, logger=logger)
        variable = Variable(
            key=var_name, value=var_value, description=spec.get("description")
        )
        variables_api.post_variables(variable)

        duration = time.time() - start_time
        RESOURCE_RECONCILIATION_DURATION.labels(
            resource_type="variable", operation="create"
        ).observe(duration)
        RESOURCE_OPERATIONS.labels(
            resource_type="variable", operation="create", status="success"
        ).inc()
        MANAGED_RESOURCES.labels(resource_type="variable").inc()

        logger.info(f"Variable {var_name} created with value: {var_value}")
        return {"message": f"Variable {var_name} created successfully."}
    except Exception as e:
        duration = time.time() - start_time
        RESOURCE_RECONCILIATION_DURATION.labels(
            resource_type="variable", operation="create"
        ).observe(duration)
        RESOURCE_OPERATIONS.labels(
            resource_type="variable", operation="create", status="failure"
        ).inc()
        RECONCILIATION_FAILURES.labels(resource_type="variable").inc()

        logger.error(f"Failed to create Airflow Variable {var_name}: {e}")
        return {"error": f"Failed to create variable {var_name}: {e}"}


@kopf.on.delete("airflow.drfaust92", "v1beta1", "variables")
def delete_variable(meta, spec, namespace, logger, body, **kwargs):
    var_name = meta.get("name")

    logger.info(f"Deleting Airflow Variable: {var_name}")
    start_time = time.time()
    try:
        variables_api.delete_variable(variable_key=var_name)

        duration = time.time() - start_time
        RESOURCE_RECONCILIATION_DURATION.labels(
            resource_type="variable", operation="delete"
        ).observe(duration)
        RESOURCE_OPERATIONS.labels(
            resource_type="variable", operation="delete", status="success"
        ).inc()
        MANAGED_RESOURCES.labels(resource_type="variable").dec()

        return {"message": f"Variable {var_name} deleted successfully."}
    except Exception as e:
        duration = time.time() - start_time
        RESOURCE_RECONCILIATION_DURATION.labels(
            resource_type="variable", operation="delete"
        ).observe(duration)

        # Ignore 404 errors - variable already doesn't exist
        if "404" in str(e) or "Not Found" in str(e):
            RESOURCE_OPERATIONS.labels(
                resource_type="variable", operation="delete", status="success"
            ).inc()
            MANAGED_RESOURCES.labels(resource_type="variable").dec()
            logger.info(f"Variable {var_name} already deleted or doesn't exist")
            return {"message": f"Variable {var_name} already deleted or doesn't exist."}

        RESOURCE_OPERATIONS.labels(
            resource_type="variable", operation="delete", status="failure"
        ).inc()
        RECONCILIATION_FAILURES.labels(resource_type="variable").inc()
        logger.error(f"Failed to delete Airflow Variable {var_name}: {e}")
        return {"error": f"Failed to delete variable {var_name}: {e}"}


@kopf.on.timer(
    "airflow.drfaust92", "v1beta1", "variables", interval=OPERATOR_RECONCILE_INTERVAL
)
@kopf.on.update("airflow.drfaust92", "v1beta1", "variables")
def update_variable(meta, spec, namespace, logger, body, **kwargs):
    var_name = meta.get("name")

    logger.info(f"Updating Airflow Variable: {var_name}")
    start_time = time.time()
    try:
        logger.debug(f"Passing spec to resolve_value: {spec}")
        var_value = resolve_value(spec, namespace, logger=logger)
        logger.debug(f"Resolved value for {var_name}: {var_value}")
        variable = Variable(
            key=var_name, value=var_value, description=spec.get("description")
        )
        variables_api.patch_variable(variable_key=var_name, variable=variable)

        duration = time.time() - start_time
        RESOURCE_RECONCILIATION_DURATION.labels(
            resource_type="variable", operation="update"
        ).observe(duration)
        RESOURCE_OPERATIONS.labels(
            resource_type="variable", operation="update", status="success"
        ).inc()

        logger.info(f"Variable {var_name} updated with value: {var_value}")
        return {"message": f"Variable {var_name} updated successfully."}
    except Exception as e:
        duration = time.time() - start_time
        RESOURCE_RECONCILIATION_DURATION.labels(
            resource_type="variable", operation="update"
        ).observe(duration)
        RESOURCE_OPERATIONS.labels(
            resource_type="variable", operation="update", status="failure"
        ).inc()
        RECONCILIATION_FAILURES.labels(resource_type="variable").inc()

        logger.error(f"Failed to update Airflow Variable {var_name}: {e}")
        return {"error": f"Failed to update variable {var_name}: {e}"}
