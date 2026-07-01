import kopf
from airflow_client.client.api.variable_api import VariableApi
from airflow_client.client.exceptions import NotFoundException
from airflow_client.client.model.variable import Variable

from config.base import OPERATOR_RECONCILE_INTERVAL, OPERATOR_RECONCILE_INTERVAL_DELAY
from config.client import api_client
from config.k8s_secret import resolve_value
from config.metrics import MANAGED_RESOURCES
from config.reconcile import track

variables_api = VariableApi(api_client=api_client)


def _build_variable(name, spec, namespace, logger) -> Variable:
    # Value may be a direct value or resolved from a Kubernetes Secret; never
    # log the resolved value.
    var_value = resolve_value(spec, namespace, logger=logger)
    fields = {"key": name, "value": var_value, "description": spec.get("description")}
    # The airflow client model rejects None for optional str fields.
    return Variable(**{k: v for k, v in fields.items() if v is not None})


@kopf.on.create("airflow.drfaust92", "v1beta1", "variables")
def create_variable(spec, name, namespace, logger, **kwargs):
    logger.info(f"Creating Airflow Variable: {name}")
    with track("variable", "create", logger):
        variables_api.post_variables(_build_variable(name, spec, namespace, logger))
        MANAGED_RESOURCES.labels(resource_type="variable").inc()
    return {"message": f"Variable {name} created successfully."}


@kopf.on.resume("airflow.drfaust92", "v1beta1", "variables")
@kopf.on.update("airflow.drfaust92", "v1beta1", "variables")
@kopf.on.timer(
    "airflow.drfaust92",
    "v1beta1",
    "variables",
    interval=OPERATOR_RECONCILE_INTERVAL,
    initial_delay=OPERATOR_RECONCILE_INTERVAL_DELAY,
)
def reconcile_variable(spec, name, namespace, logger, **kwargs):
    logger.info(f"Reconciling Airflow Variable: {name}")
    with track("variable", "update", logger):
        variable = _build_variable(name, spec, namespace, logger)
        try:
            variables_api.patch_variable(variable_key=name, variable=variable)
        except NotFoundException:
            logger.info(f"Variable {name} missing in Airflow; recreating")
            variables_api.post_variables(variable)
    return {"message": f"Variable {name} reconciled successfully."}


@kopf.on.delete("airflow.drfaust92", "v1beta1", "variables", retries=5)
def delete_variable(name, namespace, logger, **kwargs):
    # retries caps how long a failing delete blocks: after 5 attempts kopf gives
    # up and releases the finalizer instead of wedging the resource forever.
    logger.info(f"Deleting Airflow Variable: {name}")
    with track("variable", "delete", logger, delay=10):
        try:
            variables_api.delete_variable(variable_key=name)
        except NotFoundException:
            logger.info(f"Variable {name} already absent in Airflow")
        MANAGED_RESOURCES.labels(resource_type="variable").dec()
    return {"message": f"Variable {name} deleted successfully."}
