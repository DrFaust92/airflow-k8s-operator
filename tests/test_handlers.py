import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import kopf
import pytest
from airflow_client.client.exceptions import ApiException, NotFoundException

import resources.connections as connections
import resources.dags as dags
import resources.pools as pools
import resources.variables as variables

logger = logging.getLogger("test")

CONN_SPEC = {"connType": "http", "host": "example.com"}
VAR_SPEC = {"value": "v"}
POOL_SPEC = {"slots": 5, "description": "d"}
DAG_SPEC = {"dagId": "my_dag", "isPaused": True}


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


# --- dags: adopt-only contract (no create endpoint in the Airflow API) ----


def test_create_dag_patches_and_marks_synced(monkeypatch):
    api = MagicMock()
    monkeypatch.setattr(dags, "dags_api", api)
    patch = _patch()

    dags.create_dag(spec=DAG_SPEC, name="d1", namespace="ns", logger=logger, patch=patch)

    api.patch_dag.assert_called_once()
    kwargs = api.patch_dag.call_args.kwargs
    assert kwargs["dag_id"] == "my_dag"
    assert kwargs["update_mask"] == ["is_paused"]
    assert kwargs["dag"].is_paused is True
    assert patch.status["phase"] == "Synced"


def test_dag_id_falls_back_to_resource_name(monkeypatch):
    api = MagicMock()
    monkeypatch.setattr(dags, "dags_api", api)

    dags.create_dag(
        spec={"isPaused": False},
        name="my-dag-cr",
        namespace="ns",
        logger=logger,
        patch=_patch(),
    )

    assert api.patch_dag.call_args.kwargs["dag_id"] == "my-dag-cr"


def test_reconcile_dag_not_found_is_temporary_with_parse_message(monkeypatch):
    # No create endpoint exists, so a missing DAG (file not parsed yet or
    # removed) must keep retrying with a distinct message, not recreate.
    api = MagicMock()
    api.patch_dag.side_effect = NotFoundException(status=404, reason="Not Found")
    monkeypatch.setattr(dags, "dags_api", api)
    patch = _patch()

    with pytest.raises(kopf.TemporaryError):
        dags.reconcile_dag(spec=DAG_SPEC, name="d1", namespace="ns", logger=logger, patch=patch)

    assert patch.status["phase"] == "Error"
    assert "waiting for it to be parsed" in patch.status["message"]


def test_reconcile_dag_bad_request_is_permanent(monkeypatch):
    api = MagicMock()
    api.patch_dag.side_effect = ApiException(status=400, reason="bad spec")
    monkeypatch.setattr(dags, "dags_api", api)

    with pytest.raises(kopf.PermanentError):
        dags.reconcile_dag(spec=DAG_SPEC, name="d1", namespace="ns", logger=logger, patch=_patch())


def test_reconcile_dag_other_error_raises_temporary(monkeypatch):
    api = MagicMock()
    api.patch_dag.side_effect = ApiException(status=503, reason="unavailable")
    monkeypatch.setattr(dags, "dags_api", api)

    with pytest.raises(kopf.TemporaryError):
        dags.reconcile_dag(spec=DAG_SPEC, name="d1", namespace="ns", logger=logger, patch=_patch())


def test_delete_dag_calls_airflow_and_ignores_not_found(monkeypatch):
    api = MagicMock()
    api.delete_dag.side_effect = NotFoundException(status=404, reason="Not Found")
    monkeypatch.setattr(dags, "dags_api", api)

    dags.delete_dag(spec=DAG_SPEC, name="d1", namespace="ns", logger=logger)
    assert api.delete_dag.call_args.kwargs["dag_id"] == "my_dag"


def test_delete_dag_other_error_raises_temporary(monkeypatch):
    api = MagicMock()
    api.delete_dag.side_effect = ApiException(status=500, reason="boom")
    monkeypatch.setattr(dags, "dags_api", api)

    with pytest.raises(kopf.TemporaryError):
        dags.delete_dag(spec=DAG_SPEC, name="d1", namespace="ns", logger=logger)


# --- dags: source delivery (spec.source -> DAGs folder) --------------------


class FakeBackend:
    def __init__(self):
        self.store = {}
        self.writes = 0

    def read(self, name):
        return self.store.get(name)

    def write(self, name, data):
        self.store[name] = data
        self.writes += 1

    def delete(self, name):
        self.store.pop(name, None)


SOURCED_DAG_SPEC = {
    "dagId": "my_dag",
    "isPaused": True,
    "source": {"code": "print('hello')\n"},
}


def _sourced(monkeypatch):
    api = MagicMock()
    monkeypatch.setattr(dags, "dags_api", api)
    backend = FakeBackend()
    monkeypatch.setattr(dags, "get_backend", lambda: backend)
    return api, backend


def test_sourced_create_uploads_with_marker_then_patches(monkeypatch):
    api, backend = _sourced(monkeypatch)
    patch = _patch()

    dags.create_dag(
        spec=SOURCED_DAG_SPEC, name="d1", namespace="ns", logger=logger, patch=patch
    )

    uploaded = backend.store["my_dag.py"]
    assert uploaded.startswith(b"# managed-by: airflow-k8s-operator dag=ns/d1\n")
    assert b"print('hello')" in uploaded
    api.patch_dag.assert_called_once()
    assert patch.status["phase"] == "Synced"


