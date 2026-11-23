"""Client for the Airflow auth manager API, which serves roles and users.

Airflow 2 serves roles and users from the core REST API (/api/v1), so the
operator's regular client is reused. Airflow 3 moved them to the auth manager;
with the FAB auth manager they live under /auth/fab/v1.

How that API authenticates depends on the FAB provider version:

* FAB provider 3.2+ serves users and roles from FastAPI and authenticates
  with the same Airflow 3 JWT / bearer token as the core API. This is the
  default: the operator uses its configured credentials (JWT from
  /auth/token, static access token, Composer or MWAA) against /auth/fab/v1.
* Older FAB providers serve them from the legacy Flask API, which only accepts
  the FAB API auth backends (basic_auth, kerberos_auth, session). Set
  AIRFLOW_AUTH_MANAGER_BASIC_AUTH=true to use HTTP basic auth with
  AIRFLOW_USERNAME/AIRFLOW_PASSWORD, and enable the FAB basic_auth backend.
"""

import functools
import json
import os

import airflow_client.client as client
import kopf

from config.base import (
    AIRFLOW_API_BASE_URL,
    AIRFLOW_AUTH_MANAGER_BASE_URL,
    AIRFLOW_HOST_ROOT,
    IS_API_V2,
)
from config.client import (
    AIRFLOW_ACCESS_TOKEN,
    AIRFLOW_PASSWORD,
    AIRFLOW_USERNAME,
    USE_AWS_AUTH,
    USE_GOOGLE_AUTH,
    JwtAuthApiClient,
    api_client,
)

# Legacy (pre-3.2) FAB providers only accept the FAB API auth backends.
AIRFLOW_AUTH_MANAGER_BASIC_AUTH = os.getenv("AIRFLOW_AUTH_MANAGER_BASIC_AUTH")


def _enabled(flag) -> bool:
    return flag is not None and flag.lower() == "true"


@functools.cache
def get_auth_manager_api_client() -> client.ApiClient:
    """The API client for roles and users, built on first use.

    Raises kopf.PermanentError when the configured credentials cannot reach
    the auth manager API, so the resource shows a clear phase=Error instead of
    retrying a request that cannot succeed.
    """
    base = AIRFLOW_AUTH_MANAGER_BASE_URL.rstrip("/")
    if base == AIRFLOW_API_BASE_URL.rstrip("/"):
        # Same API as everything else (Airflow 2): same client and auth.
        return api_client

    configuration = client.Configuration(host=f"{AIRFLOW_HOST_ROOT}{base}")
    if _enabled(AIRFLOW_AUTH_MANAGER_BASIC_AUTH):
        if not (AIRFLOW_USERNAME and AIRFLOW_PASSWORD):
            raise kopf.PermanentError(
                "AIRFLOW_AUTH_MANAGER_BASIC_AUTH is set but AIRFLOW_USERNAME "
                "and AIRFLOW_PASSWORD are not."
            )
        configuration.username = AIRFLOW_USERNAME
        configuration.password = AIRFLOW_PASSWORD
        return client.ApiClient(configuration=configuration)

    # Same credentials as the core API, against the auth manager base path.
    if _enabled(USE_GOOGLE_AUTH):
        from config.gcp import GoogleAuthApiClient, auth_req, credentials

        return GoogleAuthApiClient(configuration, credentials, auth_req)
    if _enabled(USE_AWS_AUTH):
        from config.aws import (
            AWS_REGION,
            MWAA_ENV_NAME,
            MWAA_LOGIN_PATH,
            AWSAuthApiClient,
        )

        return AWSAuthApiClient(
            configuration,
            AWS_REGION,
            MWAA_ENV_NAME,
            MWAA_LOGIN_PATH,
            None,
            api_base_url=base,
        )
    if AIRFLOW_USERNAME and AIRFLOW_PASSWORD:
        if IS_API_V2:
            return JwtAuthApiClient(
                configuration, AIRFLOW_HOST_ROOT, AIRFLOW_USERNAME, AIRFLOW_PASSWORD
            )
        configuration.username = AIRFLOW_USERNAME
        configuration.password = AIRFLOW_PASSWORD
        return client.ApiClient(configuration=configuration)
    if AIRFLOW_ACCESS_TOKEN:
        token_client = client.ApiClient(configuration=configuration)
        token_client.set_default_header(
            "Authorization", f"Bearer {AIRFLOW_ACCESS_TOKEN}"
        )
        return token_client
    raise kopf.PermanentError(
        "No credentials are configured for the auth manager API that serves "
        "roles and users."
    )


class AuthManagerApi:
    """Roles and users endpoints of the auth manager (FAB) REST API.

    Calls go through the configured client's ``call_api`` so every auth mode
    (basic, JWT, static token, Composer, MWAA) applies unchanged, and HTTP
    errors surface as the client's ApiException / NotFoundException like the
    other resources.
    """

    def __init__(self, api_client_factory=get_auth_manager_api_client):
        self._factory = api_client_factory

    def _call(self, method, path, body=None, query=None):
        resp = self._factory().call_api(
            path,
            method,
            query_params=list((query or {}).items()),
            header_params={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            body=body,
            auth_settings=["Basic"],
            _return_http_data_only=True,
            _preload_content=False,
        )
        data = getattr(resp, "data", None)
        return json.loads(data) if data else None

    # Roles
    def get_role(self, name):
        return self._call("GET", f"/roles/{_quote(name)}")

    def post_role(self, body):
        return self._call("POST", "/roles", body=body)

    def patch_role(self, name, body):
        return self._call("PATCH", f"/roles/{_quote(name)}", body=body)

    def delete_role(self, name):
        return self._call("DELETE", f"/roles/{_quote(name)}")

    # Users
    def get_user(self, username):
        return self._call("GET", f"/users/{_quote(username)}")

    def post_user(self, body):
        return self._call("POST", "/users", body=body)

    def patch_user(self, username, body, update_mask):
        return self._call(
            "PATCH",
            f"/users/{_quote(username)}",
            body=body,
            query={"update_mask": ",".join(update_mask)},
        )

    def delete_user(self, username):
        return self._call("DELETE", f"/users/{_quote(username)}")


def _quote(segment: str) -> str:
    from urllib.parse import quote

    return quote(segment, safe="")
