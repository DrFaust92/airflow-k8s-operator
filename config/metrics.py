import prometheus_client as prometheus

# Core reconciliation metrics
RESOURCE_OPERATIONS = prometheus.Counter(
    "airflow_resource_operations_total",
    "Total number of resource operations",
    ["resource_type", "operation", "status"],
)

RESOURCE_RECONCILIATION_DURATION = prometheus.Histogram(
    "airflow_resource_reconciliation_duration_seconds",
    "Time spent on reconciliation operations",
    ["resource_type", "operation"],
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
)

# API interaction metrics
AIRFLOW_API_REQUESTS = prometheus.Counter(
    "airflow_api_requests_total",
    "Total number of Airflow API requests",
    ["method", "endpoint", "status_code"],
)

AIRFLOW_API_DURATION = prometheus.Histogram(
    "airflow_api_request_duration_seconds",
    "Duration of Airflow API requests",
    ["method", "endpoint"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

AIRFLOW_API_ERRORS = prometheus.Counter(
    "airflow_api_errors_total", "Total number of Airflow API errors", ["error_type"]
)

# Resource state metrics
MANAGED_RESOURCES = prometheus.Gauge(
    "airflow_managed_resources", "Current count of managed resources", ["resource_type"]
)

RECONCILIATION_FAILURES = prometheus.Counter(
    "airflow_reconciliation_failures_total",
    "Total number of failed reconciliation attempts",
    ["resource_type"],
)

AUTH_FAILURES = prometheus.Counter(
    "airflow_auth_failures_total",
    "Total number of authentication failures",
    ["auth_type"],
)
