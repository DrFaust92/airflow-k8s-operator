import kopf
from airflow_client.client.api.connection_api import ConnectionApi
from airflow_client.client.exceptions import NotFoundException
from airflow_client.client.model.connection import Connection

from config.base import OPERATOR_RECONCILE_INTERVAL, OPERATOR_RECONCILE_INTERVAL_DELAY
from config.client import api_client
from config.k8s_secret import resolve_value
from config.metrics import MANAGED_RESOURCES
from config.reconcile import track

connections_api = ConnectionApi(api_client=api_client)


def _build_connection(name, spec, namespace, logger) -> Connection:
    # Resolve sensitive fields from direct values or secret references
    login = (
        resolve_value(spec.get("login"), namespace, logger=logger)
        if spec.get("login")
        else None
    )
    password = (
        resolve_value(spec.get("password"), namespace, logger=logger)
        if spec.get("password")
        else None
    )
    fields = {
        "connection_id": name,
        "conn_type": spec.get("connType"),
        "description": spec.get("description"),
        "host": spec.get("host"),
        "login": login,
        "password": password,
        "port": spec.get("port"),
        "schema": spec.get("schema"),
        "extra": spec.get("extra"),
    }
    # The airflow client model rejects None for optional str fields, so only
    # pass the fields that are actually set.
    return Connection(**{k: v for k, v in fields.items() if v is not None})


@kopf.on.create("airflow.drfaust92", "v1beta1", "connections")
def create_connection(spec, name, namespace, logger, **kwargs):
    logger.info(f"Creating Airflow Connection: {name}")
    with track("connection", "create", logger):
        connections_api.post_connection(
            _build_connection(name, spec, namespace, logger)
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
def reconcile_connection(spec, name, namespace, logger, **kwargs):
    logger.info(f"Reconciling Airflow Connection: {name}")
    with track("connection", "update", logger):
        connection = _build_connection(name, spec, namespace, logger)
        try:
            connections_api.patch_connection(connection_id=name, connection=connection)
        except NotFoundException:
            logger.info(f"Connection {name} missing in Airflow; recreating")
            connections_api.post_connection(connection)
    return {"message": f"Connection {name} reconciled successfully."}


@kopf.on.delete("airflow.drfaust92", "v1beta1", "connections")
def delete_connection(name, namespace, logger, **kwargs):
    logger.info(f"Deleting Airflow Connection: {name}")
    with track("connection", "delete", logger):
        try:
            connections_api.delete_connection(connection_id=name)
        except NotFoundException:
            logger.info(f"Connection {name} already absent in Airflow")
        MANAGED_RESOURCES.labels(resource_type="connection").dec()
    return {"message": f"Connection {name} deleted successfully."}
