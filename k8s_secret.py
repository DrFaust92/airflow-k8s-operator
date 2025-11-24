import logging
from kubernetes import client
from collections.abc import Mapping

def _get_secret_value(secret_name: str, secret_key: str, namespace: str, logger=None) -> str:
    """
    Fetch a value from a Kubernetes Secret.
    
    Args:
        secret_name: Name of the Kubernetes Secret
        secret_key: Key within the Secret to retrieve
        namespace: Kubernetes namespace where the Secret is located
        logger: Optional logger for debugging
        
    Returns:
        The value from the Secret
        
    Raises:
        ValueError: If the Secret or key is not found
    """
    try:
        # Ensure we have a usable logger (tests may pass `None`)
        if logger is None:
            logger = logging.getLogger(__name__)

        # Use the Kubernetes API to fetch the secret
        v1 = client.CoreV1Api()
        secret = v1.read_namespaced_secret(secret_name, namespace)
        logger.info(f"Fetched Secret {secret_name} from namespace {namespace}")
        
        # Get the value from the secret data
        if secret.data and secret_key in secret.data:
            # Secret data is base64 encoded, need to decode
            import base64
            value = base64.b64decode(secret.data[secret_key]).decode('utf-8')
            logger.info(f"Successfully fetched value from Secret {secret_name}/{secret_key}")
            return value
        else:
            error_msg = f"Key '{secret_key}' not found in Secret '{secret_name}' in namespace '{namespace}'"
            logger.error(error_msg)
            raise ValueError(error_msg)
            
    except client.exceptions.ApiException as e:
        error_msg = f"Failed to fetch Secret '{secret_name}' from namespace '{namespace}': {e}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    except Exception as e:
        error_msg = f"Error retrieving secret: {e}"
        logger.error(error_msg)
        raise ValueError(error_msg)


def resolve_value(value_spec: dict | Mapping, namespace: str, logger=None) -> str:
    """
    Resolve a value from either a direct value or a secret reference.
    
    Args:
        value_spec: Can be either:
                   - A string (direct value)
                   - A dict with 'secretRef' key pointing to secret reference with 'name' and 'key'
        namespace: Kubernetes namespace for secret lookup
        logger: Optional logger for debugging
        
    Returns:
        The resolved value
        
    Raises:
        ValueError: If the value cannot be resolved
    """
    # Use an internal logger for local logging but preserve the original
    # `logger` argument so callers (and tests) that expect `None` are not
    # surprised when we pass it to `_get_secret_value`.
    effective_logger = logger if logger is not None else logging.getLogger(__name__)

    # Handle string value (backward compatibility)
    if isinstance(value_spec, str):
        return value_spec

    # Handle Mapping (dict-like) with secretRef (even if other keys are present)
    if isinstance(value_spec, Mapping):

        # Otherwise, handle secretRef or a 'value' field
        if 'secretRef' in value_spec:
            secret_ref = value_spec['secretRef']
            if not isinstance(secret_ref, Mapping) or 'name' not in secret_ref or 'key' not in secret_ref:
                raise ValueError("secretRef must contain 'name' and 'key' fields")
            secret_name = secret_ref['name']
            secret_key = secret_ref['key']
            return _get_secret_value(secret_name, secret_key, namespace, logger)

        elif 'value' in value_spec:
            return value_spec['value']
        
        effective_logger.error("secretRef not found in value_spec")

    raise ValueError(f"Invalid value specification: {value_spec}")
