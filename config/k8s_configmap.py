import base64
import logging

from kubernetes import client


def get_config_map_value(
    config_map_name: str, config_map_key: str, namespace: str, logger=None
) -> bytes:
    """
    Fetch a value from a Kubernetes ConfigMap.

    Looks the key up in ``data`` (plain text) first, then in ``binaryData``
    (base64-encoded). Always returns bytes so callers can handle both text
    DAG files and binary zip archives uniformly.

    Raises:
        ValueError: If the ConfigMap or key is not found
    """
    try:
        if logger is None:
            logger = logging.getLogger(__name__)

        v1 = client.CoreV1Api()
        config_map = v1.read_namespaced_config_map(config_map_name, namespace)
        logger.info(
            f"Fetched ConfigMap {config_map_name} from namespace {namespace}"
        )

        if config_map.data and config_map_key in config_map.data:
            return config_map.data[config_map_key].encode("utf-8")
        if config_map.binary_data and config_map_key in config_map.binary_data:
            return base64.b64decode(config_map.binary_data[config_map_key])

        error_msg = (
            f"Key '{config_map_key}' not found in ConfigMap '{config_map_name}' "
            f"in namespace '{namespace}'"
        )
        logger.error(error_msg)
        raise ValueError(error_msg)

    except client.exceptions.ApiException as e:
        error_msg = (
            f"Failed to fetch ConfigMap '{config_map_name}' from namespace "
            f"'{namespace}': {e}"
        )
        logger.error(error_msg)
        raise ValueError(error_msg)
    except ValueError:
        raise
    except Exception as e:
        error_msg = f"Error retrieving ConfigMap: {e}"
        logger.error(error_msg)
        raise ValueError(error_msg)
