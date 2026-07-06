import kopf
from airflow_client.client.api.connection_api import ConnectionApi
from airflow_client.client.exceptions import ApiException, NotFoundException
from airflow_client.client.model.connection import Connection

from config.base import (
    IS_API_V2,
    OPERATOR_RECONCILE_INTERVAL,
    OPERATOR_RECONCILE_INTERVAL_DELAY,
)
from config.client import api_client
from config.k8s_secret import resolve_value
from config.metrics import MANAGED_RESOURCES
from config.reconcile import DELETE_MAX_RETRIES, track
from resources.schemas import ConnectionSpec

connections_api = ConnectionApi(api_client=api_client)


def _build_connection(name, spec, namespace, logger) -> Connection:
    parsed = ConnectionSpec.model_validate(spec)
    # Resolve sensitive fields from direct values or secret references.
    login = (
        resolve_value(spec["login"], namespace, logger=logger)
        if spec.get("login")
        else None
    )
    password = (
        resolve_value(spec["password"], namespace, logger=logger)
        if spec.get("password")
        else None
    )
    fields = {
        "connection_id": name,
        "conn_type": parsed.conn_type,
        "description": parsed.description,
        "host": parsed.host,
        "login": login,
        "password": password,
        "port": parsed.port,
        "schema": parsed.schema_,
        "extra": parsed.extra,
        # team_name is an Airflow 3 (v2) multi-team field; only send it there.
        "team_name": parsed.team_name if IS_API_V2 else None,
    }
    # The airflow client model rejects None for optional str fields, so only
    # pass the fields that are actually set.
    return Connection(**{k: v for k, v in fields.items() if v is not None})


@kopf.on.create("airflow.drfaust92", "v1beta1", "connections")
def create_connection(spec, name, namespace, logger, patch, **kwargs):
    logger.info(f"Creating Airflow Connection: {name}")
    with track("connection", "create", logger, patch=patch):
        connection = _build_connection(name, spec, namespace, logger)
        try:
            connections_api.post_connection(connection, _preload_content=False)
        except ApiException as e:
            if e.status != 409:
                raise
            # Already exists in Airflow (created out-of-band or re-created CR);
            # adopt it by patching instead of looping on the conflict.
            logger.info(f"Connection {name} already exists in Airflow; adopting")
            connections_api.patch_connection(
                connection_id=name, connection=connection, _preload_content=False
            )
        MANAGED_RESOURCES.labels(resource_type="connection").inc()
    return {"message": f"Connection {name} created successfully."}


@kopf.on.resume("airflow.drfaust92", "v1beta1", "connections")
@kopf.on.update("airflow.drfaust92", "v1beta1", "connections")
@kopf.on.timer(
    "airflow.drfaust92",
    "v1beta1",
    "connections",
    interval=OPERATOR_RECONCILE_INTERVAL,
    initial_delay=OPERATOR_RECONCILE_INTERVAL_DELAY,
)
def reconcile_connection(spec, name, namespace, logger, patch, **kwargs):
    logger.info(f"Reconciling Airflow Connection: {name}")
    with track("connection", "update", logger, patch=patch):
        connection = _build_connection(name, spec, namespace, logger)
        try:
            connections_api.patch_connection(
                connection_id=name, connection=connection, _preload_content=False
            )
        except NotFoundException:
            logger.info(f"Connection {name} missing in Airflow; recreating")
            connections_api.post_connection(connection, _preload_content=False)
    return {"message": f"Connection {name} reconciled successfully."}


@kopf.on.delete(
    "airflow.drfaust92", "v1beta1", "connections", retries=DELETE_MAX_RETRIES
)
def delete_connection(name, namespace, logger, retry=0, **kwargs):
    # retries caps how long a failing delete blocks: after DELETE_MAX_RETRIES
    # attempts kopf gives up and releases the finalizer instead of wedging the
    # resource forever. The final attempt emits a Warning event + metric.
    logger.info(f"Deleting Airflow Connection: {name}")
    with track(
        "connection",
        "delete",
        logger,
        delay=10,
        retry=retry,
        max_retries=DELETE_MAX_RETRIES,
    ):
        try:
            connections_api.delete_connection(
                connection_id=name, _preload_content=False
            )
        except NotFoundException:
            logger.info(f"Connection {name} already absent in Airflow")
        MANAGED_RESOURCES.labels(resource_type="connection").dec()
    return {"message": f"Connection {name} deleted successfully."}
