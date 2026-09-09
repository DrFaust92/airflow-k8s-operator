"""Storage backends for delivering DAG files to the Airflow DAGs folder.

The Airflow REST API cannot create DAGs -- they come from files in the DAGs
folder -- so the operator writes them there directly. The destination is
configured with the ``AIRFLOW_DAGS_URI`` environment variable:

* ``gs://bucket/prefix`` -- GCS (e.g. a Composer environment's dags/ prefix)
* ``s3://bucket/prefix`` -- S3 (e.g. an MWAA environment's dags/ prefix)
* ``file:///path`` -- a local path (e.g. a PVC shared with Airflow)

This module is import-safe when the variable is unset (the upload feature is
simply off), and cloud SDKs are imported lazily per scheme so the default
import path stays clean.
"""

import functools
import logging
import pathlib
import urllib.parse

logger = logging.getLogger(__name__)

_SCHEMES = ("gs", "s3", "file")


class _GcsBackend:
    def __init__(self, bucket: str, prefix: str):
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/devstorage.read_write"]
        )
        self._session = AuthorizedSession(credentials)
        self._bucket = bucket
        self._prefix = prefix

    def _object(self, name: str) -> str:
        key = f"{self._prefix}/{name}" if self._prefix else name
        return urllib.parse.quote(key, safe="")

    def read(self, name: str) -> bytes | None:
        resp = self._session.get(
            f"https://storage.googleapis.com/storage/v1/b/{self._bucket}"
            f"/o/{self._object(name)}?alt=media"
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.content

    def write(self, name: str, data: bytes):
        resp = self._session.post(
            f"https://storage.googleapis.com/upload/storage/v1/b/{self._bucket}"
            f"/o?uploadType=media&name={self._object(name)}",
            data=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        resp.raise_for_status()

    def delete(self, name: str):
        resp = self._session.delete(
            f"https://storage.googleapis.com/storage/v1/b/{self._bucket}"
            f"/o/{self._object(name)}"
        )
        if resp.status_code != 404:
            resp.raise_for_status()


class _S3Backend:
    def __init__(self, bucket: str, prefix: str):
        import boto3

        self._client = boto3.client("s3")
        self._bucket = bucket
        self._prefix = prefix

    def _key(self, name: str) -> str:
        return f"{self._prefix}/{name}" if self._prefix else name

    def read(self, name: str) -> bytes | None:
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=self._key(name))
        except self._client.exceptions.NoSuchKey:
            return None
        return resp["Body"].read()

    def write(self, name: str, data: bytes):
        self._client.put_object(Bucket=self._bucket, Key=self._key(name), Body=data)

    def delete(self, name: str):
        # S3 DeleteObject is idempotent; a missing key is not an error.
        self._client.delete_object(Bucket=self._bucket, Key=self._key(name))


class _FileBackend:
    def __init__(self, root: str):
        self._root = pathlib.Path(root)

    def _path(self, name: str) -> pathlib.Path:
        return self._root / name

    def read(self, name: str) -> bytes | None:
        try:
            return self._path(name).read_bytes()
        except FileNotFoundError:
            return None

    def write(self, name: str, data: bytes):
        self._root.mkdir(parents=True, exist_ok=True)
        self._path(name).write_bytes(data)

    def delete(self, name: str):
        self._path(name).unlink(missing_ok=True)


def _parse_uri(uri: str) -> tuple[str, str, str]:
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme not in _SCHEMES:
        raise ValueError(
            f"Unsupported DAGs URI scheme '{parsed.scheme}' in '{uri}'; "
            f"expected one of: gs://, s3://, file://"
        )
    if parsed.scheme == "file":
        if not parsed.path:
            raise ValueError(f"file:// DAGs URI must include a path: '{uri}'")
        return "file", "", parsed.path
    if not parsed.netloc:
        raise ValueError(f"{parsed.scheme}:// DAGs URI must include a bucket: '{uri}'")
    return parsed.scheme, parsed.netloc, parsed.path.strip("/")


def _build_backend(uri: str):
    scheme, bucket, path = _parse_uri(uri)
    if scheme == "gs":
        return _GcsBackend(bucket, path)
    if scheme == "s3":
        return _S3Backend(bucket, path)
    return _FileBackend(path)


@functools.cache
def get_backend():
    """The configured DAGs-folder backend, or None when upload is disabled."""
    import os

    uri = os.getenv("AIRFLOW_DAGS_URI")
    if not uri:
        return None
    backend = _build_backend(uri)
    logger.info(f"DAG upload enabled, destination: {uri}")
    return backend


def read_object(uri: str) -> bytes | None:
    """Read a single gs:// or s3:// object (for spec.source.objectRef)."""
    scheme, bucket, path = _parse_uri(uri)
    if scheme == "file":
        raise ValueError(f"objectRef does not support file:// URIs: '{uri}'")
    prefix, _, name = path.rpartition("/")
    backend = _GcsBackend(bucket, prefix) if scheme == "gs" else _S3Backend(
        bucket, prefix
    )
    return backend.read(name)
