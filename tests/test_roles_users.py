import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import kopf
import pytest
from airflow_client.client.exceptions import ApiException, NotFoundException

import config.auth_manager as auth_manager
import resources.roles as roles
import resources.users as users

logger = logging.getLogger("test")

ROLE_SPEC = {
    "name": "Data Engineers",
    "actions": [{"action": "can_read", "resource": "DAGs"}],
}
USER_SPEC = {
    "email": "jane@example.com",
    "firstName": "Jane",
    "lastName": "Doe",
    "roles": ["Viewer"],
    "passwordSecretRef": {"name": "jane", "key": "password"},
}


def _patch():
    return SimpleNamespace(status={})


@pytest.fixture
def role_api(monkeypatch):
    api = MagicMock()
    monkeypatch.setattr(roles, "auth_manager_api", api)
    return api


@pytest.fixture
def user_api(monkeypatch):
    api = MagicMock()
    monkeypatch.setattr(users, "auth_manager_api", api)
    monkeypatch.setattr(
        users, "get_secret_value_with_version", lambda *a: ("s3cret", "v1")
    )
    return api


# --- roles ----------------------------------------------------------------


def test_create_role_uses_spec_name_and_actions(role_api):
    patch = _patch()
    roles.create_role(
        spec=ROLE_SPEC, name="data-eng", namespace="ns", logger=logger, patch=patch
    )
    role_api.post_role.assert_called_once_with(
        {
            "name": "Data Engineers",
            "actions": [{"action": {"name": "can_read"}, "resource": {"name": "DAGs"}}],
        }
    )
    assert patch.status["phase"] == "Synced"


def test_create_role_defaults_name_to_resource_name(role_api):
    roles.create_role(
        spec={"actions": []},
        name="viewer2",
        namespace="ns",
        logger=logger,
        patch=_patch(),
    )
    assert role_api.post_role.call_args.args[0]["name"] == "viewer2"


def test_create_role_conflict_without_adopt_is_permanent(role_api):
    role_api.post_role.side_effect = ApiException(status=409, reason="exists")
    with pytest.raises(kopf.PermanentError):
        roles.create_role(
            spec=ROLE_SPEC, name="r", namespace="ns", logger=logger, patch=_patch()
        )
    role_api.patch_role.assert_not_called()


def test_create_role_conflict_with_adopt_patches(role_api):
    role_api.post_role.side_effect = ApiException(status=409, reason="exists")
    roles.create_role(
        spec={**ROLE_SPEC, "adopt": True},
        name="r",
        namespace="ns",
        logger=logger,
        patch=_patch(),
    )
    role_api.patch_role.assert_called_once()
    assert role_api.patch_role.call_args.args[0] == "Data Engineers"


def test_reconcile_role_recreates_when_missing(role_api):
    role_api.patch_role.side_effect = NotFoundException(status=404)
    roles.reconcile_role(
        spec=ROLE_SPEC,
        name="r",
        namespace="ns",
        logger=logger,
        patch=_patch(),
        status={"create_role": {"message": "created"}},
    )
    role_api.post_role.assert_called_once()


def test_reconcile_role_skips_unowned(role_api):
    roles.reconcile_role(
        spec=ROLE_SPEC, name="r", namespace="ns", logger=logger, patch=_patch()
    )
    role_api.patch_role.assert_not_called()


def test_delete_role_tolerates_missing(role_api):
    role_api.delete_role.side_effect = NotFoundException(status=404)
    result = roles.delete_role(
        spec=ROLE_SPEC,
        name="r",
        namespace="ns",
        logger=logger,
        status={"create_role": {"message": "created"}},
    )
    assert "deleted" in result["message"]


# --- users ----------------------------------------------------------------


def test_create_user_sends_password_and_records_secret_version(user_api):
    patch = _patch()
    users.create_user(
        spec=USER_SPEC, name="jane", namespace="ns", logger=logger, patch=patch
    )
    body = user_api.post_user.call_args.args[0]
    assert body == {
        "username": "jane",
        "email": "jane@example.com",
        "first_name": "Jane",
        "last_name": "Doe",
        "roles": [{"name": "Viewer"}],
        "password": "s3cret",
    }
    assert patch.status[users.PASSWORD_VERSION_KEY] == "v1"
    # The password must never reach the resource status.
    assert "s3cret" not in str(patch.status)


def test_reconcile_user_omits_password_when_secret_unchanged(user_api):
    users.reconcile_user(
        spec=USER_SPEC,
        name="jane",
        namespace="ns",
        logger=logger,
        patch=_patch(),
        status={"create_user": {"message": "created"}, "passwordSecretVersion": "v1"},
    )
    args, kwargs = user_api.patch_user.call_args
    assert "password" not in args[1]
    assert "password" not in kwargs["update_mask"]


