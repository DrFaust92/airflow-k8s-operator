import time
from contextlib import contextmanager

import kopf

from config.metrics import (
    RECONCILIATION_FAILURES,
    RESOURCE_GIVEUPS,
    RESOURCE_OPERATIONS,
    RESOURCE_RECONCILIATION_DURATION,
)

# How many times a delete handler retries before kopf gives up and releases the
# finalizer (see resources/*.py delete handlers and the giveup handling below).
DELETE_MAX_RETRIES = 5


@contextmanager
def track(
    resource_type: str,
    operation: str,
    logger,
    *,
    patch=None,
    delay: int = 30,
    retry: int | None = None,
    max_retries: int | None = None,
):
    """Measure a reconcile operation, reflect its outcome, and retry on failure.

    Responsibilities:

    * Records the operation duration and a success/failure counter.
    * Reflects the outcome on the custom resource's ``status`` (``phase`` +
      ``message``) via ``patch`` so ``kubectl get/describe`` shows the state.
    * Re-raises failures as ``kopf.TemporaryError`` (retried after ``delay`` s)
      so kopf retries instead of treating the handler as succeeded -- returning
      normally is treated by kopf as success, which for a delete would drop the
      finalizer and orphan the Airflow object.
    * When a capped handler (``retry``/``max_retries`` set, i.e. deletes) is on
      its final attempt, emits a Warning event + ``RESOURCE_GIVEUPS`` metric so
      the give-up (and possible orphan) is never silent.
    """
    start = time.time()
    try:
        yield
    except kopf.PermanentError as e:
        _record(resource_type, operation, start, ok=False)
        _set_status(patch, operation, "Error", str(e))
        raise
    except kopf.TemporaryError as e:
        _record(resource_type, operation, start, ok=False)
        _set_status(patch, operation, "Error", str(e))
        raise
    except Exception as e:
        _record(resource_type, operation, start, ok=False)
        _set_status(patch, operation, "Error", f"{operation} failed: {e}")
        logger.error(f"Failed to {operation} {resource_type}: {e}")
        if max_retries is not None and retry is not None and retry + 1 >= max_retries:
            RESOURCE_GIVEUPS.labels(
                resource_type=resource_type, operation=operation
            ).inc()
            # logger.warning on a per-object handler logger is posted as a
            # Warning event on the resource by kopf.
            logger.warning(
                f"Giving up after {max_retries} attempts to {operation} "
                f"{resource_type}; the Airflow object may be left orphaned."
            )
        raise kopf.TemporaryError(
            f"Failed to {operation} {resource_type}: {e}", delay=delay
        ) from e
    else:
        _record(resource_type, operation, start, ok=True)
        _set_status(patch, operation, "Synced", f"{operation} succeeded")


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


def _set_status(patch, operation: str, phase: str, message: str):
    # Deletes remove the object, so there is no status to reflect.
    if patch is None or operation == "delete":
        return
    patch.status["phase"] = phase
    patch.status["message"] = message
