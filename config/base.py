import logging
import os

logger = logging.getLogger(__name__)

AIRFLOW_HOST = os.getenv("AIRFLOW_HOST")
OPERATOR_RECONCILE_INTERVAL = int(
    os.getenv("OPERATOR_RECONCILE_INTERVAL", "300")
)  # default to 5 minutes
OPERATOR_RECONCILE_INTERVAL_DELAY = int(
    os.getenv("OPERATOR_RECONCILE_INTERVAL_DELAY", "10")
)  # default to 10 seconds
AIRFLOW_API_BASE_URL = os.getenv(
    "AIRFLOW_API_BASE_URL", "/api/v1"
)  # for airflow api v1 compatibility, airflow v2.
if not AIRFLOW_HOST:
    raise RuntimeError("Environment variable AIRFLOW_HOST must be set")

# Ensure AIRFLOW_HOST includes the API base path (/api/v1 for Airflow 2,
# /api/v2 for Airflow 3) for the apache-airflow-client.
if not AIRFLOW_HOST.endswith(AIRFLOW_API_BASE_URL):
    AIRFLOW_HOST = AIRFLOW_HOST.rstrip("/") + AIRFLOW_API_BASE_URL
    logger.debug(
        f"Appending AIRFLOW_API_BASE_URL to AIRFLOW_HOST. Using: {AIRFLOW_HOST}"
    )

# True when targeting the Airflow 3 REST API (/api/v2). Airflow 2 serves only
# /api/v1 and Airflow 3 only /api/v2, so the base path is the version selector.
IS_API_V2 = AIRFLOW_API_BASE_URL.rstrip("/").endswith("v2")

# Host without the API base path, for endpoints that are NOT under /api/vN
# (notably the Airflow 3 JWT endpoint at {host}/auth/token).
if AIRFLOW_HOST.endswith(AIRFLOW_API_BASE_URL):
    AIRFLOW_HOST_ROOT = AIRFLOW_HOST[: -len(AIRFLOW_API_BASE_URL)].rstrip("/")
else:
    AIRFLOW_HOST_ROOT = AIRFLOW_HOST.rstrip("/")
