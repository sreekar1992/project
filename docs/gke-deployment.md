# Deploy the research platform to GKE with Terraform

`infra/gke` provisions an Autopilot GKE cluster in `asia-south1` by default, a private VPC with Cloud NAT, Artifact Registry, private Cloud SQL for PostgreSQL, private Memorystore Redis, a private Cloud Storage bucket containing `artifacts_final/model.pt`, and service accounts for the application and GitHub Actions. The application uses Workload Identity to read the model and store private assets in Cloud Storage. The frontend receives an **internal HTTP load balancer**; reach it only from a network connected to the VPC.

The cluster, Cloud SQL, Redis, load balancer, and NAT incur charges while running. Terraform state contains the database password; use a restricted, encrypted remote backend before applying in a shared environment. Cloud SQL and GKE have deletion protection, and the bucket has `force_destroy = false`.

## 1. Apply infrastructure

Use an existing Google Cloud project with billing enabled. The operator needs permission to enable APIs and create the resources in `infra/gke`. Install Terraform 1.6+, Google Cloud CLI, and `kubectl`, then authenticate with an identity authorized for that project.

```sh
cp infra/gke/terraform.tfvars.example infra/gke/terraform.tfvars
# Set project_id in infra/gke/terraform.tfvars.
gcloud auth application-default login
terraform -chdir=infra/gke init
terraform -chdir=infra/gke plan
terraform -chdir=infra/gke apply
terraform -chdir=infra/gke output
```

The project ID is intentionally required; Terraform cannot create resources without a destination project. Set `region` in the tfvars file if `asia-south1` is unsuitable. The model object comes from `artifacts_final/model.pt`; verify it is the intended model before applying.

## 2. Create the application Secret

The Terraform apply creates the Cloud SQL password in Secret Manager. This one-time command obtains GKE credentials, creates the namespace, and creates a Kubernetes Secret with the database URL, Redis URL, and two new application secrets. It does not print them and preserves an existing Secret.

```sh
python3 scripts/bootstrap_gke_secret.py
```

The operator must have access to the Secret Manager version and Kubernetes namespace. Keep access to Terraform state and Kubernetes Secrets tightly scoped. If you rotate the Cloud SQL password, update the Kubernetes Secret in the same operation; the bootstrap script deliberately does not overwrite it.

## 3. Configure GitHub Actions

Create the GitHub environment `gke-research` for `sreekar1992/project` and set the following environment **variables**. All values except the model version and allowed origins come from `terraform -chdir=infra/gke output -raw OUTPUT_NAME`.

| GitHub variable | Terraform output or value |
| --- | --- |
| `GCP_PROJECT_ID` | `project_id` |
| `GAR_LOCATION` | `artifact_registry_location` |
| `GAR_REPOSITORY` | `artifact_registry_repository` |
| `GKE_CLUSTER` | `cluster_name` |
| `GKE_LOCATION` | `cluster_location` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `github_workload_identity_provider` |
| `GCP_DEPLOY_SERVICE_ACCOUNT` | `github_deploy_service_account` |
| `GCP_WORKLOAD_SERVICE_ACCOUNT` | `workload_service_account` |
| `OBJECT_STORAGE_BUCKET` | `asset_bucket` |
| `AI_MODEL_VERSION` | Version identifier for the verified model |
| `CORS_ORIGINS` | Approved frontend origin |
| `ECG_PLATFORM_TRUSTED_HOSTS` | Comma-separated allowed API hosts, including `ecg-platform-api` |

Protect the GitHub environment with deployment review rules. The federation provider accepts OIDC tokens only from the configured GitHub repository on `main`. Terraform grants the deploy account Artifact Registry writer and GKE developer access in this project. Restrict these roles further if the project hosts other workloads. GitHub hosted runners must reach the GKE public control plane; the nodes and application load balancer remain private.

Run **Deploy clinical platform to GKE** manually in GitHub Actions after the infrastructure and environment are ready. The workflow builds digest-pinned API and frontend images, applies the Workload Identity service account, runs the database migration, deploys the API, worker, and frontend, and waits for rollout. The init containers download the Terraform-managed model object to each pod. The frontend proxies `/api/` to the in-cluster API.

## 4. Verify

```sh
gcloud container clusters get-credentials "$(terraform -chdir=infra/gke output -raw cluster_name)" \
  --region "$(terraform -chdir=infra/gke output -raw cluster_location)" \
  --project "$(terraform -chdir=infra/gke output -raw project_id)"
kubectl -n ecg-clinical-research get pods
kubectl -n ecg-clinical-research get service ecg-platform-frontend
```

Check `/healthz`, `/readyz`, database connectivity, object storage, and a fake-data workflow from inside the approved private network. `/readyz` checks model presence only. Add TLS termination and an approved access gateway before using sensitive data. This research application is not a clinically validated service.
