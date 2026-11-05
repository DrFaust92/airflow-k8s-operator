import hashlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import kopf
import pytest
from airflow_client.client.exceptions import ApiException, NotFoundException

import config.reconcile as reconcile
import resources.connections as connections
import resources.dags as dags
import resources.pools as pools
import resources.variables as variables

logger = logging.getLogger("test")

CONN_SPEC = {"connType": "http", "host": "example.com"}
VAR_SPEC = {"value": "v"}
POOL_SPEC = {"slots": 5, "description": "d"}
DAG_SPEC = {"dagId": "my_dag", "isPaused": True}


def _owned(kind):
    """Status of a resource whose create handler succeeded (kopf stores the
    handler result under status.<handler id>)."""
    return {f"create_{kind}": {"message": "created"}}


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


def test_create_connection_conflict_without_adopt_is_permanent(monkeypatch):
    # An object that already exists in Airflow is someone else's (another
    # namespace's resource, or created by hand) unless spec.adopt says so.
    api = MagicMock()
    api.post_connection.side_effect = ApiException(status=409, reason="exists")
    monkeypatch.setattr(connections, "connections_api", api)
    patch = _patch()

    with pytest.raises(kopf.PermanentError):
        connections.create_connection(
            spec=CONN_SPEC, name="c1", namespace="ns", logger=logger, patch=patch
        )

    api.patch_connection.assert_not_called()
    assert patch.status["phase"] == "Error"
    assert "spec.adopt" in patch.status["message"]


def test_reconcile_connection_skips_unowned(monkeypatch):
    api = MagicMock()
    monkeypatch.setattr(connections, "connections_api", api)

    connections.reconcile_connection(
        spec=CONN_SPEC, name="c1", namespace="ns", logger=logger, patch=_patch()
    )

    api.patch_connection.assert_not_called()
    api.post_connection.assert_not_called()


def test_delete_connection_leaves_unowned(monkeypatch):
    api = MagicMock()
    monkeypatch.setattr(connections, "connections_api", api)

    connections.delete_connection(
        spec=CONN_SPEC, name="c1", namespace="ns", logger=logger
    )

    api.delete_connection.assert_not_called()


def test_create_variable_existing_key_without_adopt_is_permanent(monkeypatch):
    # Airflow 2 upserts on POST /variables (no 409), so an existing key must be
    # caught before posting.
    api = MagicMock()
    monkeypatch.setattr(variables, "variables_api", api)

    with pytest.raises(kopf.PermanentError):
        variables.create_variable(
            spec=VAR_SPEC, name="v1", namespace="ns", logger=logger, patch=_patch()
        )
    api.post_variables.assert_not_called()


def test_create_variable_posts_when_key_is_free(monkeypatch):
    api = MagicMock()
    api.get_variable.side_effect = NotFoundException(status=404, reason="Not Found")
    monkeypatch.setattr(variables, "variables_api", api)
    patch = _patch()

    variables.create_variable(
        spec=VAR_SPEC, name="v1", namespace="ns", logger=logger, patch=patch
    )
    api.post_variables.assert_called_once()
    assert patch.status["phase"] == "Synced"


def test_create_variable_conflict_without_adopt_is_permanent(monkeypatch):
    api = MagicMock()
    # Free on the pre-check, taken by the time of the POST (Airflow 3 409s).
    api.get_variable.side_effect = NotFoundException(status=404, reason="Not Found")
    api.post_variables.side_effect = ApiException(status=409, reason="exists")
    monkeypatch.setattr(variables, "variables_api", api)

    with pytest.raises(kopf.PermanentError):
        variables.create_variable(
            spec=VAR_SPEC, name="v1", namespace="ns", logger=logger, patch=_patch()
        )
    api.patch_variable.assert_not_called()


def test_create_pool_conflict_without_adopt_is_permanent(monkeypatch):
    api = MagicMock()
    api.post_pool.side_effect = ApiException(status=409, reason="exists")
    monkeypatch.setattr(pools, "pools_api", api)

    with pytest.raises(kopf.PermanentError):
        pools.create_pool(
            spec=POOL_SPEC, name="p1", namespace="ns", logger=logger, patch=_patch()
        )
    api.patch_pool.assert_not_called()


