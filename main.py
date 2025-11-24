import variables  # noqa: F401
import connections  # noqa: F401
import datetime
import kopf

@kopf.on.probe(id='now')
def get_current_timestamp(**kwargs):
    return datetime.datetime.now(datetime.timezone.utc).isoformat()