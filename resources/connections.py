import kopf
from airflow_client.client.api.connection_api import ConnectionApi
from airflow_client.client.model.connection import Connection
from config.client import api_client
from config.k8s_secret import resolve_value

connections_api = ConnectionApi(api_client=api_client)


@kopf.on.create("airflow.drfaust92", "v1beta1", "connections")
def create_connection(meta, spec, namespace, logger, body, **kwargs):
    connection_id = meta.get("name")
    var_conn_type = spec.get("connType")

    logger.info(
        f"Creating Airflow Connection: {connection_id} with connType: {var_conn_type}"
    )
    try:
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

        connection = Connection(
            connection_id=connection_id,
            conn_type=var_conn_type,
            description=spec.get("description"),
            host=spec.get("host"),
            login=login,
            password=password,
            port=spec.get("port"),
            schema=spec.get("schema"),
            extra=spec.get("extra"),
        )
        connections_api.post_connection(connection)
        return {"message": f"Connection {connection_id} created successfully."}
    except Exception as e:
        logger.error(f"Failed to create Airflow Connection {connection_id}: {e}")
        return {"error": f"Failed to create connection {connection_id}: {e}"}


@kopf.on.delete("airflow.drfaust92", "v1beta1", "connections")
def delete_connection(meta, spec, namespace, logger, body, **kwargs):
    connection_id = meta.get("name")

    logger.info(f"Deleting Airflow Connection: {connection_id}")
    try:
        connections_api.delete_connection(connection_id=connection_id)
        return {"message": f"Connection {connection_id} deleted successfully."}
    except Exception as e:
        # Ignore 404 errors - connection already doesn't exist
        if "404" in str(e) or "Not Found" in str(e):
            logger.info(f"Connection {connection_id} already deleted or doesn't exist")
            return {
                "message": f"Connection {connection_id} already deleted or doesn't exist."
            }
        logger.error(f"Failed to delete Airflow Connection {connection_id}: {e}")
        return {"error": f"Failed to delete connection {connection_id}: {e}"}


@kopf.on.update("airflow.drfaust92", "v1beta1", "connections")
def update_connection(meta, spec, namespace, logger, body, **kwargs):
    connection_id = meta.get("name")
    var_conn_type = spec.get("connType")

    logger.info(
        f"Updating Airflow Connection: {connection_id} with connType: {var_conn_type}"
    )
    try:
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

        connection = Connection(
            connection_id=connection_id,
            conn_type=var_conn_type,
            description=spec.get("description"),
            host=spec.get("host"),
            login=login,
            password=password,
            port=spec.get("port"),
            schema=spec.get("schema"),
            extra=spec.get("extra"),
        )
        connections_api.patch_connection(
            connection_id=connection_id, connection=connection
        )
        return {"message": f"Connection {connection_id} updated successfully."}
    except Exception as e:
        logger.error(f"Failed to update Airflow Connection {connection_id}: {e}")
        return {"error": f"Failed to update connection {connection_id}: {e}"}