def test_create_connection_adopts_on_conflict(monkeypatch):
    api = MagicMock()
    api.post_connection.side_effect = ApiException(status=409, reason="exists")
    monkeypatch.setattr(connections, "connections_api", api)
    patch = _patch()

    connections.create_connection(
        spec={**CONN_SPEC, "adopt": True},
        name="c1",
        namespace="ns",
        logger=logger,
        patch=patch,
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
        spec=CONN_SPEC,
        name="c1",
        namespace="ns",
        logger=logger,
        patch=_patch(),
        status=_owned("connection"),
    )

    api.patch_connection.assert_called_once()
    api.post_connection.assert_not_called()


def test_reconcile_connection_recreates_when_missing(monkeypatch):
    api = MagicMock()
    api.patch_connection.side_effect = NotFoundException(status=404, reason="Not Found")
    monkeypatch.setattr(connections, "connections_api", api)

    connections.reconcile_connection(
        spec=CONN_SPEC,
        name="c1",
        namespace="ns",
        logger=logger,
        patch=_patch(),
        status=_owned("connection"),
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
            spec=CONN_SPEC,
            name="c1",
            namespace="ns",
            logger=logger,
            patch=_patch(),
            status=_owned("connection"),
        )


def test_delete_connection_ignores_not_found(monkeypatch):
    api = MagicMock()
    api.delete_connection.side_effect = NotFoundException(
        status=404, reason="Not Found"
    )
    monkeypatch.setattr(connections, "connections_api", api)

    # Already-absent delete is a no-op success, not a raise (so the finalizer
    # is released cleanly).
    connections.delete_connection(
        spec=CONN_SPEC,
        name="c1",
        namespace="ns",
        logger=logger,
        status=_owned("connection"),
    )
    api.delete_connection.assert_called_once()


def test_delete_connection_other_error_raises_temporary(monkeypatch):
    api = MagicMock()
    api.delete_connection.side_effect = ApiException(status=500, reason="boom")
    monkeypatch.setattr(connections, "connections_api", api)

    with pytest.raises(kopf.TemporaryError):
        connections.delete_connection(
            spec=CONN_SPEC,
            name="c1",
            namespace="ns",
            logger=logger,
            status=_owned("connection"),
        )


# --- variables & pools: same upsert contract -----------------------------


def test_reconcile_variable_recreates_when_missing(monkeypatch):
    api = MagicMock()
    api.patch_variable.side_effect = NotFoundException(status=404, reason="Not Found")
    monkeypatch.setattr(variables, "variables_api", api)

    variables.reconcile_variable(
        spec=VAR_SPEC,
        name="v1",
        namespace="ns",
        logger=logger,
        patch=_patch(),
        status=_owned("variable"),
    )

    api.patch_variable.assert_called_once()
    api.post_variables.assert_called_once()


def test_reconcile_pool_recreates_when_missing(monkeypatch):
    api = MagicMock()
    api.patch_pool.side_effect = NotFoundException(status=404, reason="Not Found")
    monkeypatch.setattr(pools, "pools_api", api)

    pools.reconcile_pool(
        spec=POOL_SPEC,
        name="p1",
        namespace="ns",
        logger=logger,
        patch=_patch(),
        status=_owned("pool"),
    )

    api.patch_pool.assert_called_once()
    api.post_pool.assert_called_once()


# --- dags: adopt-only contract (no create endpoint in the Airflow API) ----


def test_create_dag_patches_and_marks_synced(monkeypatch):
    api = MagicMock()
    monkeypatch.setattr(dags, "dags_api", api)
    patch = _patch()

    dags.create_dag(
        spec=DAG_SPEC, name="d1", namespace="ns", logger=logger, patch=patch
    )

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
        dags.reconcile_dag(
            spec=DAG_SPEC, name="d1", namespace="ns", logger=logger, patch=patch
        )

    assert patch.status["phase"] == "Error"
    assert "waiting for it to be parsed" in patch.status["message"]


def test_reconcile_dag_bad_request_is_permanent(monkeypatch):
    api = MagicMock()
    api.patch_dag.side_effect = ApiException(status=400, reason="bad spec")
    monkeypatch.setattr(dags, "dags_api", api)

    with pytest.raises(kopf.PermanentError):
        dags.reconcile_dag(
            spec=DAG_SPEC, name="d1", namespace="ns", logger=logger, patch=_patch()
        )


def test_reconcile_dag_other_error_raises_temporary(monkeypatch):
    api = MagicMock()
    api.patch_dag.side_effect = ApiException(status=503, reason="unavailable")
    monkeypatch.setattr(dags, "dags_api", api)

    with pytest.raises(kopf.TemporaryError):
        dags.reconcile_dag(
            spec=DAG_SPEC, name="d1", namespace="ns", logger=logger, patch=_patch()
        )


