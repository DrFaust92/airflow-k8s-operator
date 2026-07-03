# airflow-k8s-operator-crds

Cluster-scoped CustomResourceDefinitions for the Airflow Kubernetes Operator:
`Connection`, `Pool`, and `Variable` (`airflow.drfaust92/v1beta1`).

These are split into their own chart so they can be installed once and shared —
useful for multi-tenant clusters where several operator releases (one per
namespace, each pointing at its own Airflow) reuse the same CRDs.

The CRDs carry `helm.sh/resource-policy: keep`, so uninstalling this chart does
**not** delete the CRDs (and therefore does not cascade-delete the custom
resources across the cluster).

## Usage

Install the CRDs once (cluster-admin):

```bash
helm upgrade --install airflow-crds oci://ghcr.io/drfaust92/charts/airflow-k8s-operator-crds
```

Then deploy the operator chart with `crds.create=false` so it doesn't try to
manage the CRDs itself:

```bash
helm upgrade --install airflow-operator oci://ghcr.io/drfaust92/charts/airflow-k8s-operator \
  --set crds.create=false --set operator.airflowHost=http://airflow.example.com
```

The operator chart depends on this chart and installs it automatically when
`crds.create=true` (the default), so a single-tenant install needs no extra
step.
