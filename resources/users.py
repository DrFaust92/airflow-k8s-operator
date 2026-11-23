import kopf
from airflow_client.client.exceptions import ApiException, NotFoundException

from config.auth_manager import AuthManagerApi
from config.base import OPERATOR_RECONCILE_INTERVAL, OPERATOR_RECONCILE_INTERVAL_DELAY
from config.k8s_secret import get_secret_value_with_version
from config.metrics import MANAGED_RESOURCES
from config.reconcile import (
    DELETE_MAX_RETRIES,
    adopt_conflict,
    adoption_allowed,
    adoption_denied,
    owns,
    track,
)
from resources.schemas import AirflowUserSpec

auth_manager_api = AuthManagerApi()

_RESOURCE = ("airflow.drfaust92", "v1beta1", "airflowusers")

# status key holding the resourceVersion of the password Secret last sent to
# Airflow. Only the version is kept: never the password or a hash of it.
PASSWORD_VERSION_KEY = "passwordSecretVersion"

_PROFILE_FIELDS = ["email", "first_name", "last_name", "roles"]


def _username(name, spec) -> str:
    return AirflowUserSpec.model_validate(spec).username or name


def _build_user(username, parsed: AirflowUserSpec) -> dict:
    return {
        "username": username,
        "email": parsed.email,
        "first_name": parsed.first_name,
        "last_name": parsed.last_name,
        "roles": [{"name": r} for r in parsed.roles],
    }


def _password(parsed: AirflowUserSpec, namespace) -> tuple[str, str]:
    ref = parsed.password_secret_ref
    return get_secret_value_with_version(ref.name, ref.key, namespace)


@kopf.on.create(*_RESOURCE)
def create_user(spec, name, namespace, logger, patch, **kwargs):
    username = _username(name, spec)
    logger.info(f"Creating Airflow User: {username}")
    with track("user", "create", logger, patch=patch):
        parsed = AirflowUserSpec.model_validate(spec)
        if parsed.adopt and not adoption_allowed(namespace):
            raise adoption_denied("User", username, namespace)
        password, version = _password(parsed, namespace)
        user = {**_build_user(username, parsed), "password": password}
        try:
            auth_manager_api.post_user(user)
        except ApiException as e:
            if e.status != 409:
                raise
            if not parsed.adopt:
                raise adopt_conflict("User", username) from e
            logger.info(f"User {username} already exists in Airflow; adopting")
            auth_manager_api.patch_user(
                username, user, update_mask=[*_PROFILE_FIELDS, "password"]
            )
        patch.status[PASSWORD_VERSION_KEY] = version
        MANAGED_RESOURCES.labels(resource_type="user").inc()
    return {"message": f"User {username} created successfully."}


@kopf.on.resume(*_RESOURCE)
@kopf.on.update(*_RESOURCE)
@kopf.on.timer(
    *_RESOURCE,
    interval=OPERATOR_RECONCILE_INTERVAL,
    initial_delay=OPERATOR_RECONCILE_INTERVAL_DELAY,
)
def reconcile_user(spec, name, namespace, logger, patch, status=None, **kwargs):
    username = _username(name, spec)
    logger.info(f"Reconciling Airflow User: {username}")
    parsed = AirflowUserSpec.model_validate(spec)
    if not owns(status, "create_user", parsed.adopt, namespace):
        logger.info(f"User {username} is not managed by this resource; skipping")
        return None
    with track("user", "update", logger, patch=patch):
        password, version = _password(parsed, namespace)
        user = _build_user(username, parsed)
        try:
            if version != (status or {}).get(PASSWORD_VERSION_KEY):
                logger.info(f"Password Secret for user {username} changed; updating")
                auth_manager_api.patch_user(
                    username,
                    {**user, "password": password},
                    update_mask=[*_PROFILE_FIELDS, "password"],
                )
            else:
                auth_manager_api.patch_user(username, user, update_mask=_PROFILE_FIELDS)
        except NotFoundException:
            logger.info(f"User {username} missing in Airflow; recreating")
            auth_manager_api.post_user({**user, "password": password})
        patch.status[PASSWORD_VERSION_KEY] = version
    return {"message": f"User {username} reconciled successfully."}


@kopf.on.delete(*_RESOURCE, retries=DELETE_MAX_RETRIES)
def delete_user(spec, name, namespace, logger, retry=0, status=None, **kwargs):
    username = _username(name, spec)
    adopt = AirflowUserSpec.model_validate(spec).adopt
    if not owns(status, "create_user", adopt, namespace):
        logger.info(f"User {username} is not managed by this resource; leaving it")
        return {"message": f"User {username} left in Airflow (not managed)."}
    logger.info(f"Deleting Airflow User: {username}")
    with track(
        "user", "delete", logger, delay=10, retry=retry, max_retries=DELETE_MAX_RETRIES
    ):
        try:
            auth_manager_api.delete_user(username)
        except NotFoundException:
            logger.info(f"User {username} already absent in Airflow")
        MANAGED_RESOURCES.labels(resource_type="user").dec()
    return {"message": f"User {username} deleted successfully."}
