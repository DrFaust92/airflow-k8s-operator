import hashlib
import io
import zipfile

import kopf
import requests
from airflow_client.client.api.dag_api import DAGApi
from airflow_client.client.exceptions import NotFoundException
from airflow_client.client.model.dag import DAG

from config.base import (
    OPERATOR_RECONCILE_INTERVAL,
    OPERATOR_RECONCILE_INTERVAL_DELAY,
)
from config.client import api_client
from config.dag_storage import get_backend, read_object
from config.k8s_configmap import get_config_map_value
from config.metrics import MANAGED_RESOURCES
from config.reconcile import DELETE_MAX_RETRIES, track
from resources.schemas import DagSpec

dags_api = DAGApi(api_client=api_client)

_ZIP_MAGIC = b"PK\x03\x04"
_URL_TIMEOUT = 30


def _dag_id(name, spec) -> str:
    return DagSpec.model_validate(spec).dag_id or name


def _marker(namespace, name) -> str:
    return f"managed-by: airflow-k8s-operator dag={namespace}/{name}"


def _resolve_source(source, namespace, logger) -> bytes:
    if source.code is not None:
        return source.code.encode("utf-8")
    if source.configMapRef is not None:
        return get_config_map_value(
            source.configMapRef.name, source.configMapRef.key, namespace, logger
        )
    if source.url is not None:
        resp = requests.get(source.url.url, timeout=_URL_TIMEOUT)
        resp.raise_for_status()
        data = resp.content
        if source.url.sha256:
            digest = hashlib.sha256(data).hexdigest()
            if digest != source.url.sha256.lower():
                raise kopf.PermanentError(
                    f"Downloaded content sha256 {digest} does not match "
                    f"spec.source.url.sha256 {source.url.sha256}"
                )
        return data
    if source.objectRef is not None:
        data = read_object(source.objectRef.uri)
        if data is None:
            raise kopf.TemporaryError(
                f"Source object {source.objectRef.uri} not found", delay=30
            )
        return data
    raise kopf.PermanentError(
        "spec.source must set one of code, configMapRef, url, or objectRef"
    )


def _prepare(data: bytes, dag_id: str, marker: str) -> tuple[str, bytes]:
    """Validate the source and embed the ownership marker.

    A zip archive gets the marker as its archive comment (does not affect
    parsing); a .py file gets it as a first-line comment. Both embeddings are
    deterministic so unchanged sources compare equal to the stored object.
    """
    if data[:4] == _ZIP_MAGIC:
        buf = io.BytesIO(data)
        try:
            with zipfile.ZipFile(buf, "a") as zf:
                zf.comment = marker.encode("utf-8")
        except zipfile.BadZipFile as e:
            raise kopf.PermanentError(f"spec.source is not a valid zip archive: {e}")
        return f"{dag_id}.zip", buf.getvalue()
    try:
        text = data.decode("utf-8")
        compile(text, f"{dag_id}.py", "exec")
    except (UnicodeDecodeError, SyntaxError, ValueError) as e:
        raise kopf.PermanentError(f"spec.source is not valid Python: {e}")
    return f"{dag_id}.py", f"# {marker}\n{text}".encode("utf-8")


def _owned(existing: bytes, marker: str) -> bool:
    if existing[:4] == _ZIP_MAGIC:
        try:
            comment = zipfile.ZipFile(io.BytesIO(existing)).comment
        except zipfile.BadZipFile:
            return False
        return comment.decode("utf-8", errors="replace") == marker
    first_line = existing.split(b"\n", 1)[0].decode("utf-8", errors="replace")
    return first_line == f"# {marker}"


def _deliver(namespace, name, dag_id, parsed, logger):
    backend = get_backend()
    if backend is None:
        raise kopf.PermanentError(
            "spec.source is set but DAG upload is not configured on the "
            "operator (AIRFLOW_DAGS_URI is unset)"
        )
    marker = _marker(namespace, name)
    data = _resolve_source(parsed.source, namespace, logger)
    filename, desired = _prepare(data, dag_id, marker)
    existing = backend.read(filename)
    if existing == desired:
        return
    if existing is not None and not _owned(existing, marker):
        raise kopf.PermanentError(
            f"{filename} already exists in the DAGs folder and is not managed "
            f"by this resource; refusing to overwrite"
        )
    logger.info(f"Uploading DAG source to {filename}")
    backend.write(filename, desired)
    # The source format may have flipped (.py <-> .zip); drop the stale
    # counterpart if this resource owns it.
    _delete_owned_files(
        backend, dag_id, marker, logger, only=_other_filename(dag_id, filename)
    )


