# Kubernetes platform deployment

These manifests deploy the separate authenticated ECG health-platform API and
its RQ worker. They do not deploy the legacy local workbench. This is deployment
scaffolding, not clinical validation, regulatory compliance,
or authorization to process real patient data.

## Before applying

1. Build and publish `Dockerfile.platform` with an immutable image digest.
2. Provision private PostgreSQL, Redis, and S3-compatible object storage.
3. Provision a read-only `ecg-platform-models` PVC with a verified `model.pt`
   at its root. Do not mount research source data or encryption keys into the
   API Pod.
4. Copy `secret.example.yaml` outside this repository, replace every value, and
   create `ecg-platform-secrets` with your deployment secret manager. Never
   commit a populated Secret.
5. Edit the ConfigMap or an overlay for private service hostnames, bucket,
   approved CORS origin, and registered model version.
6. Run `ecg-health-migrate` once as a controlled release job, then apply the
   rendered manifests. It creates reference rows only; provision real users
   through an approved administrative process.

```sh
kubectl kustomize k8s/platform
kubectl apply -k k8s/platform
kubectl -n ecg-clinical-research rollout status deployment/ecg-platform-api
kubectl -n ecg-clinical-research port-forward service/ecg-platform-api 8080:8080
```

The Service is intentionally `ClusterIP`. Add a TLS-terminating, authenticated
Ingress only after identity-provider, privacy, retention, threat-model, and
clinical governance review. Customize egress policies and source selectors for
your cluster before deploying beyond a private research environment.