def test_reconcile_user_sends_password_when_secret_changed(user_api):
    patch = _patch()
    users.reconcile_user(
        spec=USER_SPEC,
        name="jane",
        namespace="ns",
        logger=logger,
        patch=patch,
        status={"create_user": {"message": "created"}, "passwordSecretVersion": "v0"},
    )
    args, kwargs = user_api.patch_user.call_args
    assert args[1]["password"] == "s3cret"
    assert "password" in kwargs["update_mask"]
    assert patch.status[users.PASSWORD_VERSION_KEY] == "v1"


def test_reconcile_user_recreates_with_password_when_missing(user_api):
    user_api.patch_user.side_effect = NotFoundException(status=404)
    users.reconcile_user(
        spec=USER_SPEC,
        name="jane",
        namespace="ns",
        logger=logger,
        patch=_patch(),
        status={"create_user": {"message": "created"}, "passwordSecretVersion": "v1"},
    )
    assert user_api.post_user.call_args.args[0]["password"] == "s3cret"


def test_create_user_missing_secret_marks_error(user_api, monkeypatch):
    def boom(*a):
        raise ValueError("Secret 'jane' not found")

    monkeypatch.setattr(users, "get_secret_value_with_version", boom)
    patch = _patch()
    with pytest.raises(kopf.TemporaryError):
        users.create_user(
            spec=USER_SPEC, name="jane", namespace="ns", logger=logger, patch=patch
        )
    user_api.post_user.assert_not_called()
    assert patch.status["phase"] == "Error"


def test_delete_user_leaves_unowned(user_api):
    users.delete_user(spec=USER_SPEC, name="jane", namespace="ns", logger=logger)
    user_api.delete_user.assert_not_called()


# --- auth manager client --------------------------------------------------


@pytest.fixture
def fresh_client_cache():
    auth_manager.get_auth_manager_api_client.cache_clear()
    yield
    auth_manager.get_auth_manager_api_client.cache_clear()


def test_auth_manager_reuses_core_client_on_airflow_2(monkeypatch, fresh_client_cache):
    monkeypatch.setattr(auth_manager, "AIRFLOW_AUTH_MANAGER_BASE_URL", "/api/v1")
    monkeypatch.setattr(auth_manager, "AIRFLOW_API_BASE_URL", "/api/v1")
    assert auth_manager.get_auth_manager_api_client() is auth_manager.api_client


def test_auth_manager_uses_basic_auth_on_airflow_3(monkeypatch, fresh_client_cache):
    monkeypatch.setattr(auth_manager, "AIRFLOW_AUTH_MANAGER_BASE_URL", "/auth/fab/v1")
    monkeypatch.setattr(auth_manager, "AIRFLOW_API_BASE_URL", "/api/v2")
    monkeypatch.setattr(auth_manager, "AIRFLOW_HOST_ROOT", "http://airflow:8080")
    monkeypatch.setattr(auth_manager, "AIRFLOW_USERNAME", "admin")
    monkeypatch.setattr(auth_manager, "AIRFLOW_PASSWORD", "pw")
    c = auth_manager.get_auth_manager_api_client()
    assert c is not auth_manager.api_client
    assert c.configuration.host == "http://airflow:8080/auth/fab/v1"
    assert c.configuration.auth_settings()["Basic"]["value"].startswith("Basic ")


def test_auth_manager_without_credentials_on_airflow_3_is_permanent(
    monkeypatch, fresh_client_cache
):
    monkeypatch.setattr(auth_manager, "AIRFLOW_AUTH_MANAGER_BASE_URL", "/auth/fab/v1")
    monkeypatch.setattr(auth_manager, "AIRFLOW_API_BASE_URL", "/api/v2")
    monkeypatch.setattr(auth_manager, "AIRFLOW_USERNAME", None)
    monkeypatch.setattr(auth_manager, "AIRFLOW_PASSWORD", None)
    with pytest.raises(kopf.PermanentError, match="AIRFLOW_USERNAME"):
        auth_manager.get_auth_manager_api_client()


def test_auth_manager_api_quotes_names_and_sends_update_mask():
    client = MagicMock()
    client.call_api.return_value = SimpleNamespace(data=b'{"name": "x"}')
    api = auth_manager.AuthManagerApi(lambda: client)

    assert api.get_role("Data Engineers") == {"name": "x"}
    assert client.call_api.call_args.args[:2] == ("/roles/Data%20Engineers", "GET")

    api.patch_user("jane", {"email": "e"}, update_mask=["email", "roles"])
    kwargs = client.call_api.call_args.kwargs
    assert client.call_api.call_args.args[:2] == ("/users/jane", "PATCH")
    assert kwargs["query_params"] == [("update_mask", "email,roles")]
    assert kwargs["body"] == {"email": "e"}
