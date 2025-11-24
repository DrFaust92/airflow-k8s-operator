import kopf
from airflow_client.client.api.variable_api import VariableApi
from airflow_client.client.model.variable import Variable
from config.client import api_client
from config.k8s_secret import resolve_value
from config.base import OPERATOR_RECONCILE_INTERVAL

variables_api = VariableApi(api_client=api_client)


@kopf.on.create("airflow.drfaust92", "v1beta1", "variables")
def create_variable(meta, spec, namespace, logger, body, **kwargs):
    var_name = meta.get("name")

    logger.info(f"Creating Airflow Variable: {var_name} with spec: {spec}")
    try:
        logger.debug(f"Passing spec to resolve_value: {spec}")
        var_value = resolve_value(spec, namespace, logger=logger)
        variable = Variable(
            key=var_name, value=var_value, description=spec.get("description")
        )
        variables_api.post_variables(variable)
        logger.info(f"Variable {var_name} created with value: {var_value}")
        return {"message": f"Variable {var_name} created successfully."}
    except Exception as e:
        logger.error(f"Failed to create Airflow Variable {var_name}: {e}")
        return {"error": f"Failed to create variable {var_name}: {e}"}


@kopf.on.delete("airflow.drfaust92", "v1beta1", "variables")
def delete_variable(meta, spec, namespace, logger, body, **kwargs):
    var_name = meta.get("name")

    logger.info(f"Deleting Airflow Variable: {var_name}")
    try:
        variables_api.delete_variable(variable_key=var_name)
        return {"message": f"Variable {var_name} deleted successfully."}
    except Exception as e:
        # Ignore 404 errors - variable already doesn't exist
        if "404" in str(e) or "Not Found" in str(e):
            logger.info(f"Variable {var_name} already deleted or doesn't exist")
            return {"message": f"Variable {var_name} already deleted or doesn't exist."}
        logger.error(f"Failed to delete Airflow Variable {var_name}: {e}")
        return {"error": f"Failed to delete variable {var_name}: {e}"}


@kopf.on.timer(
    "airflow.drfaust92", "v1beta1", "variables", interval=OPERATOR_RECONCILE_INTERVAL
)
@kopf.on.update("airflow.drfaust92", "v1beta1", "variables")
def update_variable(meta, spec, namespace, logger, body, **kwargs):
    var_name = meta.get("name")

    logger.info(f"Updating Airflow Variable: {var_name}")
    try:
        logger.debug(f"Passing spec to resolve_value: {spec}")
        var_value = resolve_value(spec, namespace, logger=logger)
        logger.debug(f"Resolved value for {var_name}: {var_value}")
        variable = Variable(
            key=var_name, value=var_value, description=spec.get("description")
        )
        variables_api.patch_variable(variable_key=var_name, variable=variable)
        logger.info(f"Variable {var_name} updated with value: {var_value}")
        return {"message": f"Variable {var_name} updated successfully."}
    except Exception as e:
        logger.error(f"Failed to update Airflow Variable {var_name}: {e}")
        return {"error": f"Failed to update variable {var_name}: {e}"}
