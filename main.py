import datetime
import logging

import kopf
import prometheus_client as prometheus

import resources.connections  # noqa: F401
import resources.pools  # noqa: F401
import resources.variables  # noqa: F401

# Serving metrics is best-effort: if the port is unavailable, keep the operator
# running (reconciliation is what matters) rather than crashing at startup.
try:
    prometheus.start_http_server(9000)
except OSError as e:
    logging.getLogger(__name__).warning(
        "Could not start metrics server on :9000: %s", e
    )


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **kwargs):
    # Keep kopf's own bookkeeping in annotations so it does not collide with the
    # structural CRD status schema (which prunes undeclared status fields). This
    # leaves ``status`` for the operator's user-facing phase/message.
    settings.persistence.finalizer = "airflow.drfaust92/finalizer"
    settings.persistence.progress_storage = kopf.AnnotationsProgressStorage(
        prefix="airflow.drfaust92"
    )
    settings.persistence.diffbase_storage = kopf.AnnotationsDiffBaseStorage(
        prefix="airflow.drfaust92", key="last-handled-configuration"
    )


@kopf.on.probe(id="now")
def get_current_timestamp(**kwargs):
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
