import logging

import kopf
import pytest
from prometheus_client import REGISTRY

from config.reconcile import track

logger = logging.getLogger("test")


def _counter(name, **labels):
    """Current value of a labelled counter sample (0.0 if not yet observed)."""
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _ops(operation, status):
    return _counter(
        "airflow_resource_operations_total",
        resource_type="unittest",
        operation=operation,
        status=status,
    )


def _failures():
    return _counter("airflow_reconciliation_failures_total", resource_type="unittest")


def _duration_count(operation):
    return _counter(
        "airflow_resource_reconciliation_duration_seconds_count",
        resource_type="unittest",
        operation=operation,
    )


def test_success_records_metrics_and_does_not_raise():
    before_ok = _ops("create", "success")
    before_dur = _duration_count("create")

    with track("unittest", "create", logger):
        pass

    assert _ops("create", "success") == before_ok + 1
    assert _duration_count("create") == before_dur + 1


def test_failure_is_reraised_as_temporary_error():
    before_fail = _ops("update", "failure")
    before_failures = _failures()
    before_dur = _duration_count("update")

    with pytest.raises(kopf.TemporaryError):
        with track("unittest", "update", logger):
            raise ValueError("boom")

    # A generic failure is wrapped so kopf retries instead of treating the
    # handler as succeeded.
    assert _ops("update", "failure") == before_fail + 1
    assert _failures() == before_failures + 1
    assert _duration_count("update") == before_dur + 1


def test_permanent_error_propagates_unwrapped():
    before_fail = _ops("delete", "failure")

    with pytest.raises(kopf.PermanentError):
        with track("unittest", "delete", logger):
            raise kopf.PermanentError("do not retry")

    # Still counted as a failure, but the permanent semantics are preserved
    # (not downgraded to a retryable TemporaryError).
    assert _ops("delete", "failure") == before_fail + 1


def test_temporary_error_propagates_unwrapped():
    with pytest.raises(kopf.TemporaryError) as excinfo:
        with track("unittest", "create", logger):
            raise kopf.TemporaryError("original message", delay=99)

    # Re-raised as-is rather than double-wrapped with the "Failed to ..." prefix.
    assert "original message" in str(excinfo.value)
    assert "Failed to create" not in str(excinfo.value)
