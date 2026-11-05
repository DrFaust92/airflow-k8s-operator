import hashlib
import io
import ipaddress
import re
import socket
import time
import urllib.parse
import zipfile

import certifi
import kopf
import urllib3
from airflow_client.client.api.dag_api import DAGApi
from airflow_client.client.exceptions import NotFoundException
from airflow_client.client.model.dag import DAG

from config.base import (
    OPERATOR_RECONCILE_INTERVAL,
    OPERATOR_RECONCILE_INTERVAL_DELAY,
)
from config.client import api_client
from config.dag_storage import (
    MAX_OBJECT_BYTES,
    ObjectTooLarge,
    get_backend,
    read_object,
    source_allowed,
)
from config.k8s_configmap import get_config_map_value
from config.metrics import MANAGED_RESOURCES
from config.reconcile import DELETE_MAX_RETRIES, track
from resources.schemas import DagSpec

dags_api = DAGApi(api_client=api_client)

_ZIP_MAGIC = b"PK\x03\x04"
_URL_CONNECT_TIMEOUT = 10
_URL_READ_TIMEOUT = 30
# Bounds the whole download, so a server dripping bytes can't hold a handler.
_URL_DEADLINE = 120
_URL_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# The dag_id becomes the uploaded object/file name; forbid path separators and
# anything else that could traverse out of the DAGs destination. 250 is
# Airflow's dag_id limit and keeps "<dag_id>.zip" under 255 bytes.
_SAFE_DAG_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,249}")

# NAT64 (RFC 6052) embeds an IPv4 address in the low 32 bits.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _marker(namespace, name) -> str:
    return f"managed-by: airflow-k8s-operator dag={namespace}/{name}"


def _redact(url: str) -> str:
    """The URL without userinfo or query, for messages that reach the CR
    status and Events (signed URLs carry their credential in the query)."""
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.scheme}://{parsed.hostname or ''}{parsed.path}"


def _ip_allowed(ip) -> bool:
    """True only for publicly routable unicast addresses.

    Unwraps IPv6 forms that embed an IPv4 address (mapped, NAT64, 6to4) so
    e.g. ::ffff:169.254.169.254 is judged as the metadata address it is.
    """
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
    return ip.is_global and not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve(host: str, port: int) -> str:
    """Resolve ``host`` once and return an address safe to connect to.

    Every resolved address must be public: the operator runs inside the
    cluster with cloud credentials, so a Dag resource must not reach the
    metadata service, cluster services or anything else internal (SSRF). The
    caller connects to the returned address itself, so a second lookup can't
    swap in a different answer (DNS rebinding).
    """
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise kopf.TemporaryError(f"Cannot resolve {host}: {e}", delay=60) from e
    addresses = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            # e.g. a scoped IPv6 literal ("fe80::1%eth0"); never public.
            ip = None
        if ip is None or not _ip_allowed(ip):
            raise kopf.PermanentError(
                f"spec.source.url host {host} resolves to a non-public address "
                f"({info[4][0]}); refusing to fetch"
            )
        addresses.append(str(ip))
    if not addresses:
        raise kopf.TemporaryError(f"Cannot resolve {host}", delay=60)
    return addresses[0]


