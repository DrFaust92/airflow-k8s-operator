import logging
import os

import airflow_client.client as client

from config.base import AIRFLOW_HOST

logger = logging.getLogger(__name__)

AIRFLOW_USERNAME = os.getenv("AIRFLOW_USERNAME")
AIRFLOW_PASSWORD = os.getenv("AIRFLOW_PASSWORD")
AIRFLOW_ACCESS_TOKEN = os.getenv("AIRFLOW_ACCESS_TOKEN")

# Check if we should use Google Cloud authentication (for Cloud Composer)
USE_GOOGLE_AUTH = os.getenv("USE_GOOGLE_AUTH")
USE_AWS_AUTH = os.getenv("USE_AWS_AUTH")

if USE_GOOGLE_AUTH is not None and USE_GOOGLE_AUTH.lower() in ["true"]:
    from config.gcp import gcp_api_client

    api_client = gcp_api_client
elif USE_AWS_AUTH is not None and USE_AWS_AUTH.lower() in ["true"]:
    from config.aws import aws_api_client

    api_client = aws_api_client
elif AIRFLOW_USERNAME and AIRFLOW_PASSWORD:
    configuration = client.Configuration(
        host=AIRFLOW_HOST, username=AIRFLOW_USERNAME, password=AIRFLOW_PASSWORD
    )
    api_client = client.ApiClient(configuration=configuration)
elif AIRFLOW_ACCESS_TOKEN:
    configuration = client.Configuration(
        host=AIRFLOW_HOST,
        access_token=AIRFLOW_ACCESS_TOKEN,
    )
    api_client = client.ApiClient(configuration=configuration)
else:
    raise RuntimeError(
        "Airflow client authentication is not configured.\n\n"
        + "Configure at least one of the following options:\n"
        + "- Set USE_GOOGLE_AUTH=true for Google Cloud authentication, or\n"
        + "- Set USE_AWS_AUTH=true for AWS authentication, or\n"
        + "- Set AIRFLOW_USERNAME and AIRFLOW_PASSWORD for basic authentication, or\n"
        + "- Set AIRFLOW_ACCESS_TOKEN for token-based authentication."
    )
