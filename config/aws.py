import logging
import os

import airflow_client.client as client
import boto3
import requests

from config.base import AIRFLOW_API_BASE_URL
from config.metrics import AUTH_FAILURES

logger = logging.getLogger(__name__)

AWS_REGION = os.getenv("AWS_REGION")
MWAA_ENV_NAME = os.getenv("MWAA_ENV_NAME")
MWAA_LOGIN_PATH = os.getenv("MWAA_LOGIN_PATH", "/pluginsv2/aws_mwaa/login")

if not AWS_REGION or not MWAA_ENV_NAME:
    raise RuntimeError(
        "AWS_REGION and MWAA_ENV_NAME must be set when USE_AWS_AUTH is enabled"
    )


def get_token_info(region, env_name, login_path):
    try:
        # Initialize MWAA client and request a web login token
        mwaa = boto3.client("mwaa", region_name=region)
        response = mwaa.create_web_login_token(Name=env_name)
        # Extract the web server hostname and login token
        web_server_host_name = response["WebServerHostname"]
        web_token = response["WebToken"]
        # Construct the URL needed for authentication
        login_url = f"https://{web_server_host_name}{login_path}"
        login_payload = {"token": web_token}
        # Make a POST request to the MWAA login url using the login payload
        response = requests.post(login_url, data=login_payload, timeout=10)
        # Check if login was successful
        if response.status_code == 200:
            # Safely retrieve the session cookie
            session_token = response.cookies.get("_token")
            if not session_token:
                logging.error(
                    "Login succeeded (HTTP 200) but session token cookie '_token' is missing"
                )
                AUTH_FAILURES.labels(auth_type="aws").inc()
                return None
            # Return the hostname and the session cookie
            return (f"https://{web_server_host_name}", session_token)
        else:
            # Log an error
            logging.error("Failed to log in: HTTP %d", response.status_code)
            AUTH_FAILURES.labels(auth_type="aws").inc()
            return None
    except requests.RequestException as e:
        # Log any exceptions raised during the request to the MWAA login endpoint
        logging.error("Request failed: %s", str(e))
        AUTH_FAILURES.labels(auth_type="aws").inc()
        return None
    except Exception as e:
        # Log any other unexpected exceptions
        logging.error("An unexpected error occurred: %s", str(e))
        AUTH_FAILURES.labels(auth_type="aws").inc()
        return None


class AWSAuthApiClient(client.ApiClient):
    def __init__(self, configuration, region, env_name, login_path, credentials):
        super().__init__(configuration)
        self._region = region
        self._env_name = env_name
        self._login_path = login_path
        self._credentials = credentials

    def call_api(
        self,
        resource_path,
        method,
        path_params=None,
        query_params=None,
        header_params=None,
        body=None,
        post_params=None,
        files=None,
        response_type=None,
        auth_settings=None,
        async_req=None,
        _return_http_data_only=None,
        collection_formats=None,
        _preload_content=True,
        _request_timeout=None,
        _host=None,
        _check_type=None,
    ):
        # Refresh MWAA session token before each API call
        refreshed = get_token_info(self._region, self._env_name, self._login_path)
        if not refreshed:
            logger.error("Failed to refresh MWAA session token")
            AUTH_FAILURES.labels(auth_type="aws").inc()
            raise RuntimeError(
                "MWAA authentication failed; cannot obtain session token"
            )

        self._credentials = refreshed

        # Set the authorization header
        if header_params is None:
            header_params = {}
        header_params["Authorization"] = f"Bearer {self._credentials[1]}"
        header_params["Content-Type"] = "application/json"

        # Ensure the call targets the correct host + API base path
        host = f"{self._credentials[0].rstrip('/')}{AIRFLOW_API_BASE_URL}"

        return super().call_api(
            resource_path,
            method,
            path_params=path_params,
            query_params=query_params,
            header_params=header_params,
            body=body,
            post_params=post_params,
            files=files,
            response_type=response_type,
            auth_settings=auth_settings,
            async_req=async_req,
            _return_http_data_only=_return_http_data_only,
            collection_formats=collection_formats,
            _preload_content=_preload_content,
            _request_timeout=_request_timeout,
            _host=host,
            _check_type=_check_type,
        )


# Defer MWAA token acquisition until the first API call to avoid import-time failures.
credentials = None

# Configuration host will be overridden per call via the `_host` parameter in `call_api`.
configuration = client.Configuration(host=AIRFLOW_API_BASE_URL)
aws_api_client = AWSAuthApiClient(
    configuration, AWS_REGION, MWAA_ENV_NAME, MWAA_LOGIN_PATH, credentials
)
