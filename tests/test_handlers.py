import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import kopf
import pytest
from airflow_client.client.exceptions import ApiException, NotFoundException

import resources.connections as connections
import resources.pools as pools
import resources.variables as variables

logger = logging.getLogger("test")

CONN_SPEC = {"connType": "http", "host": "example.com"}
VAR_SPEC = {"value": "v"}
POOL_SPEC = {"slots": 5, "description": "d"}


def _patch():
    """A stand-in for kopf's ``patch`` whose ``.status`` is a plain dict."""
    return SimpleNamespace(status={})


# --- connections ----------------------------------------------------------


def test_create_connection_posts_and_marks_synced(monkeypatch):
    api = MagicMock()
    monkeypatch.setattr(connections, "connections_api", api)
    patch = _patch()

    connections.create_connection(
        spec=CONN_SPEC, name="c1", namespace="ns", logger=logger, patch=patch
    )

    api.post_connection.assert_called_once()
    api.patch_connection.assert_not_called()
    assert patch.status["phase"] == "Synced"


def test_create_connection_failure_raises_and_marks_error(monkeypatch):
    api = MagicMock()
    api.post_connection.side_effect = ApiException(status=500, reason="boom")
    monkeypatch.setattr(connections, "connections_api", api)
    patch = _patch()

    with pytest.raises(kopf.TemporaryError):
        connections.create_connection(
            spec=CONN_SPEC, name="c1", namespace="ns", logger=logger, patch=patch
        )
    assert patch.status["phase"] == "Error"


def test_create_connection_adopts_on_conflict(monkeypatch):
    api = MagicMock()
    api.post_connection.side_effect = ApiException(status=409, reason="exists")
    monkeypatch.setattr(connections, "connections_api", api)
    patch = _patch()

    connections.create_connection(
        spec=CONN_SPEC, name="c1", namespace="ns", logger=logger, patch=patch
    )

    # 409 on create -> adopt via patch, and end up Synced (no conflict loop).
    api.post_connection.assert_called_once()
    api.patch_connection.assert_called_once()
    assert patch.status["phase"] == "Synced"


def test_create_connection_bad_request_is_permanent(monkeypatch):
    api = MagicMock()
    api.post_connection.side_effect = ApiException(status=400, reason="bad spec")
    monkeypatch.setattr(connections, "connections_api", api)

    # A rejected spec must not retry forever.
    with pytest.raises(kopf.PermanentError):
        connections.create_connection(
            spec=CONN_SPEC, name="c1", namespace="ns", logger=logger, patch=_patch()
        )


def test_reconcile_connection_patches_when_present(monkeypatch):
    api = MagicMock()
    monkeypatch.setattr(connections, "connections_api", api)

    connections.reconcile_connection(
        spec=CONN_SPEC, name="c1", namespace="ns", logger=logger, patch=_patch()
    )

    api.patch_connection.assert_called_once()
    api.post_connection.assert_not_called()


def test_reconcile_connection_recreates_when_missing(monkeypatch):
    api = MagicMock()
    api.patch_connection.side_effect = NotFoundException(status=404, reason="Not Found")
    monkeypatch.setattr(connections, "connections_api", api)

    connections.reconcile_connection(
        spec=CONN_SPEC, name="c1", namespace="ns", logger=logger, patch=_patch()
    )

    # Drift heal: patch 404s, so it falls back to creating the connection.
    api.patch_connection.assert_called_once()
    api.post_connection.assert_called_once()


def test_reconcile_connection_other_error_raises_temporary(monkeypatch):
    api = MagicMock()
    api.patch_connection.side_effect = ApiException(status=503, reason="unavailable")
    monkeypatch.setattr(connections, "connections_api", api)

    with pytest.raises(kopf.TemporaryError):
        connections.reconcile_connection(
            spec=CONN_SPEC, name="c1", namespace="ns", logger=logger, patch=_patch()
        )


def test_delete_connection_ignores_not_found(monkeypatch):
    api = MagicMock()
    api.delete_connection.side_effect = NotFoundException(
        status=404, reason="Not Found"
    )
    monkeypatch.setattr(connections, "connections_api", api)

    # Already-absent delete is a no-op success, not a raise (so the finalizer
    # is released cleanly).
    connections.delete_connection(name="c1", namespace="ns", logger=logger)
    api.delete_connection.assert_called_once()


def test_delete_connection_other_error_raises_temporary(monkeypatch):
    api = MagicMock()
    api.delete_connection.side_effect = ApiException(status=500, reason="boom")
    monkeypatch.setattr(connections, "connections_api", api)

    with pytest.raises(kopf.TemporaryError):
        connections.delete_connection(name="c1", namespace="ns", logger=logger)


# --- variables & pools: same upsert contract -----------------------------


def test_reconcile_variable_recreates_when_missing(monkeypatch):
    api = MagicMock()
    api.patch_variable.side_effect = NotFoundException(status=404, reason="Not Found")
    monkeypatch.setattr(variables, "variables_api", api)

    variables.reconcile_variable(
        spec=VAR_SPEC, name="v1", namespace="ns", logger=logger, patch=_patch()
    )

    api.patch_variable.assert_called_once()
    api.post_variables.assert_called_once()


def test_reconcile_pool_recreates_when_missing(monkeypatch):
    api = MagicMock()
    api.patch_pool.side_effect = NotFoundException(status=404, reason="Not Found")
    monkeypatch.setattr(pools, "pools_api", api)

    pools.reconcile_pool(spec=POOL_SPEC, name="p1", logger=logger, patch=_patch())

    api.patch_pool.assert_called_once()
    api.post_pool.assert_called_once()


# --- team_name (Airflow 3 / v2 multi-team field) --------------------------


def test_build_connection_includes_team_name_on_v2(monkeypatch):
    monkeypatch.setattr(connections, "IS_API_V2", True)
    conn = connections._build_connection(
        "c1", {**CONN_SPEC, "teamName": "team-a"}, "ns", logger
    )
    assert conn.to_dict().get("team_name") == "team-a"


def test_build_connection_omits_team_name_on_v1(monkeypatch):
    monkeypatch.setattr(connections, "IS_API_V2", False)
    conn = connections._build_connection(
        "c1", {**CONN_SPEC, "teamName": "team-a"}, "ns", logger
    )
    assert "team_name" not in conn.to_dict()


def test_build_variable_includes_team_name_on_v2(monkeypatch):
    monkeypatch.setattr(variables, "IS_API_V2", True)
    var = variables._build_variable(
        "v1", {**VAR_SPEC, "teamName": "team-a"}, "ns", logger
    )
    assert var.to_dict().get("team_name") == "team-a"


def test_build_pool_includes_team_name_on_v2(monkeypatch):
    monkeypatch.setattr(pools, "IS_API_V2", True)
    pool = pools._build_pool("p1", {**POOL_SPEC, "teamName": "team-a"})
    assert pool.to_dict().get("team_name") == "team-a"
