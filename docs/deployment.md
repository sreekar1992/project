# Deployment guide and Kubernetes plan

## Local development

The clinical platform starts independently from the legacy `ecg-gui` workbench.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
AI_MODEL_PATH="$PWD/artifacts_final/model.pt" ecg-health-api --project "$PWD"
```

Without `DATABASE_URL`, development uses SQLite at `var/ecg_health.db`; without `OBJECT_STORAGE_BACKEND`, it uses private local files under `var/clinical_objects`. These are local-development defaults, not a shared or encrypted clinical deployment. Use `ecg-health-seed --project "$PWD"` only to create clearly fake development accounts and provide a new password interactively.

The frontend is a separate Vite client:

```sh
cd frontend
npm install
npm run dev
```

It proxies `/api` to `http://localhost:8080` by default. Build it with `npm run build`.

## Docker Compose

`docker-compose.yml` starts PostgreSQL, Redis, MinIO, the Flask backend, an RQ worker, and the nginx-served frontend. Copy `.env.example` to `.env`, replace every placeholder with unique local-development secrets, and ensure `artifacts_final/model.pt` is available for the read-only model mount. The S3-compatible storage adapter can instead target AWS S3 or OCI Object Storage's S3-compatible endpoint when the deployment supplies the appropriate endpoint, bucket, and credentials.

```sh
docker compose up --build
```

Compose binds the backend and MinIO console to loopback addresses by default. It is not an authorization to expose those services publicly. Its one-shot `migrate` service runs `ecg-health-migrate` before the API and worker start; the command applies reviewed Alembic revisions and creates only role/permission/feature reference rows. A real release process must likewise run a reviewed migration before serving requests rather than relying on automatic development schema creation. Compose sets `ALLOW_OBJECT_STORAGE_BUCKET_CREATE=true` only because its local MinIO volume begins empty. A production deployment must pre-provision the private bucket and leave that setting disabled.

`Dockerfile.platform` builds the API/worker image. The frontend has its own multi-stage image under `frontend/Dockerfile`.

## Health checks

- `/healthz` confirms the Flask process is responsive.
- `/readyz` returns ready only when `AI_MODEL_PATH` points to an existing compatible model artifact.

Neither endpoint validates clinical accuracy, database migration state, object-storage access, Redis connectivity, or external governance requirements.

## Kubernetes deployment scaffolding

`k8s/platform/` provides Kubernetes manifests for the platform API and RQ worker, plus ConfigMap and Secret templates, a Service, NetworkPolicy, and Kustomize entry point. They have not been applied to a cluster or validated as a production deployment. They deliberately require an external frontend/Ingress, migration release job using `ecg-health-migrate`, model PVC, private PostgreSQL/Redis/object storage, and populated Secret. The older private ECG workbench remains separately packaged under `k8s/base`.

A deployment must separately operate stateless frontend, API, and RQ worker workloads from stateful services. Use a managed or separately operated PostgreSQL service, private S3-compatible storage, Redis, a secret manager, TLS ingress, restrictive ingress and egress network policies, immutable model artifacts, and a non-root/read-only container configuration. Keep database backups, storage lifecycle, access logs, monitoring, and migration jobs outside the application image.

The cluster should never embed patient data, checkpoint secrets, passwords, or populated `.env` files into an image or ConfigMap. Use Kubernetes Secrets only as a delivery mechanism from an approved secret-management process; configure distinct secret values, rotation, scoped service accounts, egress policy, and tenant-aware operational monitoring. These are deployment requirements to design and verify, not production guarantees of the supplied scaffolding.
