import os
import airflow_client.client as client
import google.auth
import google.auth.transport.requests
import logging

logger = logging.getLogger(__name__)

AIRFLOW_HOST = os.getenv("AIRFLOW_HOST")
AIRFLOW_API_BASE_URL = os.getenv("AIRFLOW_API_BASE_URL", "/api/v1") # for airflow api v1 compatibility, airflow v2.
if not AIRFLOW_HOST:
	raise RuntimeError("Environment variable AIRFLOW_HOST must be set")

# Ensure AIRFLOW_HOST includes /api/v1 for the apache-airflow-client
if not AIRFLOW_HOST.endswith(AIRFLOW_API_BASE_URL):
	AIRFLOW_HOST = AIRFLOW_HOST.rstrip('/') + AIRFLOW_API_BASE_URL
	logger.debug(f"Appending AIRFLOW_API_BASE_URL to AIRFLOW_HOST. Using: {AIRFLOW_HOST}")

AIRFLOW_USERNAME = os.getenv("AIRFLOW_USERNAME")
AIRFLOW_PASSWORD = os.getenv("AIRFLOW_PASSWORD")

# Check if we should use Google Cloud authentication (for Cloud Composer)
USE_GOOGLE_AUTH = os.getenv("USE_GOOGLE_AUTH")

if USE_GOOGLE_AUTH is not None and USE_GOOGLE_AUTH.lower() in ['true']:
	# Authenticate using Application Default Credentials for Google Cloud Composer
	credentials, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
	auth_req = google.auth.transport.requests.Request()
	credentials.refresh(auth_req)
	
	configuration = client.Configuration(host=AIRFLOW_HOST)
	
	# Create a custom API client that adds the Bearer token to every request
	class GoogleAuthApiClient(client.ApiClient):
		def __init__(self, configuration, credentials, auth_request):
			super().__init__(configuration)
			self._credentials = credentials
			self._auth_request = auth_request
		
		def call_api(self, resource_path, method, path_params=None, query_params=None,
					 header_params=None, body=None, post_params=None, files=None,
					 response_type=None, auth_settings=None, async_req=None,
					 _return_http_data_only=None, collection_formats=None, _preload_content=True,
					 _request_timeout=None, _host=None, _check_type=None):
			# Refresh token if needed before each API call
			if not self._credentials.valid:
				self._credentials.refresh(self._auth_request)
			
			# Set the authorization header
			if header_params is None:
				header_params = {}
			header_params['Authorization'] = f'Bearer {self._credentials.token}'
			
			return super().call_api(
				resource_path, method, path_params=path_params, query_params=query_params,
				header_params=header_params, body=body, post_params=post_params, files=files,
				response_type=response_type, auth_settings=auth_settings, async_req=async_req,
				_return_http_data_only=_return_http_data_only, collection_formats=collection_formats,
				_preload_content=_preload_content, _request_timeout=_request_timeout,
				_host=_host, _check_type=_check_type
			)
	
	api_client = GoogleAuthApiClient(configuration, credentials, auth_req)
elif AIRFLOW_USERNAME and AIRFLOW_PASSWORD:
	configuration = client.Configuration(host=AIRFLOW_HOST, username=AIRFLOW_USERNAME, password=AIRFLOW_PASSWORD)
	api_client = client.ApiClient(configuration=configuration)
else:
	raise RuntimeError("Either USE_GOOGLE_AUTH must be true (default) or AIRFLOW_USERNAME and AIRFLOW_PASSWORD must be set")