def test_delete_dag_leaves_airflow_untouched_by_default(monkeypatch):
    api = MagicMock()
    monkeypatch.setattr(dags, "dags_api", api)

    dags.delete_dag(spec=DAG_SPEC, name="d1", namespace="ns", logger=logger)
    api.delete_dag.assert_not_called()


def test_delete_dag_opt_in_calls_airflow_and_ignores_not_found(monkeypatch):
    api = MagicMock()
    api.delete_dag.side_effect = NotFoundException(status=404, reason="Not Found")
    monkeypatch.setattr(dags, "dags_api", api)

    dags.delete_dag(
        spec={**DAG_SPEC, "deleteDag": True}, name="d1", namespace="ns", logger=logger
    )
    assert api.delete_dag.call_args.kwargs["dag_id"] == "my_dag"


def test_delete_dag_other_error_raises_temporary(monkeypatch):
    api = MagicMock()
    api.delete_dag.side_effect = ApiException(status=500, reason="boom")
    monkeypatch.setattr(dags, "dags_api", api)

    with pytest.raises(kopf.TemporaryError):
        dags.delete_dag(
            spec={**DAG_SPEC, "deleteDag": True},
            name="d1",
            namespace="ns",
            logger=logger,
        )


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

    # Airflow reports the DAG as loaded from whichever file was delivered.
    def get_dag(dag_id, **kwargs):
        name = next(
            (f for f in (f"{dag_id}.py", f"{dag_id}.zip") if f in backend.store),
            f"{dag_id}.py",
        )
        return MagicMock(data=json.dumps({"fileloc": f"/dags/{name}"}).encode())

    api.get_dag.side_effect = get_dag
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


def test_sourced_dag_id_with_path_traversal_is_permanent(monkeypatch):
    api, backend = _sourced(monkeypatch)
    spec = {**SOURCED_DAG_SPEC, "dagId": "../../etc/cron.d/evil"}

    with pytest.raises(kopf.PermanentError):
        dags.create_dag(
            spec=spec, name="d1", namespace="ns", logger=logger, patch=_patch()
        )
    assert backend.store == {}


def test_sourced_dag_id_too_long_is_permanent(monkeypatch):
    api, backend = _sourced(monkeypatch)
    spec = {**SOURCED_DAG_SPEC, "dagId": "d" * 251}

    with pytest.raises(kopf.PermanentError):
        dags.create_dag(
            spec=spec, name="d1", namespace="ns", logger=logger, patch=_patch()
        )
    assert backend.store == {}


# --- dags: url sources -----------------------------------------------------

DAG_PY = b"print('x')\n"
DAG_PY_SHA = hashlib.sha256(DAG_PY).hexdigest()
PUBLIC_IP = "93.184.216.34"


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self.headers = headers or {}
        self._body = body

    def stream(self, amt):
        for i in range(0, len(self._body), amt):
            yield self._body[i : i + amt]

    def release_conn(self):
        pass


class FakePool:
    def __init__(self, host, port, responses, **kwargs):
        self.host = host
        self.port = port
        self.kwargs = kwargs
        self.requests = []
        self._responses = responses

    def urlopen(self, method, path, **kwargs):
        self.requests.append((method, path, kwargs))
        return self._responses.pop(0)

    def close(self):
        pass


def _fake_http(monkeypatch, responses, resolve=None):
    """Route url fetches to canned responses; ``resolve`` maps host -> IPs."""
    resolve = resolve or (lambda host: [PUBLIC_IP])
    monkeypatch.setattr(
        dags.socket,
        "getaddrinfo",
        lambda host, port, **k: [(2, 1, 6, "", (ip, port)) for ip in resolve(host)],
    )
    pools = []

    def factory(host, port, **kwargs):
        pool = FakePool(host, port, responses, **kwargs)
        pools.append(pool)
        return pool

    monkeypatch.setattr(dags.urllib3, "HTTPSConnectionPool", factory)
    monkeypatch.setattr(dags.urllib3, "HTTPConnectionPool", factory)
    return pools


def _url_spec(url, sha256=DAG_PY_SHA):
    return {**SOURCED_DAG_SPEC, "source": {"url": {"url": url, "sha256": sha256}}}


def _create(spec):
    dags.create_dag(spec=spec, name="d1", namespace="ns", logger=logger, patch=_patch())


