import os
import logging

logger = logging.getLogger(__name__)

AIRFLOW_HOST = os.getenv("AIRFLOW_HOST")
OPERATOR_RECONCILE_INTERVAL = int(
    os.getenv("OPERATOR_RECONCILE_INTERVAL", "300")
)  # default to 5 minutes
AIRFLOW_API_BASE_URL = os.getenv(
    "AIRFLOW_API_BASE_URL", "/api/v1"
)  # for airflow api v1 compatibility, airflow v2.
if not AIRFLOW_HOST:
    raise RuntimeError("Environment variable AIRFLOW_HOST must be set")

# Ensure AIRFLOW_HOST includes /api/v1 for the apache-airflow-client
if not AIRFLOW_HOST.endswith(AIRFLOW_API_BASE_URL):
    AIRFLOW_HOST = AIRFLOW_HOST.rstrip("/") + AIRFLOW_API_BASE_URL
    logger.debug(
        f"Appending AIRFLOW_API_BASE_URL to AIRFLOW_HOST. Using: {AIRFLOW_HOST}"
    )
