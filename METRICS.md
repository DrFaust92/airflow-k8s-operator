# Prometheus Metrics Documentation

This document describes all Prometheus metrics exposed by the Airflow Kubernetes Operator on port `9000`.

## Accessing Metrics

Metrics are exposed at `http://localhost:9000/metrics` by default. The metrics endpoint is started automatically when the operator launches.

## Core Reconciliation Metrics

### `airflow_resource_operations_total`
**Type:** Counter
**Labels:** `resource_type`, `operation`, `status`
**Description:** Total number of resource operations performed by the operator.

- `resource_type`: `variable`, `connection`, or `pool`
- `operation`: `create`, `update`, or `delete`
- `status`: `success` or `failure`

**Use Cases:**
- Monitor the rate of operations across different resource types
- Track success/failure ratios
- Identify which operations are most frequent

**Example Queries:**
```promql
# Rate of successful operations per second
rate(airflow_resource_operations_total{status="success"}[5m])

# Failure rate by resource type
rate(airflow_resource_operations_total{status="failure"}[5m])

# Total operations by type
sum by (resource_type, operation) (airflow_resource_operations_total)
```

---

### `airflow_resource_reconciliation_duration_seconds`
**Type:** Histogram
**Labels:** `resource_type`, `operation`
**Buckets:** `[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]`
**Description:** Duration of reconciliation operations in seconds.

**Use Cases:**
- Monitor reconciliation performance
- Detect slow operations
- Set SLOs for reconciliation time

**Example Queries:**
```promql
# 95th percentile reconciliation time by resource type
histogram_quantile(0.95, sum by (resource_type, operation, le) (
  rate(airflow_resource_reconciliation_duration_seconds_bucket[5m])
))

# Average reconciliation duration
rate(airflow_resource_reconciliation_duration_seconds_sum[5m]) /
rate(airflow_resource_reconciliation_duration_seconds_count[5m])
```

---

### `airflow_reconciliation_failures_total`
**Type:** Counter
**Labels:** `resource_type`
**Description:** Total number of failed reconciliation attempts.

**Use Cases:**
- Alert on reconciliation failures
- Track reliability by resource type

**Example Queries:**
```promql
# Failure rate per resource type
rate(airflow_reconciliation_failures_total[5m])

# Total failures in the last hour
increase(airflow_reconciliation_failures_total[1h])
```

---

## Resource State Metrics

### `airflow_managed_resources`
**Type:** Gauge
**Labels:** `resource_type`
**Description:** Current count of resources managed by the operator.

**Use Cases:**
- Monitor the scale of managed resources
- Track resource growth over time
- Capacity planning

**Example Queries:**
```promql
# Total managed resources
sum(airflow_managed_resources)

# Resources by type
airflow_managed_resources
```

---

## API Interaction Metrics

### `airflow_api_requests_total`
**Type:** Counter
**Labels:** `method`, `endpoint`, `status_code`
**Description:** Total number of Airflow API requests made.

**Note:** Currently instrumented via OpenTelemetry RequestsInstrumentor. Can be enhanced with custom tracking for more detailed endpoint-level metrics.

---

### `airflow_api_request_duration_seconds`
**Type:** Histogram
**Labels:** `method`, `endpoint`
**Buckets:** `[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]`
**Description:** Duration of Airflow API requests in seconds.

**Note:** Metric defined but requires custom instrumentation of the Airflow client for full functionality.

---

### `airflow_api_errors_total`
**Type:** Counter
**Labels:** `error_type`
**Description:** Total number of Airflow API errors encountered.

**Note:** Metric defined but requires custom instrumentation for full functionality.

---

## Authentication Metrics

---

### `airflow_auth_failures_total`
**Type:** Counter
**Labels:** `auth_type`
**Description:** Total number of authentication failures.

**Use Cases:**
- Alert on authentication issues
- Track authentication reliability

---

## Recommended Alerts

### High Reconciliation Failure Rate
```yaml
- alert: HighReconciliationFailureRate
  expr: rate(airflow_reconciliation_failures_total[5m]) > 0.1
  for: 5m
  annotations:
    summary: "High reconciliation failure rate detected"
    description: "{{ $labels.resource_type }} reconciliation failing at {{ $value }} per second"
```

### Slow Reconciliation
```yaml
- alert: SlowReconciliation
  expr: |
    histogram_quantile(0.95,
      rate(airflow_resource_reconciliation_duration_seconds_bucket[5m])
    ) > 10
  for: 5m
  annotations:
    summary: "Slow reconciliation detected"
    description: "95th percentile reconciliation time is {{ $value }}s"
```

### Authentication Failures
```yaml
- alert: AuthenticationFailures
  expr: rate(airflow_auth_failures_total[5m]) > 0
  for: 2m
  annotations:
    summary: "Authentication failures detected"
    description: "{{ $labels.auth_type }} auth failing at {{ $value }} per second"
```

---

## Grafana Dashboard Recommendations

### Operator Overview Panel
- Managed resources (gauge)
- Event rate (graph)
- Success vs failure rate (graph)

### Reconciliation Performance Panel
- Reconciliation duration heatmap
- Operations by resource type (pie chart)
- Success/failure ratio (stat)

### API Health Panel
- API request duration (graph)
- API error rate (graph)
- Authentication metrics (stat)

---
