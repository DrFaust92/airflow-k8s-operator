import logging

import airflow_client.client as client
import google.auth
import google.auth.transport.requests

from config.base import AIRFLOW_HOST
from config.metrics import AUTH_FAILURES

logger = logging.getLogger(__name__)

# Authenticate using Application Default Credentials for Google Cloud Composer
try:
    credentials, project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
except Exception as e:
    logger.error(f"Failed to authenticate with Google Cloud: {e}")
    AUTH_FAILURES.labels(auth_type="google_cloud").inc()
    raise

configuration = client.Configuration(host=AIRFLOW_HOST)


# Create a custom API client that adds the Bearer token to every request
class GoogleAuthApiClient(client.ApiClient):
    def __init__(self, configuration, credentials, auth_request):
        super().__init__(configuration)
        self._credentials = credentials
        self._auth_request = auth_request

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
        # Refresh token if needed before each API call
        if not self._credentials.valid:
            try:
                self._credentials.refresh(self._auth_request)
            except Exception as e:
                logger.error(f"Failed to refresh Google Cloud credentials: {e}")
                AUTH_FAILURES.labels(auth_type="google_cloud").inc()
                raise

        # Set the authorization header
        if header_params is None:
            header_params = {}
        header_params["Authorization"] = f"Bearer {self._credentials.token}"

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
            _host=_host,
            _check_type=_check_type,
        )


gcp_api_client = GoogleAuthApiClient(configuration, credentials, auth_req)
