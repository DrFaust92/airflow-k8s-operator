import kopf
from airflow_client.client.exceptions import ApiException, NotFoundException

from config.auth_manager import AuthManagerApi
from config.base import OPERATOR_RECONCILE_INTERVAL, OPERATOR_RECONCILE_INTERVAL_DELAY
from config.metrics import MANAGED_RESOURCES
from config.reconcile import (
    DELETE_MAX_RETRIES,
    adopt_conflict,
    adoption_allowed,
    adoption_denied,
    owns,
    track,
)
from resources.schemas import AirflowRoleSpec

auth_manager_api = AuthManagerApi()

_RESOURCE = ("airflow.drfaust92", "v1beta1", "airflowroles")


def _role_name(name, spec) -> str:
    return AirflowRoleSpec.model_validate(spec).name or name


def _build_role(role_name, spec) -> dict:
    parsed = AirflowRoleSpec.model_validate(spec)
    return {
        "name": role_name,
        "actions": [
            {"action": {"name": a.action}, "resource": {"name": a.resource}}
            for a in parsed.actions
        ],
    }


@kopf.on.create(*_RESOURCE)
def create_role(spec, name, namespace, logger, patch, **kwargs):
    role_name = _role_name(name, spec)
    logger.info(f"Creating Airflow Role: {role_name}")
    with track("role", "create", logger, patch=patch):
        adopt = AirflowRoleSpec.model_validate(spec).adopt
        if adopt and not adoption_allowed(namespace):
            raise adoption_denied("Role", role_name, namespace)
        role = _build_role(role_name, spec)
        try:
            auth_manager_api.post_role(role)
        except ApiException as e:
            if e.status != 409:
                raise
            if not adopt:
                raise adopt_conflict("Role", role_name) from e
            logger.info(f"Role {role_name} already exists in Airflow; adopting")
            auth_manager_api.patch_role(role_name, role)
        MANAGED_RESOURCES.labels(resource_type="role").inc()
    return {"message": f"Role {role_name} created successfully."}


@kopf.on.resume(*_RESOURCE)
@kopf.on.update(*_RESOURCE)
@kopf.on.timer(
    *_RESOURCE,
    interval=OPERATOR_RECONCILE_INTERVAL,
    initial_delay=OPERATOR_RECONCILE_INTERVAL_DELAY,
)
def reconcile_role(spec, name, namespace, logger, patch, status=None, **kwargs):
    role_name = _role_name(name, spec)
    logger.info(f"Reconciling Airflow Role: {role_name}")
    adopt = AirflowRoleSpec.model_validate(spec).adopt
    if not owns(status, "create_role", adopt, namespace):
        logger.info(f"Role {role_name} is not managed by this resource; skipping")
        return None
    with track("role", "update", logger, patch=patch):
        role = _build_role(role_name, spec)
        try:
            auth_manager_api.patch_role(role_name, role)
        except NotFoundException:
            logger.info(f"Role {role_name} missing in Airflow; recreating")
            auth_manager_api.post_role(role)
    return {"message": f"Role {role_name} reconciled successfully."}


@kopf.on.delete(*_RESOURCE, retries=DELETE_MAX_RETRIES)
def delete_role(spec, name, namespace, logger, retry=0, status=None, **kwargs):
    role_name = _role_name(name, spec)
    adopt = AirflowRoleSpec.model_validate(spec).adopt
    if not owns(status, "create_role", adopt, namespace):
        logger.info(f"Role {role_name} is not managed by this resource; leaving it")
        return {"message": f"Role {role_name} left in Airflow (not managed)."}
    logger.info(f"Deleting Airflow Role: {role_name}")
    with track(
        "role", "delete", logger, delay=10, retry=retry, max_retries=DELETE_MAX_RETRIES
    ):
        try:
            auth_manager_api.delete_role(role_name)
        except NotFoundException:
            logger.info(f"Role {role_name} already absent in Airflow")
        MANAGED_RESOURCES.labels(resource_type="role").dec()
    return {"message": f"Role {role_name} deleted successfully."}
