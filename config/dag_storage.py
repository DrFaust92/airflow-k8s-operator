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
import os
import pathlib
import urllib.parse

logger = logging.getLogger(__name__)

_SCHEMES = ("gs", "s3", "file")
_HTTP_TIMEOUT = 30

# Upper bound on any object read: a DAG source, or an existing file in the DAGs
# folder that is compared against it.
MAX_OBJECT_BYTES = 50 * 1024 * 1024


class ObjectTooLarge(ValueError):
    pass


def _too_large(name: str) -> ObjectTooLarge:
    return ObjectTooLarge(f"{name} exceeds {MAX_OBJECT_BYTES} bytes")


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
            f"/o/{self._object(name)}?alt=media",
            stream=True,
            timeout=_HTTP_TIMEOUT,
        )
        with resp:
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = bytearray()
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                data += chunk
                if len(data) > MAX_OBJECT_BYTES:
                    raise _too_large(f"gs://{self._bucket}/{name}")
            return bytes(data)

    def write(self, name: str, data: bytes):
        resp = self._session.post(
            f"https://storage.googleapis.com/upload/storage/v1/b/{self._bucket}"
            f"/o?uploadType=media&name={self._object(name)}",
            data=data,
            headers={"Content-Type": "application/octet-stream"},
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()

    def delete(self, name: str):
        resp = self._session.delete(
            f"https://storage.googleapis.com/storage/v1/b/{self._bucket}"
            f"/o/{self._object(name)}",
            timeout=_HTTP_TIMEOUT,
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
        if resp.get("ContentLength", 0) > MAX_OBJECT_BYTES:
            resp["Body"].close()
            raise _too_large(f"s3://{self._bucket}/{self._key(name)}")
        data = resp["Body"].read(MAX_OBJECT_BYTES + 1)
        if len(data) > MAX_OBJECT_BYTES:
            raise _too_large(f"s3://{self._bucket}/{self._key(name)}")
        return data

    def write(self, name: str, data: bytes):
        self._client.put_object(Bucket=self._bucket, Key=self._key(name), Body=data)

    def delete(self, name: str):
        # S3 DeleteObject is idempotent; a missing key is not an error.
        self._client.delete_object(Bucket=self._bucket, Key=self._key(name))


class _FileBackend:
    def __init__(self, root: str):
        self._root = pathlib.Path(root)

    def _path(self, name: str) -> pathlib.Path:
        # Defense in depth: the caller validates names, but never let one
        # escape the DAGs root regardless.
        root = self._root.resolve()
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"file name '{name}' escapes the DAGs root {root}")
        return path

    def read(self, name: str) -> bytes | None:
        path = self._path(name)
        try:
            if path.stat().st_size > MAX_OBJECT_BYTES:
                raise _too_large(str(path))
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def write(self, name: str, data: bytes):
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._path(name)
        # Write beside the target and rename over it, so Airflow's parser never
        # reads a half-written file. The leading dot keeps it out of parsing.
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

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
    uri = os.getenv("AIRFLOW_DAGS_URI")
    if not uri:
        return None
    backend = _build_backend(uri)
    logger.info(f"DAG upload enabled, destination: {uri}")
    return backend


def _normalize_prefix(prefix: str) -> str | None:
    try:
        scheme, bucket, path = _parse_uri(prefix.strip())
    except ValueError:
        logger.warning(f"Ignoring invalid DAG source prefix '{prefix}'")
        return None
    if scheme == "file":
        logger.warning(f"Ignoring file:// DAG source prefix '{prefix}'")
        return None
    return f"{scheme}://{bucket}/{path}/" if path else f"{scheme}://{bucket}/"


def source_allowed(uri: str) -> bool:
    """Whether spec.source.objectRef may read ``uri``.

    objectRef reads with the operator's own cloud credentials, so a Dag
    resource could otherwise copy anything those credentials can read into the
    DAGs folder. Only objects under a prefix listed (comma-separated) in
    AIRFLOW_DAGS_SOURCE_PREFIXES are allowed; none are by default.
    """
    try:
        scheme, bucket, path = _parse_uri(uri)
    except ValueError:
        return False
    segments = path.split("/")
    if scheme == "file" or not path or any(s in ("", ".", "..") for s in segments):
        return False
    target = f"{scheme}://{bucket}/{path}"
    prefixes = os.getenv("AIRFLOW_DAGS_SOURCE_PREFIXES", "").split(",")
    for prefix in filter(None, map(_normalize_prefix, filter(str.strip, prefixes))):
        if target.startswith(prefix):
            return True
    return False


def read_object(uri: str) -> bytes | None:
    """Read a single gs:// or s3:// object (for spec.source.objectRef)."""
    scheme, bucket, path = _parse_uri(uri)
    if scheme == "file":
        raise ValueError(f"objectRef does not support file:// URIs: '{uri}'")
    prefix, _, name = path.rpartition("/")
    backend = (
        _GcsBackend(bucket, prefix) if scheme == "gs" else _S3Backend(bucket, prefix)
    )
    return backend.read(name)