def _other_filename(dag_id: str, filename: str) -> str:
    return f"{dag_id}.py" if filename.endswith(".zip") else f"{dag_id}.zip"


def _delete_owned_files(backend, dag_id, marker, logger, only=None):
    for filename in [only] if only else [f"{dag_id}.py", f"{dag_id}.zip"]:
        existing = backend.read(filename)
        if existing is None:
            continue
        if not _owned(existing, marker):
            logger.warning(
                f"{filename} in the DAGs folder is not managed by this "
                f"resource; leaving it in place"
            )
            continue
        logger.info(f"Deleting {filename} from the DAGs folder")
        backend.delete(filename)


def _patch_dag(name, spec, namespace, logger):
    # DAGs cannot be created via the API (they come from DAG files). With
    # spec.source the operator delivers the file to the DAGs folder first;
    # either way the DAG record is adopted by patching its paused state. A
    # missing DAG means the file has not been parsed yet (or was removed) --
    # retry until it shows up.
    parsed = DagSpec.model_validate(spec)
    dag_id = parsed.dag_id or name
    if parsed.source is not None:
        _deliver(namespace, name, dag_id, parsed, logger)
    try:
        dags_api.patch_dag(
            dag_id=dag_id,
            dag=DAG(is_paused=parsed.is_paused),
            update_mask=["is_paused"],
            _preload_content=False,
        )
    except NotFoundException as e:
        raise kopf.TemporaryError(
            f"DAG {dag_id} not found in Airflow; waiting for it to be parsed",
            delay=30,
        ) from e


@kopf.on.create("airflow.drfaust92", "v1beta1", "dags")
def create_dag(spec, name, namespace, logger, patch, **kwargs):
    logger.info(f"Adopting Airflow DAG: {name}")
    with track("dag", "create", logger, patch=patch):
        _patch_dag(name, spec, namespace, logger)
        MANAGED_RESOURCES.labels(resource_type="dag").inc()
    return {"message": f"DAG {name} adopted successfully."}


@kopf.on.resume("airflow.drfaust92", "v1beta1", "dags")
@kopf.on.update("airflow.drfaust92", "v1beta1", "dags")
@kopf.on.timer(
    "airflow.drfaust92",
    "v1beta1",
    "dags",
    interval=OPERATOR_RECONCILE_INTERVAL,
    initial_delay=OPERATOR_RECONCILE_INTERVAL_DELAY,
)
def reconcile_dag(spec, name, namespace, logger, patch, **kwargs):
    logger.info(f"Reconciling Airflow DAG: {name}")
    with track("dag", "update", logger, patch=patch):
        _patch_dag(name, spec, namespace, logger)
    return {"message": f"DAG {name} reconciled successfully."}


@kopf.on.delete("airflow.drfaust92", "v1beta1", "dags", retries=DELETE_MAX_RETRIES)
def delete_dag(spec, name, namespace, logger, retry=0, **kwargs):
    # retries caps how long a failing delete blocks: after DELETE_MAX_RETRIES
    # attempts kopf gives up and releases the finalizer instead of wedging the
    # resource forever. The final attempt emits a Warning event + metric.
    logger.info(f"Deleting Airflow DAG: {name}")
    with track(
        "dag", "delete", logger, delay=10, retry=retry, max_retries=DELETE_MAX_RETRIES
    ):
        parsed = DagSpec.model_validate(spec)
        dag_id = parsed.dag_id or name
        if parsed.source is not None:
            backend = get_backend()
            if backend is not None:
                _delete_owned_files(backend, dag_id, _marker(namespace, name), logger)
        try:
            dags_api.delete_dag(dag_id=dag_id, _preload_content=False)
        except NotFoundException:
            logger.info(f"DAG {name} already absent in Airflow")
        MANAGED_RESOURCES.labels(resource_type="dag").dec()
    return {"message": f"DAG {name} deleted successfully."}
