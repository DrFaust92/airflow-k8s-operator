import time
from contextlib import contextmanager

import kopf

from config.metrics import (
    RECONCILIATION_FAILURES,
    RESOURCE_OPERATIONS,
    RESOURCE_RECONCILIATION_DURATION,
)


@contextmanager
def track(resource_type: str, operation: str, logger, delay: int = 30):
    """Measure a reconcile operation and surface failures to kopf.

    Records the operation duration and a success/failure counter. On any
    exception the failure metrics are recorded and the error is re-raised as a
    ``kopf.TemporaryError`` (retried after ``delay`` seconds) so kopf retries
    instead of treating the handler as succeeded. Returning normally from a
    handler is treated by kopf as success, so failures MUST raise -- otherwise a
    failed create/update looks reconciled and a failed delete drops the
    finalizer, orphaning the Airflow object.

    Delete handlers should also cap retries (``@kopf.on.delete(..., retries=N)``)
    so kopf eventually gives up and releases the finalizer rather than blocking
    the resource's deletion forever when the backend is unreachable.
    """
    start = time.time()
    try:
        yield
    except kopf.PermanentError:
        _record(resource_type, operation, start, ok=False)
        raise
    except kopf.TemporaryError:
        _record(resource_type, operation, start, ok=False)
        raise
    except Exception as e:
        _record(resource_type, operation, start, ok=False)
        logger.error(f"Failed to {operation} {resource_type}: {e}")
        raise kopf.TemporaryError(
            f"Failed to {operation} {resource_type}: {e}", delay=delay
        ) from e
    else:
        _record(resource_type, operation, start, ok=True)


def _record(resource_type: str, operation: str, start: float, ok: bool):
    RESOURCE_RECONCILIATION_DURATION.labels(
        resource_type=resource_type, operation=operation
    ).observe(time.time() - start)
    RESOURCE_OPERATIONS.labels(
        resource_type=resource_type,
        operation=operation,
        status="success" if ok else "failure",
    ).inc()
    if not ok:
        RECONCILIATION_FAILURES.labels(resource_type=resource_type).inc()
