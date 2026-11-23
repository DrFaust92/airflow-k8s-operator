"""Client for the Airflow auth manager API, which serves roles and users.

Airflow 2 serves roles and users from the core REST API (/api/v1), so the
operator's regular client is reused. Airflow 3 moved them to the auth manager;
with the FAB auth manager they live under /auth/fab/v1. That API does not
accept the Airflow 3 JWT issued by /auth/token -- it authenticates with the
FAB API auth backends (basic_auth, kerberos_auth, session) -- so on Airflow 3
the operator talks to it with HTTP basic auth using AIRFLOW_USERNAME and
AIRFLOW_PASSWORD. The FAB ``[fab] auth_backends`` setting must include
``airflow.providers.fab.auth_manager.api.auth.backend.basic_auth``.

On Cloud Composer and MWAA the platform's own credentials are reused against
the auth manager path; whether the FAB API accepts them depends on the
platform.
"""

import functools
import json

import airflow_client.client as client
import kopf

from config.base import (
    AIRFLOW_API_BASE_URL,
    AIRFLOW_AUTH_MANAGER_BASE_URL,
    AIRFLOW_HOST_ROOT,
)
from config.client import (
    AIRFLOW_PASSWORD,
    AIRFLOW_USERNAME,
    USE_AWS_AUTH,
    USE_GOOGLE_AUTH,
    api_client,
)


def _enabled(flag) -> bool:
    return flag is not None and flag.lower() == "true"


@functools.cache
def get_auth_manager_api_client() -> client.ApiClient:
    """The API client for roles and users, built on first use.

    Raises kopf.PermanentError when the auth manager API needs credentials the
    operator was not given, so the resource shows a clear phase=Error instead
    of retrying a request that cannot succeed.
    """
    base = AIRFLOW_AUTH_MANAGER_BASE_URL.rstrip("/")
    if base == AIRFLOW_API_BASE_URL.rstrip("/"):
        # Same API as everything else (Airflow 2): same client and auth.
        return api_client

    configuration = client.Configuration(host=f"{AIRFLOW_HOST_ROOT}{base}")
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
        configuration.username = AIRFLOW_USERNAME
        configuration.password = AIRFLOW_PASSWORD
        return client.ApiClient(configuration=configuration)
    raise kopf.PermanentError(
        f"Roles and users are served by the auth manager API at {base}, which "
        "does not accept an access token; set AIRFLOW_USERNAME and "
        "AIRFLOW_PASSWORD (and enable the FAB basic_auth API backend)."
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