def _open(url: str):
    """Issue one GET for ``url`` against a vetted address. Returns the
    unread response and its pool, which the caller must release."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise kopf.PermanentError(f"spec.source.url must be http(s): {_redact(url)}")
    if parsed.username is not None or parsed.password is not None:
        # The spec is not a secret; credentials don't belong in it.
        raise kopf.PermanentError("spec.source.url must not contain credentials")
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    address = _resolve(host, port)
    timeout = urllib3.Timeout(connect=_URL_CONNECT_TIMEOUT, read=_URL_READ_TIMEOUT)
    if parsed.scheme == "https":
        # Connect to the vetted address but verify the certificate and send
        # SNI for the requested host.
        pool = urllib3.HTTPSConnectionPool(
            address,
            port,
            timeout=timeout,
            retries=False,
            cert_reqs="CERT_REQUIRED",
            ca_certs=certifi.where(),
            server_hostname=host,
            assert_hostname=host,
        )
    else:
        pool = urllib3.HTTPConnectionPool(address, port, timeout=timeout, retries=False)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    # urllib3 is used directly (not requests) so HTTP(S)_PROXY from the
    # environment can't route the fetch around the address check.
    resp = pool.urlopen(
        "GET",
        path,
        headers={"Host": parsed.netloc, "User-Agent": "airflow-k8s-operator"},
        redirect=False,
        retries=False,
        preload_content=False,
        assert_same_host=False,
    )
    return resp, pool


def _fetch_url(url: str) -> bytes:
    # Redirects are followed manually so every hop passes the address check.
    deadline = time.monotonic() + _URL_DEADLINE
    for _ in range(_URL_MAX_REDIRECTS + 1):
        resp, pool = _open(url)
        try:
            if resp.status in _REDIRECT_STATUSES:
                location = resp.headers.get("location")
                if not location:
                    raise kopf.PermanentError(
                        f"spec.source.url redirect without a Location: {_redact(url)}"
                    )
                url = urllib.parse.urljoin(url, location)
                continue
            if resp.status >= 400:
                raise kopf.TemporaryError(
                    f"Fetching {_redact(url)} failed: HTTP {resp.status}", delay=60
                )
            data = bytearray()
            for chunk in resp.stream(64 * 1024):
                data += chunk
                if len(data) > MAX_OBJECT_BYTES:
                    raise kopf.PermanentError(
                        f"spec.source.url content exceeds {MAX_OBJECT_BYTES} bytes"
                    )
                if time.monotonic() > deadline:
                    raise kopf.TemporaryError(
                        f"Fetching {_redact(url)} took longer than {_URL_DEADLINE}s",
                        delay=60,
                    )
            return bytes(data)
        finally:
            resp.release_conn()
            pool.close()
    raise kopf.PermanentError(
        f"spec.source.url: too many redirects fetching {_redact(url)}"
    )


def _resolve_source(source, namespace, logger) -> bytes:
    if source.code is not None:
        return source.code.encode("utf-8")
    if source.configMapRef is not None:
        return get_config_map_value(
            source.configMapRef.name, source.configMapRef.key, namespace, logger
        )
    if source.url is not None:
        data = _fetch_url(source.url.url)
        digest = hashlib.sha256(data).hexdigest()
        if digest != source.url.sha256.lower():
            raise kopf.PermanentError(
                f"Downloaded content sha256 {digest} does not match "
                f"spec.source.url.sha256 {source.url.sha256}"
            )
        return data
    if source.objectRef is not None:
        uri = source.objectRef.uri
        # objectRef reads with the operator's cloud credentials, so only
        # prefixes the operator admin allowed are reachable (confused deputy).
        if not source_allowed(uri):
            raise kopf.PermanentError(
                f"spec.source.objectRef {uri} is not under an allowed source "
                f"prefix (operator AIRFLOW_DAGS_SOURCE_PREFIXES)"
            )
        try:
            data = read_object(uri)
        except ObjectTooLarge as e:
            raise kopf.PermanentError(str(e)) from e
        if data is None:
            raise kopf.TemporaryError(f"Source object {uri} not found", delay=30)
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
    if len(data) > MAX_OBJECT_BYTES:
        raise kopf.PermanentError(f"spec.source exceeds {MAX_OBJECT_BYTES} bytes")
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
        # Syntax check only; compile() does not run the code. Deeply nested
        # input raises RecursionError/MemoryError rather than SyntaxError.
        compile(text, f"{dag_id}.py", "exec")
    except (
        UnicodeDecodeError,
        SyntaxError,
        ValueError,
        RecursionError,
        MemoryError,
    ) as e:
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


def _read_existing(backend, filename):
    # A file too large to be one we wrote is not ours; don't load it.
    try:
        return backend.read(filename)
    except ObjectTooLarge:
        return b""


def _validate_dag_id(dag_id: str):
    if not _SAFE_DAG_ID.fullmatch(dag_id):
        raise kopf.PermanentError(
            f"dag_id '{dag_id}' is not a safe file name for DAG upload; "
            f"allowed: letters, digits, underscore, dash, dot (no leading dot), "
            f"at most 250 characters"
        )


def _deliver(namespace, name, dag_id, parsed, logger):
    backend = get_backend()
    if backend is None:
        raise kopf.PermanentError(
            "spec.source is set but DAG upload is not configured on the "
            "operator (AIRFLOW_DAGS_URI is unset)"
        )
    _validate_dag_id(dag_id)
    marker = _marker(namespace, name)
    data = _resolve_source(parsed.source, namespace, logger)
    filename, desired = _prepare(data, dag_id, marker)
    existing = _read_existing(backend, filename)
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


def _delete_owned_files(backend, dag_id, marker, logger, only=None, quiet=False):
    for filename in [only] if only else [f"{dag_id}.py", f"{dag_id}.zip"]:
        existing = _read_existing(backend, filename)
        if existing is None:
            continue
        if not _owned(existing, marker):
            if not quiet:
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
    #
    # Files this resource delivered are always removed: the operator put them
    # there, and leaving them would keep a DAG running that nothing manages.
    # Ownership markers keep hand-managed files safe. This also cleans up when
    # spec.source was removed before the delete.
    #
    # The DAG record is a separate matter. Deleting it only wipes the run
    # history (and the DAG reappears if a file still defines it), so keep it
    # unless spec.deleteDag asks otherwise.
    logger.info(f"Deleting Airflow DAG resource: {name}")
    with track(
        "dag", "delete", logger, delay=10, retry=retry, max_retries=DELETE_MAX_RETRIES
    ):
        parsed = DagSpec.model_validate(spec)
        dag_id = parsed.dag_id or name
        backend = get_backend()
        if backend is not None and _SAFE_DAG_ID.fullmatch(dag_id):
            _delete_owned_files(
                backend,
                dag_id,
                _marker(namespace, name),
                logger,
                quiet=parsed.source is None,
            )
        if parsed.delete_dag:
            try:
                dags_api.delete_dag(dag_id=dag_id, _preload_content=False)
            except NotFoundException:
                logger.info(f"DAG {name} already absent in Airflow")
        else:
            logger.info(f"Leaving DAG {name} in Airflow; deleteDag is not set")
        MANAGED_RESOURCES.labels(resource_type="dag").dec()
    return {"message": f"DAG {name} deleted successfully."}
