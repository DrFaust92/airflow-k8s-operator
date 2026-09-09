from unittest.mock import MagicMock, patch

import pytest

import config.dag_storage as dag_storage

# --- URI parsing -----------------------------------------------------------


def test_parse_uri_rejects_unknown_scheme():
    with pytest.raises(ValueError):
        dag_storage._parse_uri("ftp://bucket/dags")


def test_parse_uri_requires_bucket():
    with pytest.raises(ValueError):
        dag_storage._parse_uri("gs://")


def test_parse_uri_requires_file_path():
    with pytest.raises(ValueError):
        dag_storage._parse_uri("file://")


def test_parse_uri_strips_prefix_slashes():
    assert dag_storage._parse_uri("s3://bucket/dags/") == ("s3", "bucket", "dags")


# --- get_backend -----------------------------------------------------------


def test_get_backend_none_when_unset(monkeypatch):
    monkeypatch.delenv("AIRFLOW_DAGS_URI", raising=False)
    dag_storage.get_backend.cache_clear()
    assert dag_storage.get_backend() is None


def test_get_backend_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AIRFLOW_DAGS_URI", f"file://{tmp_path}")
    dag_storage.get_backend.cache_clear()
    backend = dag_storage.get_backend()
    assert isinstance(backend, dag_storage._FileBackend)
    dag_storage.get_backend.cache_clear()


# --- file backend (real IO) ------------------------------------------------


def test_file_backend_roundtrip(tmp_path):
    backend = dag_storage._FileBackend(str(tmp_path / "dags"))
    assert backend.read("d.py") is None
    backend.write("d.py", b"data")
    assert backend.read("d.py") == b"data"
    backend.delete("d.py")
    assert backend.read("d.py") is None
    backend.delete("d.py")  # missing delete is a no-op


# --- s3 backend (mocked boto3) ---------------------------------------------


def _s3_backend(mock_client):
    with patch("boto3.client", return_value=mock_client):
        return dag_storage._S3Backend("bucket", "dags")


def test_s3_backend_read_write_delete():
    mock_client = MagicMock()
    mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: b"data")}
    backend = _s3_backend(mock_client)

    assert backend.read("d.py") == b"data"
    mock_client.get_object.assert_called_once_with(Bucket="bucket", Key="dags/d.py")

    backend.write("d.py", b"new")
    mock_client.put_object.assert_called_once_with(
        Bucket="bucket", Key="dags/d.py", Body=b"new"
    )

    backend.delete("d.py")
    mock_client.delete_object.assert_called_once_with(Bucket="bucket", Key="dags/d.py")


def test_s3_backend_read_missing_returns_none():
    class NoSuchKey(Exception):
        pass

    mock_client = MagicMock()
    mock_client.exceptions.NoSuchKey = NoSuchKey
    mock_client.get_object.side_effect = NoSuchKey()
    backend = _s3_backend(mock_client)

    assert backend.read("d.py") is None


# --- gcs backend (mocked AuthorizedSession) --------------------------------


def _gcs_backend(mock_session):
    with (
        patch("google.auth.default", return_value=(MagicMock(), "proj")),
        patch(
            "google.auth.transport.requests.AuthorizedSession",
            return_value=mock_session,
        ),
    ):
        return dag_storage._GcsBackend("bucket", "dags")


def test_gcs_backend_read_write_delete():
    mock_session = MagicMock()
    mock_session.get.return_value = MagicMock(status_code=200, content=b"data")
    backend = _gcs_backend(mock_session)

    assert backend.read("d.py") == b"data"
    assert "b/bucket/o/dags%2Fd.py?alt=media" in mock_session.get.call_args.args[0]

    backend.write("d.py", b"new")
    write_url = mock_session.post.call_args.args[0]
    assert "uploadType=media&name=dags%2Fd.py" in write_url
    assert mock_session.post.call_args.kwargs["data"] == b"new"

    mock_session.delete.return_value = MagicMock(status_code=204)
    backend.delete("d.py")
    mock_session.delete.assert_called_once()


def test_gcs_backend_read_missing_returns_none():
    mock_session = MagicMock()
    mock_session.get.return_value = MagicMock(status_code=404)
    backend = _gcs_backend(mock_session)
    assert backend.read("d.py") is None


# --- read_object (objectRef) ------------------------------------------------


def test_read_object_rejects_file_uri():
    with pytest.raises(ValueError):
        dag_storage.read_object("file:///tmp/d.py")


def test_read_object_s3():
    mock_client = MagicMock()
    mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: b"data")}
    with patch("boto3.client", return_value=mock_client):
        assert dag_storage.read_object("s3://bucket/staging/d.py") == b"data"
    mock_client.get_object.assert_called_once_with(
        Bucket="bucket", Key="staging/d.py"
    )