def test_url_source_requires_sha256():
    from pydantic import ValidationError

    from resources.schemas import DagSourceUrl

    with pytest.raises(ValidationError):
        DagSourceUrl.model_validate({"url": "https://example.com/dag.py"})
    with pytest.raises(ValidationError):
        DagSourceUrl.model_validate(
            {"url": "https://example.com/dag.py", "sha256": "not-a-digest"}
        )


def test_url_fetch_connects_to_vetted_address_and_verifies_host(monkeypatch):
    api, backend = _sourced(monkeypatch)
    pools = _fake_http(monkeypatch, [FakeResponse(body=DAG_PY)])

    _create(_url_spec("https://example.com/dags/dag.py?v=1"))

    (pool,) = pools
    assert pool.host == PUBLIC_IP
    assert pool.kwargs["server_hostname"] == "example.com"
    assert pool.kwargs["assert_hostname"] == "example.com"
    assert pool.kwargs["cert_reqs"] == "CERT_REQUIRED"
    method, path, kwargs = pool.requests[0]
    assert (method, path) == ("GET", "/dags/dag.py?v=1")
    assert kwargs["headers"]["Host"] == "example.com"
    assert kwargs["redirect"] is False
    assert b"print('x')" in backend.store["my_dag.py"]


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "::1",
        "fd00::1",
        "fe80::1",
        "::ffff:169.254.169.254",
        "64:ff9b::a9fe:a9fe",
        "2002:a9fe:a9fe::1",
    ],
)
def test_url_non_public_address_is_permanent(monkeypatch, address):
    api, backend = _sourced(monkeypatch)
    pools = _fake_http(monkeypatch, [], resolve=lambda host: [address])

    with pytest.raises(kopf.PermanentError):
        _create(_url_spec("http://internal.example/dag.py"))
    assert pools == []
    assert backend.store == {}


def test_url_rejected_if_any_resolved_address_is_internal(monkeypatch):
    api, backend = _sourced(monkeypatch)
    pools = _fake_http(
        monkeypatch, [], resolve=lambda host: [PUBLIC_IP, "169.254.169.254"]
    )

    with pytest.raises(kopf.PermanentError):
        _create(_url_spec("https://example.com/dag.py"))
    assert pools == []


def test_url_redirect_target_is_rechecked(monkeypatch):
    api, backend = _sourced(monkeypatch)
    redirect = FakeResponse(302, headers={"location": "http://internal.example/x"})
    pools = _fake_http(
        monkeypatch,
        [redirect],
        resolve=lambda host: [PUBLIC_IP if host == "example.com" else "10.0.0.5"],
    )

    with pytest.raises(kopf.PermanentError):
        _create(_url_spec("https://example.com/dag.py"))
    assert len(pools) == 1
    assert backend.store == {}


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/dag.py",
        "https://user:s3cr3t@example.com/dag.py",
    ],
)
def test_url_bad_scheme_or_credentials_is_permanent(monkeypatch, url):
    api, backend = _sourced(monkeypatch)
    pools = _fake_http(monkeypatch, [])

    with pytest.raises(kopf.PermanentError) as err:
        _create(_url_spec(url))
    assert "s3cr3t" not in str(err.value)
    assert pools == []


def test_url_sha256_mismatch_is_permanent(monkeypatch):
    api, backend = _sourced(monkeypatch)
    _fake_http(monkeypatch, [FakeResponse(body=DAG_PY)])

    with pytest.raises(kopf.PermanentError):
        _create(_url_spec("https://example.com/dag.py", sha256="0" * 64))
    assert backend.store == {}


def test_url_download_size_is_capped(monkeypatch):
    api, backend = _sourced(monkeypatch)
    monkeypatch.setattr(dags, "MAX_OBJECT_BYTES", 8)
    _fake_http(monkeypatch, [FakeResponse(body=b"x" * 100)])

    with pytest.raises(kopf.PermanentError):
        _create(_url_spec("https://example.com/dag.py"))


def test_url_error_message_redacts_query(monkeypatch):
    api, backend = _sourced(monkeypatch)
    _fake_http(monkeypatch, [FakeResponse(503)])
    patch = _patch()

    with pytest.raises(kopf.TemporaryError) as err:
        dags.create_dag(
            spec=_url_spec("https://example.com/dag.py?X-Amz-Signature=s3cr3t"),
            name="d1",
            namespace="ns",
            logger=logger,
            patch=patch,
        )
    assert "s3cr3t" not in str(err.value)
    assert "s3cr3t" not in patch.status["message"]


# --- dags: objectRef sources -----------------------------------------------


