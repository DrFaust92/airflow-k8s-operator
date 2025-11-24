import os
import airflow_client.client as client
import logging
from config.gcp import gcp_api_client
from config.base import AIRFLOW_HOST

logger = logging.getLogger(__name__)

AIRFLOW_USERNAME = os.getenv("AIRFLOW_USERNAME")
AIRFLOW_PASSWORD = os.getenv("AIRFLOW_PASSWORD")

# Check if we should use Google Cloud authentication (for Cloud Composer)
USE_GOOGLE_AUTH = os.getenv("USE_GOOGLE_AUTH")

if USE_GOOGLE_AUTH is not None and USE_GOOGLE_AUTH.lower() in ["true"]:
    api_client = gcp_api_client
elif AIRFLOW_USERNAME and AIRFLOW_PASSWORD:
    configuration = client.Configuration(
        host=AIRFLOW_HOST, username=AIRFLOW_USERNAME, password=AIRFLOW_PASSWORD
    )
    api_client = client.ApiClient(configuration=configuration)
else:
    raise RuntimeError(
        "Either USE_GOOGLE_AUTH must be true (default) or AIRFLOW_USERNAME and AIRFLOW_PASSWORD must be set"
    )
