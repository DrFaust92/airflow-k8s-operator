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

## Installation

[Installation instructions to be added]

## Usage

[Usage examples to be added]

## Contributing

[Contributing guidelines to be added]

## License

Licensed under the Apache License, Version 2.0.