def _object_spec(uri):
    return {**SOURCED_DAG_SPEC, "source": {"objectRef": {"uri": uri}}}


def test_object_ref_denied_without_allowed_prefix(monkeypatch):
    api, backend = _sourced(monkeypatch)
    monkeypatch.delenv("AIRFLOW_DAGS_SOURCE_PREFIXES", raising=False)
    read = MagicMock()
    monkeypatch.setattr(dags, "read_object", read)

    with pytest.raises(kopf.PermanentError):
        _create(_object_spec("gs://other-bucket/secrets.py"))
    read.assert_not_called()


def test_object_ref_allowed_under_prefix(monkeypatch):
    api, backend = _sourced(monkeypatch)
    monkeypatch.setenv("AIRFLOW_DAGS_SOURCE_PREFIXES", "gs://staging/dags/")
    monkeypatch.setattr(dags, "read_object", lambda uri: DAG_PY)

    _create(_object_spec("gs://staging/dags/team-a/dag.py"))
    assert b"print('x')" in backend.store["my_dag.py"]


def test_object_ref_too_large_is_permanent(monkeypatch):
    api, backend = _sourced(monkeypatch)
    monkeypatch.setenv("AIRFLOW_DAGS_SOURCE_PREFIXES", "gs://staging/dags/")

    def too_large(uri):
        raise dags.ObjectTooLarge("too big")

    monkeypatch.setattr(dags, "read_object", too_large)

    with pytest.raises(kopf.PermanentError):
        _create(_object_spec("gs://staging/dags/big.zip"))


def test_oversized_existing_file_is_treated_as_foreign(monkeypatch):
    api, backend = _sourced(monkeypatch)

    def read(name):
        raise dags.ObjectTooLarge("too big")

    backend.read = read

    with pytest.raises(kopf.PermanentError):
        _create(SOURCED_DAG_SPEC)
    assert backend.writes == 0


# --- dags: deletion with a source ------------------------------------------


def test_sourced_delete_removes_owned_file_and_keeps_dag_record(monkeypatch):
    api, backend = _sourced(monkeypatch)
    _create(SOURCED_DAG_SPEC)
    assert "my_dag.py" in backend.store

    dags.delete_dag(spec=SOURCED_DAG_SPEC, name="d1", namespace="ns", logger=logger)

    assert backend.store == {}
    api.delete_dag.assert_not_called()


def test_sourced_delete_with_delete_dag_also_deletes_record(monkeypatch):
    api, backend = _sourced(monkeypatch)
    spec = {**SOURCED_DAG_SPEC, "deleteDag": True}
    _create(spec)

    dags.delete_dag(spec=spec, name="d1", namespace="ns", logger=logger)

    assert backend.store == {}
    assert api.delete_dag.call_args.kwargs["dag_id"] == "my_dag"


def test_delete_removes_owned_file_after_source_was_dropped(monkeypatch):
    api, backend = _sourced(monkeypatch)
    _create(SOURCED_DAG_SPEC)

    dags.delete_dag(spec=DAG_SPEC, name="d1", namespace="ns", logger=logger)

    assert backend.store == {}


def test_sourced_delete_leaves_foreign_file(monkeypatch):
    api, backend = _sourced(monkeypatch)
    backend.store["my_dag.py"] = b"# hand-managed\nprint('mine')\n"

    dags.delete_dag(spec=SOURCED_DAG_SPEC, name="d1", namespace="ns", logger=logger)

    assert backend.store["my_dag.py"] == b"# hand-managed\nprint('mine')\n"
    api.delete_dag.assert_not_called()


def test_delete_leaves_file_owned_by_another_namespace(monkeypatch):
    api, backend = _sourced(monkeypatch)
    dags.create_dag(
        spec=SOURCED_DAG_SPEC,
        name="d1",
        namespace="other",
        logger=logger,
        patch=_patch(),
    )

    dags.delete_dag(spec=SOURCED_DAG_SPEC, name="d1", namespace="ns", logger=logger)

    assert "my_dag.py" in backend.store


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


# --- adoption is limited to namespaces the operator admin allows ----------


def _deny_adoption(monkeypatch):
    monkeypatch.setattr(reconcile, "ADOPT_NAMESPACES", frozenset({"admin-ns"}))


