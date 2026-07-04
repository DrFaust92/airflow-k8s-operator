import kopf
from airflow_client.client.api.variable_api import VariableApi
from airflow_client.client.exceptions import ApiException, NotFoundException
from airflow_client.client.model.variable import Variable

from config.base import (
    IS_API_V2,
    OPERATOR_RECONCILE_INTERVAL,
    OPERATOR_RECONCILE_INTERVAL_DELAY,
)
from config.client import api_client
from config.k8s_secret import resolve_value
from config.metrics import MANAGED_RESOURCES
from config.reconcile import DELETE_MAX_RETRIES, track

variables_api = VariableApi(api_client=api_client)


def _build_variable(name, spec, namespace, logger) -> Variable:
    # Value may be a direct value or resolved from a Kubernetes Secret; never
    # log the resolved value.
    var_value = resolve_value(spec, namespace, logger=logger)
    fields = {
        "key": name,
        "value": var_value,
        "description": spec.get("description"),
        # team_name is an Airflow 3 (v2) multi-team field; only send it there.
        "team_name": spec.get("teamName") if IS_API_V2 else None,
    }
    # The airflow client model rejects None for optional str fields.
    return Variable(**{k: v for k, v in fields.items() if v is not None})


@kopf.on.create("airflow.drfaust92", "v1beta1", "variables")
def create_variable(spec, name, namespace, logger, patch, **kwargs):
    logger.info(f"Creating Airflow Variable: {name}")
    with track("variable", "create", logger, patch=patch):
        variable = _build_variable(name, spec, namespace, logger)
        try:
            variables_api.post_variables(variable, _preload_content=False)
        except ApiException as e:
            if e.status != 409:
                raise
            # Already exists in Airflow; adopt it by patching.
            logger.info(f"Variable {name} already exists in Airflow; adopting")
            variables_api.patch_variable(
                variable_key=name, variable=variable, _preload_content=False
            )
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
def reconcile_variable(spec, name, namespace, logger, patch, **kwargs):
    logger.info(f"Reconciling Airflow Variable: {name}")
    with track("variable", "update", logger, patch=patch):
        variable = _build_variable(name, spec, namespace, logger)
        try:
            variables_api.patch_variable(
                variable_key=name, variable=variable, _preload_content=False
            )
        except NotFoundException:
            logger.info(f"Variable {name} missing in Airflow; recreating")
            variables_api.post_variables(variable, _preload_content=False)
    return {"message": f"Variable {name} reconciled successfully."}


@kopf.on.delete("airflow.drfaust92", "v1beta1", "variables", retries=DELETE_MAX_RETRIES)
def delete_variable(name, namespace, logger, retry=0, **kwargs):
    # retries caps how long a failing delete blocks: after DELETE_MAX_RETRIES
    # attempts kopf gives up and releases the finalizer instead of wedging the
    # resource forever. The final attempt emits a Warning event + metric.
    logger.info(f"Deleting Airflow Variable: {name}")
    with track(
        "variable",
        "delete",
        logger,
        delay=10,
        retry=retry,
        max_retries=DELETE_MAX_RETRIES,
    ):
        try:
            variables_api.delete_variable(variable_key=name, _preload_content=False)
        except NotFoundException:
            logger.info(f"Variable {name} already absent in Airflow")
        MANAGED_RESOURCES.labels(resource_type="variable").dec()
    return {"message": f"Variable {name} deleted successfully."}
