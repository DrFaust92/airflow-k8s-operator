import logging
from types import SimpleNamespace

import kopf
import pytest
from airflow_client.client.exceptions import (
    ApiException,
    ForbiddenException,
    UnauthorizedException,
)
from prometheus_client import REGISTRY

from config.reconcile import track

logger = logging.getLogger("test")


def _patch():
    return SimpleNamespace(status={})


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


def _giveups(operation):
    return _counter(
        "airflow_resource_giveups_total",
        resource_type="unittest",
        operation=operation,
    )


def test_success_reflects_synced_status():
    patch = _patch()
    with track("unittest", "create", logger, patch=patch):
        pass
    assert patch.status["phase"] == "Synced"


def test_failure_reflects_error_status():
    patch = _patch()
    with pytest.raises(kopf.TemporaryError):
        with track("unittest", "update", logger, patch=patch):
            raise ValueError("boom")
    assert patch.status["phase"] == "Error"


def test_delete_does_not_write_status():
    patch = _patch()
    with track("unittest", "delete", logger, patch=patch):
        pass
    # Deletes remove the object, so no status is written.
    assert patch.status == {}


def test_giveup_on_final_retry_emits_metric():
    before = _giveups("delete")
    with pytest.raises(kopf.TemporaryError):
        # retry=4 is the 5th (final) attempt when max_retries=5.
        with track("unittest", "delete", logger, retry=4, max_retries=5):
            raise ValueError("backend down")
    assert _giveups("delete") == before + 1


def test_no_giveup_before_final_retry():
    before = _giveups("delete")
    with pytest.raises(kopf.TemporaryError):
        with track("unittest", "delete", logger, retry=0, max_retries=5):
            raise ValueError("backend down")
    # Not the final attempt yet -> no give-up recorded.
    assert _giveups("delete") == before


def test_permanent_status_raises_permanent_error():
    # A 4xx that won't succeed on retry must stop the loop.
    with pytest.raises(kopf.PermanentError):
        with track("unittest", "create", logger):
            raise ApiException(status=422, reason="unprocessable")


def test_retryable_status_raises_temporary_error():
    # A 5xx is transient and should keep retrying.
    with pytest.raises(kopf.TemporaryError):
        with track("unittest", "create", logger):
            raise ApiException(status=503, reason="unavailable")


def test_unauthorized_is_retryable_and_marks_error():
    # 401 (bad/missing credentials) is deliberately retryable so the operator
    # self-heals once the credentials/secret are fixed -- surfaced as Error but
    # NOT downgraded to a PermanentError that would park the resource.
    patch = _patch()
    with pytest.raises(kopf.TemporaryError):
        with track("unittest", "create", logger, patch=patch):
            raise UnauthorizedException(status=401, reason="Unauthorized")
    assert patch.status["phase"] == "Error"


def test_forbidden_is_retryable_and_marks_error():
    # 403 is also retryable: on Airflow 3 a bad/expired JWT surfaces as
    # 403 "Invalid JWT", which the client refresh (or a secret rotation) can
    # resolve, so it must not be treated as permanent.
    patch = _patch()
    with pytest.raises(kopf.TemporaryError):
        with track("unittest", "create", logger, patch=patch):
            raise ForbiddenException(status=403, reason="Forbidden")
    assert patch.status["phase"] == "Error"