def test_adopt_outside_allowed_namespaces_is_denied(monkeypatch):
    _deny_adoption(monkeypatch)
    api = MagicMock()
    monkeypatch.setattr(variables, "variables_api", api)
    patch = _patch()

    with pytest.raises(kopf.PermanentError):
        variables.create_variable(
            spec={**VAR_SPEC, "adopt": True},
            name="v1",
            namespace="ns",
            logger=logger,
            patch=patch,
        )

    api.post_variables.assert_not_called()
    api.patch_variable.assert_not_called()
    assert "AIRFLOW_ADOPT_NAMESPACES" in patch.status["message"]


def test_adopt_flag_does_not_grant_ownership_outside_allowed_namespaces(
    monkeypatch,
):
    _deny_adoption(monkeypatch)
    api = MagicMock()
    monkeypatch.setattr(connections, "connections_api", api)

    connections.reconcile_connection(
        spec={**CONN_SPEC, "adopt": True},
        name="c1",
        namespace="ns",
        logger=logger,
        patch=_patch(),
    )
    connections.delete_connection(
        spec={**CONN_SPEC, "adopt": True}, name="c1", namespace="ns", logger=logger
    )

    api.patch_connection.assert_not_called()
    api.delete_connection.assert_not_called()


def test_created_resource_stays_managed_outside_allowed_namespaces(monkeypatch):
    # Adoption limits only apply to objects the resource did not create.
    _deny_adoption(monkeypatch)
    api = MagicMock()
    monkeypatch.setattr(pools, "pools_api", api)

    pools.reconcile_pool(
        spec=POOL_SPEC,
        name="p1",
        namespace="ns",
        logger=logger,
        patch=_patch(),
        status=_owned("pool"),
    )
    api.patch_pool.assert_called_once()


def test_unsourced_dag_outside_allowed_namespaces_is_denied(monkeypatch):
    _deny_adoption(monkeypatch)
    api = MagicMock()
    monkeypatch.setattr(dags, "dags_api", api)

    with pytest.raises(kopf.PermanentError):
        dags.create_dag(
            spec=DAG_SPEC, name="d1", namespace="ns", logger=logger, patch=_patch()
        )
    api.patch_dag.assert_not_called()


def test_unsourced_dag_delete_outside_allowed_namespaces_keeps_record(monkeypatch):
    _deny_adoption(monkeypatch)
    api = MagicMock()
    monkeypatch.setattr(dags, "dags_api", api)

    dags.delete_dag(
        spec={**DAG_SPEC, "deleteDag": True}, name="d1", namespace="ns", logger=logger
    )
    api.delete_dag.assert_not_called()


def test_sourced_dag_is_allowed_outside_adopt_namespaces(monkeypatch):
    # A delivered DAG is the resource's own, so no adoption grant is needed.
    _deny_adoption(monkeypatch)
    api, backend = _sourced(monkeypatch)

    dags.create_dag(
        spec=SOURCED_DAG_SPEC, name="d1", namespace="ns", logger=logger, patch=_patch()
    )
    api.patch_dag.assert_called_once()


def test_sourced_dag_loaded_from_another_file_is_refused(monkeypatch):
    # A dummy file named after someone else's dag_id must not let the
    # resource pause that DAG.
    api, backend = _sourced(monkeypatch)
    api.get_dag.side_effect = lambda dag_id, **kw: MagicMock(
        data=json.dumps({"fileloc": "/dags/team_a/pipelines.py"}).encode()
    )
    patch = _patch()

    with pytest.raises(kopf.PermanentError):
        dags.create_dag(
            spec=SOURCED_DAG_SPEC, name="d1", namespace="ns", logger=logger, patch=patch
        )
    api.patch_dag.assert_not_called()
    assert "pipelines.py" in patch.status["message"]


def test_sourced_delete_keeps_record_loaded_from_another_file(monkeypatch):
    api, backend = _sourced(monkeypatch)
    spec = {**SOURCED_DAG_SPEC, "deleteDag": True}
    _create(spec)
    api.get_dag.side_effect = lambda dag_id, **kw: MagicMock(
        data=json.dumps({"fileloc": "/dags/team_a/pipelines.py"}).encode()
    )

    dags.delete_dag(spec=spec, name="d1", namespace="ns", logger=logger)

    # The delivered file goes; the other team's DAG record stays.
    assert backend.store == {}
    api.delete_dag.assert_not_called()


def test_packaged_dag_fileloc_inside_zip_counts_as_own_file():
    assert dags._defined_by("/dags/my_dag.zip/my_dag.py", "my_dag.zip")
    assert not dags._defined_by("/dags/other.zip/my_dag.py", "my_dag.zip")
