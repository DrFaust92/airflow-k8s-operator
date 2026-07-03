import os

# Importing ``resources.*`` triggers ``config.client`` at import time, which
# requires Airflow auth to be configured. Provide harmless defaults so the pure
# unit tests can import the handler modules without real credentials. Real CI
# values (set via the environment) still take precedence.
os.environ.setdefault("AIRFLOW_HOST", "http://localhost:8080")
os.environ.setdefault("AIRFLOW_ACCESS_TOKEN", "unit-test-token")
