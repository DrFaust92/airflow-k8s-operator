import base64
import json
import logging
import os
import threading
import time

import airflow_client.client as client
import requests
from airflow_client.client.exceptions import UnauthorizedException

from config.base import AIRFLOW_HOST, AIRFLOW_HOST_ROOT, IS_API_V2
from config.metrics import AUTH_FAILURES

logger = logging.getLogger(__name__)

AIRFLOW_USERNAME = os.getenv("AIRFLOW_USERNAME")
AIRFLOW_PASSWORD = os.getenv("AIRFLOW_PASSWORD")
AIRFLOW_ACCESS_TOKEN = os.getenv("AIRFLOW_ACCESS_TOKEN")

# Check if we should use Google Cloud authentication (for Cloud Composer)
USE_GOOGLE_AUTH = os.getenv("USE_GOOGLE_AUTH")
USE_AWS_AUTH = os.getenv("USE_AWS_AUTH")

# Refresh a JWT this many seconds before it expires.
_JWT_REFRESH_GRACE = 60


def _jwt_expiry(token: str) -> float:
    """Best-effort read of a JWT 'exp' claim (no signature verification).

    Returns 0.0 if it can't be parsed, in which case refresh is driven purely
    by 401 responses.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0))
    except Exception:
        return 0.0


class JwtAuthApiClient(client.ApiClient):
    """Airflow 3 (/api/v2) username/password auth.

    The 2.x client only wires HTTP Basic auth, and Airflow 3 requires a JWT
    Bearer token. This client obtains a token from ``{host}/auth/token`` and
    injects it as ``Authorization: Bearer``, refreshing it proactively before
    expiry and reactively on a 401 (so a token that expires mid-reconcile
    self-heals within the same handler call).
    """

    def __init__(self, configuration, host_root, username, password):
        super().__init__(configuration)
        self._token_url = f"{host_root}/auth/token"
        self._username = username
        self._password = password
        self._token = None
        self._expires_at = 0.0
        # kopf reconciles resources on a thread pool; guard the token cache.
        self._lock = threading.Lock()

    def _fetch_token(self):
        try:
            resp = requests.post(
                self._token_url,
                json={"username": self._username, "password": self._password},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            AUTH_FAILURES.labels(auth_type="airflow_jwt").inc()
            raise RuntimeError(
                f"Failed to obtain Airflow JWT from {self._token_url}: {e}"
            ) from e
        token = data.get("access_token") or data.get("token") or data.get("access")
        if not token:
            AUTH_FAILURES.labels(auth_type="airflow_jwt").inc()
            raise RuntimeError(f"Airflow token response missing access_token: {data}")
        self._token = token
        self._expires_at = _jwt_expiry(token)

    def _authorization(self, force=False):
        with self._lock:
            expiring = (
                self._expires_at
                and time.time() >= self._expires_at - _JWT_REFRESH_GRACE
            )
            if force or self._token is None or expiring:
                self._fetch_token()
            return f"Bearer {self._token}"

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
        if header_params is None:
            header_params = {}
        header_params["Authorization"] = self._authorization()
        try:
            return super().call_api(
                resource_path,
                method,
                path_params,
                query_params,
                header_params,
                body,
                post_params,
                files,
                response_type,
                auth_settings,
                async_req,
                _return_http_data_only,
                collection_formats,
                _preload_content,
                _request_timeout,
                _host,
                _check_type,
            )
        except UnauthorizedException:
            # Token likely expired mid-flight; force a refresh and retry once.
            header_params["Authorization"] = self._authorization(force=True)
            return super().call_api(
                resource_path,
                method,
                path_params,
                query_params,
                header_params,
                body,
                post_params,
                files,
                response_type,
                auth_settings,
                async_req,
                _return_http_data_only,
                collection_formats,
                _preload_content,
                _request_timeout,
                _host,
                _check_type,
            )


if USE_GOOGLE_AUTH is not None and USE_GOOGLE_AUTH.lower() in ["true"]:
    from config.gcp import gcp_api_client

    api_client = gcp_api_client
elif USE_AWS_AUTH is not None and USE_AWS_AUTH.lower() in ["true"]:
    from config.aws import aws_api_client

    api_client = aws_api_client
elif AIRFLOW_USERNAME and AIRFLOW_PASSWORD:
    if IS_API_V2:
        # Airflow 3: exchange username/password for a JWT and send it as Bearer.
        configuration = client.Configuration(host=AIRFLOW_HOST)
        api_client = JwtAuthApiClient(
            configuration, AIRFLOW_HOST_ROOT, AIRFLOW_USERNAME, AIRFLOW_PASSWORD
        )
    else:
        # Airflow 2: HTTP Basic auth against /api/v1.
        configuration = client.Configuration(
            host=AIRFLOW_HOST, username=AIRFLOW_USERNAME, password=AIRFLOW_PASSWORD
        )
        api_client = client.ApiClient(configuration=configuration)
elif AIRFLOW_ACCESS_TOKEN:
    # Static bearer token. The 2.x client's Configuration(access_token=...) is
    # ignored (auth_settings only wires Basic), so set the header explicitly.
    # Works for both /api/v1 and /api/v2.
    configuration = client.Configuration(host=AIRFLOW_HOST)
    api_client = client.ApiClient(configuration=configuration)
    api_client.set_default_header("Authorization", f"Bearer {AIRFLOW_ACCESS_TOKEN}")
    logger.warning(
        "Using a static AIRFLOW_ACCESS_TOKEN; it is not refreshed and the "
        "operator will start failing once it expires. For a long-running "
        "operator, prefer AIRFLOW_USERNAME/AIRFLOW_PASSWORD (auto-refreshed "
        "against Airflow 3's /auth/token) or rotate the token out-of-band."
    )
else:
    raise RuntimeError(
        "Airflow client authentication is not configured.\n\n"
        + "Configure at least one of the following options:\n"
        + "- Set USE_GOOGLE_AUTH=true for Google Cloud authentication, or\n"
        + "- Set USE_AWS_AUTH=true for AWS authentication, or\n"
        + "- Set AIRFLOW_USERNAME and AIRFLOW_PASSWORD for basic auth (Airflow 2) "
        + "or JWT auth (Airflow 3), or\n"
        + "- Set AIRFLOW_ACCESS_TOKEN for token-based authentication."
    )
