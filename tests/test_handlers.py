import logging
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


# --- connections ----------------------------------------------------------


def test_create_connection_posts(monkeypatch):
    api = MagicMock()
    monkeypatch.setattr(connections, "connections_api", api)

    connections.create_connection(
        spec=CONN_SPEC, name="c1", namespace="ns", logger=logger
    )

    api.post_connection.assert_called_once()
    api.patch_connection.assert_not_called()


def test_create_connection_failure_raises_temporary(monkeypatch):
    api = MagicMock()
    api.post_connection.side_effect = ApiException(status=500, reason="boom")
    monkeypatch.setattr(connections, "connections_api", api)

    with pytest.raises(kopf.TemporaryError):
        connections.create_connection(
            spec=CONN_SPEC, name="c1", namespace="ns", logger=logger
        )


def test_reconcile_connection_patches_when_present(monkeypatch):
    api = MagicMock()
    monkeypatch.setattr(connections, "connections_api", api)

    connections.reconcile_connection(
        spec=CONN_SPEC, name="c1", namespace="ns", logger=logger
    )

    api.patch_connection.assert_called_once()
    api.post_connection.assert_not_called()


def test_reconcile_connection_recreates_when_missing(monkeypatch):
    api = MagicMock()
    api.patch_connection.side_effect = NotFoundException(status=404, reason="Not Found")
    monkeypatch.setattr(connections, "connections_api", api)

    connections.reconcile_connection(
        spec=CONN_SPEC, name="c1", namespace="ns", logger=logger
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
            spec=CONN_SPEC, name="c1", namespace="ns", logger=logger
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
        spec=VAR_SPEC, name="v1", namespace="ns", logger=logger
    )

    api.patch_variable.assert_called_once()
    api.post_variables.assert_called_once()


def test_reconcile_pool_recreates_when_missing(monkeypatch):
    api = MagicMock()
    api.patch_pool.side_effect = NotFoundException(status=404, reason="Not Found")
    monkeypatch.setattr(pools, "pools_api", api)

    pools.reconcile_pool(spec=POOL_SPEC, name="p1", logger=logger)

    api.patch_pool.assert_called_once()
    api.post_pool.assert_called_once()