def test_sourced_reconcile_is_idempotent(monkeypatch):
    api, backend = _sourced(monkeypatch)

    for _ in range(2):
        dags.reconcile_dag(
            spec=SOURCED_DAG_SPEC,
            name="d1",
            namespace="ns",
            logger=logger,
            patch=_patch(),
        )

    assert backend.writes == 1


def test_sourced_zip_uploads_with_comment_marker(monkeypatch):
    import io
    import zipfile

    api, backend = _sourced(monkeypatch)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("my_dag.py", "print('zipped')\n")
    spec = {
        "dagId": "my_dag",
        "isPaused": True,
        "source": {"configMapRef": {"name": "cm", "key": "dag.zip"}},
    }
    monkeypatch.setattr(dags, "get_config_map_value", lambda *a, **k: buf.getvalue())

    dags.create_dag(spec=spec, name="d1", namespace="ns", logger=logger, patch=_patch())

    uploaded = backend.store["my_dag.zip"]
    comment = zipfile.ZipFile(io.BytesIO(uploaded)).comment.decode()
    assert comment == "managed-by: airflow-k8s-operator dag=ns/d1"


def test_unsourced_dag_never_touches_storage(monkeypatch):
    api = MagicMock()
    monkeypatch.setattr(dags, "dags_api", api)
    backend_factory = MagicMock()
    monkeypatch.setattr(dags, "get_backend", backend_factory)

    dags.reconcile_dag(
        spec=DAG_SPEC, name="d1", namespace="ns", logger=logger, patch=_patch()
    )

    backend_factory.assert_not_called()


def test_sourced_dag_without_backend_is_permanent(monkeypatch):
    api = MagicMock()
    monkeypatch.setattr(dags, "dags_api", api)
    monkeypatch.setattr(dags, "get_backend", lambda: None)

    with pytest.raises(kopf.PermanentError):
        dags.create_dag(
            spec=SOURCED_DAG_SPEC,
            name="d1",
            namespace="ns",
            logger=logger,
            patch=_patch(),
        )
    api.patch_dag.assert_not_called()


def test_sourced_dag_refuses_foreign_file(monkeypatch):
    api, backend = _sourced(monkeypatch)
    backend.store["my_dag.py"] = b"# hand-managed\nprint('mine')\n"

    with pytest.raises(kopf.PermanentError):
        dags.create_dag(
            spec=SOURCED_DAG_SPEC,
            name="d1",
            namespace="ns",
            logger=logger,
            patch=_patch(),
        )
    assert backend.store["my_dag.py"] == b"# hand-managed\nprint('mine')\n"


def test_sourced_dag_syntax_error_is_permanent(monkeypatch):
    api, backend = _sourced(monkeypatch)
    spec = {**SOURCED_DAG_SPEC, "source": {"code": "def broken(:\n"}}

    with pytest.raises(kopf.PermanentError):
        dags.create_dag(
            spec=spec, name="d1", namespace="ns", logger=logger, patch=_patch()
        )
    assert backend.store == {}


def test_sourced_dag_url_sha256_mismatch_is_permanent(monkeypatch):
    api, backend = _sourced(monkeypatch)
    resp = MagicMock()
    resp.content = b"print('x')\n"
    monkeypatch.setattr(dags.requests, "get", lambda *a, **k: resp)
    spec = {
        **SOURCED_DAG_SPEC,
        "source": {"url": {"url": "https://example.com/dag.py", "sha256": "0" * 64}},
    }

    with pytest.raises(kopf.PermanentError):
        dags.create_dag(
            spec=spec, name="d1", namespace="ns", logger=logger, patch=_patch()
        )
    assert backend.store == {}


def test_sourced_delete_removes_owned_file_and_dag(monkeypatch):
    api, backend = _sourced(monkeypatch)
    dags.create_dag(
        spec=SOURCED_DAG_SPEC, name="d1", namespace="ns", logger=logger, patch=_patch()
    )
    assert "my_dag.py" in backend.store

    dags.delete_dag(spec=SOURCED_DAG_SPEC, name="d1", namespace="ns", logger=logger)

    assert backend.store == {}
    api.delete_dag.assert_called_once()


def test_sourced_delete_leaves_foreign_file(monkeypatch):
    api, backend = _sourced(monkeypatch)
    backend.store["my_dag.py"] = b"# hand-managed\nprint('mine')\n"

    dags.delete_dag(spec=SOURCED_DAG_SPEC, name="d1", namespace="ns", logger=logger)

    assert backend.store["my_dag.py"] == b"# hand-managed\nprint('mine')\n"
    api.delete_dag.assert_called_once()


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


def test_create_connection_rejected_team_name_is_permanent(monkeypatch):
    # Multi-team validation (or a missing team) makes Airflow reject the
    # request with a 422. The operator must surface that as phase=Error and not
    # loop on it -- not silently succeed.
    monkeypatch.setattr(connections, "IS_API_V2", True)
    api = MagicMock()
    api.post_connection.side_effect = ApiException(status=422, reason="unknown team")
    monkeypatch.setattr(connections, "connections_api", api)
    patch = _patch()

    with pytest.raises(kopf.PermanentError):
        connections.create_connection(
            spec={**CONN_SPEC, "teamName": "ghost-team"},
            name="c1",
            namespace="ns",
            logger=logger,
            patch=patch,
        )
    assert patch.status["phase"] == "Error"
