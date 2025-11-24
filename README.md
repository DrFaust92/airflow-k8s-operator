# Airflow Kubernetes Operator

**Note: This project is currently in draft status and under active development.**

This Kubernetes operator provides a way to manage Airflow resources within a Kubernetes cluster.

## Features

- **Airflow Variables**: Management of Airflow variables
- **Airflow Connections**: Management of Airflow connections

## Roadmap (TBD)

- Airflow pools
- Support for AWS managed Airflow
- Support for fetching sensitive values from Kubernetes secrets

## Compatibility

Currently supports **Airflow v2** (tested on v2.10.0). Airflow v3 is not yet supported.

## Authentication

The operator supports the following authentication methods:

### Google Cloud Authentication

Recommended for Google Cloud Composer environments. This method uses Application Default Credentials (ADC) with Google Cloud authentication.

**Environment Variables:**

- `USE_GOOGLE_AUTH`: Set to `true` to enable Google Cloud authentication

**Example:**

```bash
export AIRFLOW_HOST=https://your-composer-environment.appspot.com
export USE_GOOGLE_AUTH=true
```

The operator will automatically obtain credentials from the environment (service account, Application Default Credentials, etc.) and refresh the authentication token before each API call.

### Username/Password Authentication

For Airflow instances with basic authentication enabled.

**Environment Variables:**

- `AIRFLOW_USERNAME`: The Airflow username
- `AIRFLOW_PASSWORD`: The Airflow password

**Example:**

```bash
export AIRFLOW_HOST=http://airflow.example.com
export AIRFLOW_USERNAME=admin
export AIRFLOW_PASSWORD=your_password
```

### Configuration

**AIRFLOW_HOST**: Set the base URL of your Airflow instance. The operator will automatically append `/api/v1` if not already present. Trailing slashes are automatically stripped before appending the API endpoint.

Example:

```bash
AIRFLOW_HOST=http://airflow.example.com
AIRFLOW_HOST=http://airflow.example.com/
```

Both will result in: `http://airflow.example.com/api/v1`

## Testing Locally

The recommended approach for local testing is to set up a local Kubernetes cluster using [kind](https://kind.sigs.k8s.io/) and deploy Airflow within it.

### Prerequisites

- Docker
- kind
- kubectl
- Python 3.12+

### Setting Up a Local Testing Environment

1. Create a local kind cluster:

```bash
kind create cluster
```

1. Deploy Airflow to the cluster:

```bash
helm repo add apache-airflow https://airflow.apache.org && helm repo update
helm upgrade --install airflow apache-airflow/airflow --version 1.16.0 \
            --set postgresql.image.repository=bitnamilegacy/postgresql \
            --set postgresql.image.tag=16.1.0-debian-11-r15 \
            --set executor=KubernetesExecutor \
            --set workers.replicas=0 \
            --set redis.enabled=false \
            --set triggerer.enabled=false \
            --set statsd.enabled=false \
            --set createUserJob.useHelmHooks=false \
            --set createUserJob.applyCustomEnv=false \
            --set migrateDatabaseJob.useHelmHooks=false \
            --set migrateDatabaseJob.applyCustomEnv=false \
            --set webserver.resources.requests.memory=1Gi \
            --set webserver.resources.limits.memory=1Gi \
            --set config.api.auth_backend=airflow.api.auth.backend.basic_auth \
            --timeout 3m \
            --wait
kubectl port-forward svc/airflow-webserver 8080:8080
```

This deploys the necessary resources to your local kind cluster for testing.

1. Deploy the Custom Resource Definitions (CRDs):

```bash
kubectl apply -f chart/airflow-k8s-operator/templates/crds/
```

This applies the Airflow Variable and Connection CRDs to your cluster, enabling you to manage Airflow resources using Kubernetes custom resources.

1. Deploy a Custom Resource:

After deploying the CRDs, you can create Airflow Variables and Connections as Kubernetes custom resources. Examples are available in the `tests/` directory.

**Deploy an Airflow Variable:**

```bash
kubectl apply -f tests/variable.yaml
```

**Deploy an Airflow Connection:**

```bash
kubectl apply -f tests/connection.yaml
```

These custom resources will be automatically synchronized with your Airflow instance.

1. Run the test suite:

```bash
python tests/operator_test.py
```

### Cleaning Up

To remove the test resources:

```bash
kubectl delete -f tests/
```

To delete the kind cluster:

```bash
kind delete cluster
```

## Installation

[Installation instructions to be added]

## Usage

[Usage examples to be added]

## Contributing

[Contributing guidelines to be added]

## License

Licensed under the Apache License, Version 2.0.
