# airflow-k8s-operator

![Version: 0.22.0](https://img.shields.io/badge/Version-0.22.0-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: 0.9.0](https://img.shields.io/badge/AppVersion-0.9.0-informational?style=flat-square)

A Helm chart for Kubernetes Airflow Operator

## Maintainers

| Name | Email | Url |
| ---- | ------ | --- |
| DrFaust92 |  | <https://github.com/DrFaust92> |
| KyriosGN0 |  | <https://github.com/KyriosGN0> |

## Requirements

| Repository | Name | Version |
|------------|------|---------|
| file://../airflow-k8s-operator-crds | airflow-k8s-operator-crds | >= 0.1.0 |

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| affinity | object | `{}` |  |
| crds.create | bool | `true` | install the CRDs with this release. CRDs are cluster-scoped and shared, so for multi-tenant deploys install them once (e.g. the first release) and set this to false on additional tenant releases. |
| dnsConfig | map | `{}` | dnsConfig for the pod |
| env | list | `[]` | environment variables for the container (name/value entries) |
| envFrom | list | `[]` | envFrom sources (configMapRef or secretRef) |
| fullnameOverride | string | `""` | full chart name override |
| image.pullPolicy | string | `"IfNotPresent"` | the image pull policy This sets the pull policy for images. |
| image.repository | string | `"ghcr.io/drfaust92/airflow-k8s-operator"` | the repository of the image |
| image.tag | string | `""` | the image tag (overrides chart appVersion) Overrides the image tag whose default is the chart appVersion. |
| imagePullSecrets | list | `[]` | image pull secrets for private registries |
| livenessProbe | map | `{"httpGet":{"path":"/healthz","port":"http"}}` | liveness probe configuration |
| metricsPort | int | `9000` | metrics port for Prometheus scraping |
| nameOverride | string | `""` | short chart name override |
| nodeSelector | object | `{}` |  |
| operator.adoptNamespaces | list | `[]` | namespaces whose resources may adopt objects that already exist in Airflow (spec.adopt on Variables/Connections/Pools, and Dag resources without spec.source). Empty means only the release namespace; ["*"] allows every namespace. Resources can always manage objects they created. |
| operator.airflowHost | string | `""` | address for the operator |
| operator.basicAuthSecret.create | bool | `false` | whether to create the basic auth secret If set to false, you must provide a Kubernetes secret with the name specified in `secretName`. The secret must contain the keys specified in `usernameKey` and `passwordKey`. If the secret or keys do not exist, the deployment will fail. |
| operator.basicAuthSecret.enabled | bool | `false` | whether to use basic auth secrets |
| operator.basicAuthSecret.password | string | `""` | password to store in the created secret (required when create is true) |
| operator.basicAuthSecret.passwordKey | string | `"AIRFLOW_PASSWORD"` | key name for password in the secret |
| operator.basicAuthSecret.secretName | string | `"airflow-basic-auth"` | name of the basic auth secret |
| operator.basicAuthSecret.username | string | `""` | username to store in the created secret (required when create is true) |
| operator.basicAuthSecret.usernameKey | string | `"AIRFLOW_USERNAME"` | key name for username in the secret |
| operator.dagsSourcePrefixes | list | `[]` | gs:// or s3:// prefixes that Dag spec.source.objectRef may copy from (e.g. gs://staging-bucket/dags/). objectRef reads with the operator's credentials, so it is denied when this is empty. |
| operator.dagsUri | string | `""` | destination for DAG uploads (spec.source): gs://bucket/prefix, s3://bucket/prefix, or file:///path (e.g. a PVC added via volumes/volumeMounts). Empty disables DAG upload; Dag resources then adopt existing DAGs only. |
| operator.livenessProbeAddress | string | `"http://0.0.0.0:{{ .Values.port }}/healthz"` | liveness probe address for the operator |
| operator.logFormat | string | `"json"` | log format for the operator (full, json, simple) |
| operator.verbose | bool | `false` | enable kopf verbose (debug) logging |
| podAnnotations | map | `{}` | annotations to add to the pod |
| podLabels | map | `{}` | labels to add to the pod |
| podMonitor.enabled | bool | `false` | create a PodMonitor for Prometheus Operator |
| podMonitor.interval | string | `"30s"` | scrape interval for the PodMonitor |
| podMonitor.labels | map | `{}` | additional labels for the PodMonitor |
| podMonitor.path | string | `""` | metrics path for the PodMonitor |
| podMonitor.timeout | string | `"10s"` | scrape timeout for the PodMonitor |
| podSecurityContext | map | `{"runAsGroup":999,"runAsNonRoot":true,"runAsUser":999,"seccompProfile":{"type":"RuntimeDefault"}}` | pod-level security context. The image's `nonroot` user is UID/GID 999; it is set numerically because runAsNonRoot can't verify a named user. |
| port | int | `8080` | metrics port for the operator |
| resources | map | `{"limits":{"memory":"512Mi"},"requests":{"cpu":"50m","memory":"128Mi"}}` | resource requests and limits |
| revisionHistoryLimit | int | `10` | number of old ReplicaSets to retain to allow rollback |
| scope | string | `"cluster"` | deployment scope: "cluster" (one operator watches all namespaces, ClusterRole RBAC) or "namespaced" (operator watches only its release namespace with namespaced RBAC). Use "namespaced" for multi-tenancy: deploy one release per tenant namespace, each pointing at its own Airflow (operator.airflowHost). |
| securityContext | map | `{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]},"readOnlyRootFilesystem":true}` | container security context. The root filesystem is read-only; /tmp is an emptyDir. |
| serviceAccount.annotations | map | `{}` | annotations to add to the service account Annotations to add to the service account. |
| serviceAccount.automount | bool | `true` | whether to automount the service account token Automatically mount a ServiceAccount's API credentials? |
| serviceAccount.name | string | `""` | service account name (optional) The name of the service account to use. If not set and create is true, a name is generated using the fullname template. |
| tolerations | list | `[]` |  |
| volumeMounts | list | `[]` |  |
| volumes | list | `[]` |  |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
