import datetime

import kopf
import prometheus_client as prometheus

import resources.connections  # noqa: F401
import resources.pools  # noqa: F401
import resources.variables  # noqa: F401

prometheus.start_http_server(9000)


@kopf.on.probe(id="now")
def get_current_timestamp(**kwargs):
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
