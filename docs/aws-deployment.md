# AWS deployment runbook — ECG research clinical platform

> **Status:** planning and deployment guidance only. No AWS account, VPC,
> Kubernetes cluster, container registry, database, cache, bucket, IAM role, or
> load balancer has been created or validated by this repository. Treat every
> identifier in this document as a placeholder.

This runbook describes a possible AWS deployment shape for the separate
authenticated ECG health-platform application in this repository. It does not
deploy the legacy `ecg-gui` workbench, its local encryption/camouflage
demonstration, the Mendeley MLII source data, or a training environment.

The application is a research and qualified-clinician-review workflow. It is
not a validated diagnostic device, a treatment recommender, a prescribing
system, an emergency-triage service, or evidence of clinical, regulatory,
privacy, HIPAA, DPDP, GDPR, ISO, or security compliance. Do not put real
patient data into this design until the institution has completed its own
clinical-safety, privacy, security, legal, procurement, and operational review.

For model export and cross-platform local operations, read
[model-export-cross-platform.md](model-export-cross-platform.md). For the
application's existing limitations, also read [architecture.md](architecture.md),
[deployment.md](deployment.md), [security.md](security.md), and
[authentication.md](authentication.md).

## 1. What this repository currently provides

The AWS architecture below must respect the actual implementation rather than
assuming production capabilities that are not in the source tree.

| Area | Current repository fact | AWS consequence |
| --- | --- | --- |
| API and worker image | `Dockerfile.platform` builds a non-root Python 3.12 API/worker image; the worker runs `ecg-health-worker`. | Build and publish an immutable image to Amazon ECR. API and worker use the same image with different commands. |
| Frontend | `frontend/Dockerfile` builds a static React client served by nginx. There is no frontend Kubernetes Deployment, Service, or Ingress manifest. | Add and test a frontend workload and routing overlay outside the supplied `k8s/platform/` scaffolding. |
| Kubernetes manifests | `k8s/platform/` has API/worker Deployments, one `ClusterIP` API Service, a ConfigMap, and a limited ingress NetworkPolicy. It has no model PVC, migration Job, frontend, Ingress, HPA, PDB, service account, secret synchronization, or EKS integration. | Treat the manifests as a starting point, not a ready-to-apply AWS environment. Build a reviewed AWS overlay before applying anything. |
| Database | The API uses SQLAlchemy with PostgreSQL (`postgresql+psycopg://...`) in production. `ecg-health-migrate` applies Alembic revisions and reference rows. | Use private Amazon RDS for PostgreSQL. Run the migration command as a controlled release step before API/worker rollout. |
| Jobs | With `ASYNC_ANALYSIS=true`, RQ uses Redis and deliberately queues analysis and organization IDs, not waveform bytes. | Use a private ElastiCache Redis OSS/Valkey-compatible deployment only after validating the chosen connection mode with RQ. |
| Clinical objects | The API stores ECG files, Grad-CAM PNGs, and report PDFs through an S3-compatible adapter. Object keys begin with `organizations/<UUID>/...`. | Use a private S3 clinical-object bucket with a narrowly scoped prefix policy, versioning, audit controls, and lifecycle policy. |
| Model | The API accepts a native PyTorch `model.pt` file at `AI_MODEL_PATH`; the included clinical adapter does **not** load TorchScript (`model.ts`) or ONNX (`model.onnx`). | Deliver a verified native `model.pt` to `/models/model.pt` through a read-only volume. Do not set `AI_MODEL_PATH` to an ONNX or TorchScript file. |
| Object-store credentials | `S3PrivateStorage` currently requires `OBJECT_STORAGE_ENDPOINT`, `OBJECT_STORAGE_BUCKET`, `OBJECT_STORAGE_ACCESS_KEY`, and `OBJECT_STORAGE_SECRET_KEY` and passes explicit credentials to boto3. It cannot currently use EKS Pod Identity / the default AWS credential chain. | Store a restricted access-key pair in Secrets Manager and inject it as environment variables as an interim measure. A code refactor is required before IAM-only Pod Identity can replace those credentials. |
| S3 encryption header | The adapter writes `ServerSideEncryption="AES256"`, which requests SSE-S3. It has no setting for an AWS KMS key or SSE-KMS. | Do not claim customer-managed KMS encryption for application-written objects without changing and testing the adapter. A bucket policy that requires `aws:kms` will reject the current writes. |
| Health endpoints | `/healthz` checks process responsiveness. `/readyz` currently checks only that `AI_MODEL_PATH` names an existing file. Neither tests database, Redis, S3, migration state, model load, model accuracy, or clinical readiness. | Use `/readyz` only as a basic target/pod signal and add release smoke tests and dependency monitoring. |
| Authentication | Current login is local password + HS256 JWT. There is no SSO/OIDC/SAML, MFA, password-reset, CSRF design, token-key rotation, or enterprise identity integration. | Do not treat an ALB or EKS deployment as hospital-grade identity. Design an identity and privileged-access solution separately. |

## 2. Target AWS architecture

The following is an intended *research-environment* layout, not an assertion
that it has been provisioned.

```text
Approved research users
       |
       | HTTPS, private access/VPN/zero-trust gateway as approved
       v
Application Load Balancer + ACM certificate
       |
       +-- /        -> frontend Service -> nginx/React Pods
       +-- /api/... -> ecg-platform-api Service -> API Pods
                                                  |
               +----------------------------------+------------------+
               |                                  |                  |
               v                                  v                  v
          RDS PostgreSQL                  ElastiCache Redis       S3 clinical
          private subnets                 private subnets         objects bucket
               ^                                  ^                  ^
               |                                  |                  |
               +-------------- EKS private worker/API Pods -----------+
                                      |
                                      v
                          read-only model volume
                          /models/model.pt
                                      ^
                                      |
                 verified model-artifact S3 bucket -> controlled delivery job

CloudWatch Logs/Metrics/Alarms, CloudTrail, AWS Config, backup and incident
processes are operated around the environment; they are not supplied by the app.
```

### Recommended AWS services and boundaries

| Service | Purpose in this design | Important boundary |
| --- | --- | --- |
| Amazon VPC | Isolated network across at least two Availability Zones. | EKS nodes, RDS, ElastiCache, and storage endpoints stay in private subnets. The ALB is public only if governance explicitly allows it. |
| Amazon ECR | Private registry for the platform and frontend images. | Push immutable image digests after test/scan approval; do not store model data, MLII data, `.env`, or patient data in images. |
| Amazon EKS | Runs stateless frontend, API, migration, and RQ worker workloads. | The supplied Kubernetes files are incomplete scaffolding. EKS does not make the software clinically validated or compliant. |
| Amazon RDS for PostgreSQL | Persistent clinical workflow metadata, users, RBAC, audit rows, and model-analysis records. | Private endpoint only. Object bytes stay outside the relational database. Backups and restore exercises need an owner. |
| Amazon ElastiCache | Redis endpoint for optional RQ analysis jobs. | The source uses the standard `redis.Redis` client, so select a compatible non-cluster-mode endpoint unless Redis Cluster support has been explicitly validated. No ECG waveform payload should enter Redis. |
| Amazon S3 — clinical objects | Private ECG MAT/CSV files, Grad-CAM files, and report PDFs. | Current application writes use SSE-S3 (`AES256`); do not mandate SSE-KMS without code work. Deny public access and restrict the application identity to the clinical-object bucket/prefix. |
| Amazon S3 — model artifacts | Immutable exported model bundle, manifest, checksums, and release provenance. | Keep separate from clinical objects. The application does not load a model directly from S3; a controlled delivery step puts `model.pt` on the mounted volume. |
| Amazon EFS or approved model volume solution | Shared, read-only model volume for API and worker Pods. | The supplied Deployments expect a PVC named `ecg-platform-models`, but no such PVC is included. `k8s/base/pvc.yaml` is for the legacy workbench and must not be reused. |
| AWS Secrets Manager | Holds application secrets, RDS credentials, interim S3 access-key pair, and cache credentials. | The app reads environment variables. A file-only CSI mount alone is insufficient without a wrapper/refactor; use an approved secret-sync path to the Kubernetes Secret referenced by the workloads. |
| IAM / EKS Pod Identity | Least-privilege operational identities. | Use Pod Identity for model delivery, secret retrieval/synchronization, and controllers where possible. The current application S3 adapter still needs explicit access keys until refactored. |
| AWS Load Balancer Controller + ALB | TLS ingress and path routing for the frontend and `/api`. | The supplied Service is `ClusterIP` and there is no Ingress. Install/configure the controller and create a reviewed Ingress separately. |
| Amazon CloudWatch | Container logs, metrics, dashboards, and alarms. | Do not log raw ECG, report text, tokens, passwords, or other PHI. Logging configuration and retention must be governed outside the source repository. |

AWS documentation changes over time. Consult the current official guides for
[EKS setup](https://docs.aws.amazon.com/eks/latest/userguide/setting-up.html),
[AWS Load Balancer Controller](https://docs.aws.amazon.com/eks/latest/userguide/aws-load-balancer-controller.html),
[ECR image push](https://docs.aws.amazon.com/AmazonECR/latest/userguide/getting-started-cli.html),
[EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html),
[Secrets Manager on EKS](https://docs.aws.amazon.com/eks/latest/userguide/manage-secrets.html),
[S3 Block Public Access](https://docs.aws.amazon.com/AmazonS3/latest/userguide/configuring-block-public-access-bucket.html),
and [ElastiCache encryption](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/encryption.html)
before selecting resources or policies.

## 3. Preconditions and accountable decisions

Complete these decisions before creating cloud resources:

1. **Environment and account:** use a dedicated non-production AWS account,
   Region, and naming convention. Do not test this project against production
   accounts or real patient records.
2. **Responsible owners:** identify accountable owners for AWS billing, network,
   database, model release, identity, incident response, backups, security
   review, and clinical/research governance.
3. **Data classification and access approval:** document whether the intended
   test data are synthetic, de-identified under an approved process, or
   prohibited. A technical bucket setting does not create a legal basis to
   process patient data.
4. **Region/residency:** choose an approved AWS Region before data exist. Model
   and data buckets, backups, logs, and disaster-recovery copies may all have
   residency implications.
5. **Network decision:** decide whether the ALB is internal only, internet
   facing behind an approved access gateway, or not exposed at all. Do not
   expose RDS, ElastiCache, S3, the Kubernetes API, or the legacy workbench to
   the public internet.
6. **Identity decision:** decide how users are provisioned and how privileged
   actions are protected. The repository's basic password/JWT mechanism is not
   a replacement for enterprise identity.
7. **Release decision:** require a model manifest, source commit, reviewer,
   checksum, test results, and rollback plan before any model reaches a shared
   environment.
8. **Budget decision:** set AWS Budgets/alerts before creating EKS, NAT
   Gateways, ALBs, RDS, ElastiCache, EFS, logs, or data-transfer paths. These
   services can incur charges while idle.

### Local tooling

Install and authenticate the following through your institution's approved
process:

- AWS CLI v2 and an IAM Identity Center/assumed-role profile; do not use a
  long-lived administrator access key on a developer workstation.
- Docker with Buildx, `kubectl`, `eksctl`, Helm, and a supported `kustomize`
  workflow.
- Python 3.12 project environment for model-export verification and project
  tests; Node/npm for the frontend build.
- A private DNS/ACM certificate plan if HTTPS ingress will be used.

The shell examples below use POSIX syntax. Set equivalent variables in Windows
PowerShell as shown; do not paste secrets into shell history, source control,
terminal recordings, issue trackers, or screenshots.

```sh
# POSIX shell — replace every angle-bracket value.
export AWS_PROFILE="<approved-aws-profile>"
export AWS_REGION="<approved-region>"
export AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
export ENVIRONMENT="research-dev"
export APP_NAME="ecg-health-platform"
export EKS_CLUSTER="${APP_NAME}-${ENVIRONMENT}"
export ECR_REPOSITORY="${APP_NAME}"
export CLINICAL_BUCKET="<globally-unique-private-clinical-bucket>"
export MODEL_BUCKET="<globally-unique-private-model-bucket>"

aws sts get-caller-identity
```

```powershell
# Windows PowerShell equivalent. Do not use this to hold secret values.
$env:AWS_PROFILE = "<approved-aws-profile>"
$env:AWS_REGION = "<approved-region>"
$env:AWS_ACCOUNT_ID = aws sts get-caller-identity --query Account --output text
$env:ENVIRONMENT = "research-dev"
$env:APP_NAME = "ecg-health-platform"
$env:EKS_CLUSTER = "$($env:APP_NAME)-$($env:ENVIRONMENT)"
$env:ECR_REPOSITORY = $env:APP_NAME
$env:CLINICAL_BUCKET = "<globally-unique-private-clinical-bucket>"
$env:MODEL_BUCKET = "<globally-unique-private-model-bucket>"

aws sts get-caller-identity
```

## 4. Network and perimeter design

Create a VPC with private subnets in at least two Availability Zones before
creating stateful services or EKS node groups. A typical research design has:

| Layer | Placement | Allowed connections |
| --- | --- | --- |
| ALB | Public subnets for approved internet-facing access, or private subnets for internal-only access. | HTTPS 443 from approved clients/security perimeter only. Redirect HTTP to HTTPS if HTTP exists at all. |
| Frontend/API/worker nodes and Pods | Private subnets. | ALB to frontend/API only; API/worker to RDS, Redis, S3/Secrets endpoints, CloudWatch, and approved package/registry endpoints. |
| RDS PostgreSQL | Private DB subnet group across AZs; not publicly accessible. | TCP 5432 only from API, worker, and controlled migration sources. |
| ElastiCache | Private cache subnet group. | TLS Redis port only from API and worker security groups. |
| S3 / ECR / Secrets Manager | AWS service endpoints. | Prefer S3 gateway and required interface VPC endpoints where the approved network design avoids broad NAT egress. |

Use security groups as explicit allow lists. Example intent:

```text
approved-client CIDRs / access gateway  -> ALB               : 443
ALB security group                     -> API Pod ENI/target : 8080
ALB security group                     -> frontend target    : 80
API/worker security group              -> RDS                : 5432
API/worker security group              -> ElastiCache        : approved TLS port
```

Restrict egress deliberately. The supplied Kubernetes `NetworkPolicy` limits
only API ingress and relies on a CNI with policy enforcement; it does not supply
the necessary egress policy. Define and test egress for DNS, RDS, Redis, S3,
Secrets Manager, ECR, telemetry, and any approved external identity service.

### TLS, hostnames, and browser routing

- Terminate TLS at an ALB with an ACM certificate for
  `https://<approved-research-host>`. Configure a 443 listener and redirect 80
  to 443 if 80 is exposed.
- Prefer a single origin: ALB route `/` to the frontend and `/api` to the API.
  The React client defaults to `/api/v1`, so it then stays same-origin.
- Set `CORS_ORIGINS=https://<approved-research-host>` and
  `ECG_PLATFORM_TRUSTED_HOSTS=<approved-research-host>,ecg-platform-api` in the
  AWS overlay. Do not retain `localhost` as a production browser origin.
- The `frontend/nginx.conf` currently proxies `/api/` to the Compose-only name
  `backend:8080`. An ALB path rule that sends `/api` directly to the API avoids
  that proxy path. If any request is intended to pass through the frontend
  nginx, replace and test that upstream configuration for the EKS service DNS.
- An alternative is to add a reviewed `ARG`/`ENV VITE_API_BASE_URL` to the
  frontend Dockerfile before its `npm run build`, build with
  `VITE_API_BASE_URL=https://api.<approved-domain>/api/v1`, and expose a
  separate API hostname. The current Dockerfile does not declare that build
  argument. This creates a cross-origin browser flow, so configure the exact
  frontend `CORS_ORIGINS` value, HTTPS/cookie behavior, and ALB routing
  deliberately. The existing frontend image does not automatically provide
  either cloud routing option.
- The app marks refresh cookies `Secure` outside development. HTTPS is
  therefore a functional requirement, not merely an optional hardening step.

An ALB does not add SSO, MFA, tenant isolation, CSRF protections, clinical
authorization, or model validation. Those remain separate controls.

## 5. Build, test, and publish immutable container images

Run tests before a shared release. From the repository root, the minimum
research smoke checks are:

```sh
python -m unittest tests.test_clinical_platform tests.test_export_model -v
cd frontend && npm ci && npm run build
```

Use the project's own test suite as appropriate; passing it does not validate
security, cloud infrastructure, clinical accuracy, or patient-data safety.

Create separate private ECR repositories for the platform and frontend, with
tag immutability and scanning settings approved by the organization. For
example:

```sh
aws ecr create-repository \
  --repository-name "$ECR_REPOSITORY" \
  --image-tag-mutability IMMUTABLE \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=AES256 \
  --region "$AWS_REGION"

aws ecr create-repository \
  --repository-name "${ECR_REPOSITORY}-frontend" \
  --image-tag-mutability IMMUTABLE \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=AES256 \
  --region "$AWS_REGION"
```

Authenticate Docker without placing a password in a file:

```sh
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin \
    "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
```

Build a Linux image matching the EKS node architecture. This matters when a
developer builds on Apple Silicon but the EKS node group uses x86_64. The
following is an example for x86_64 nodes; use `linux/arm64` instead only if the
cluster and image policy intentionally use ARM nodes.

```sh
export RELEASE_TAG="<git-sha-or-approved-version>"
export PLATFORM_IMAGE="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY}:${RELEASE_TAG}"
export FRONTEND_IMAGE="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY}-frontend:${RELEASE_TAG}"

docker buildx build --platform linux/amd64 \
  --file Dockerfile.platform --tag "$PLATFORM_IMAGE" --push .
docker buildx build --platform linux/amd64 \
  --file frontend/Dockerfile --tag "$FRONTEND_IMAGE" --push frontend

aws ecr describe-images --repository-name "$ECR_REPOSITORY" \
  --image-ids imageTag="$RELEASE_TAG" --region "$AWS_REGION"
```

Record the resulting image digest rather than deploying an unreviewed mutable
tag. The base Kubernetes manifest currently uses a placeholder GHCR image, so
an AWS Kustomize overlay must replace it with the reviewed ECR image digest.

## 6. Model release and delivery

### 6.1 Release the right artifact

The clinical API uses the native PyTorch checkpoint only:

```text
AI_MODEL_PATH=/models/model.pt
```

Do **not** deploy raw `MLII/` data, a rendered ECG PNG, an encrypted workbench
batch, `model.ts`, or `model.onnx` as the API model. Export an auditable bundle
from the reviewed checkpoint first:

```sh
ecg-export-model \
  --checkpoint artifacts_final/model.pt \
  --output "exports/<approved-model-version>"

shasum -a 256 "exports/<approved-model-version>/model.pt"
cat "exports/<approved-model-version>/model_manifest.json"
```

On Windows PowerShell, use the equivalent hash command:

```powershell
Get-FileHash "exports\<approved-model-version>\model.pt" -Algorithm SHA256
```

The output bundle contains the native checkpoint, numeric `label_map.json`, a
`model_manifest.json`, and source `metrics.json` when available. Compare the
calculated model hash with `source_checkpoint.sha256` in the manifest. Preserve
the source Git commit, evaluation notes, manifest, reviewer, release date, and
the known fragment-level validation limitation with the model record.

The existing `artifacts_final` baseline records fragment-level metrics, not
patient-level or clinical performance. Its scores are uncalibrated and must not
be presented as disease probabilities or clinical efficacy.

### 6.2 Separate model and clinical-object buckets

Use distinct buckets and different IAM policies:

```text
s3://<private-model-bucket>/ecg-models/<model-version>/
  model.pt
  label_map.json
  metrics.json                  # if present
  model_manifest.json

s3://<private-clinical-bucket>/organizations/<organization-uuid>/...
  ecgs/...                      # uploaded waveform source bytes
  explanations/...              # Grad-CAM PNGs
  reports/...                   # clinician report PDFs
```

The S3 prefix shown for clinical objects matches the current service code. It
is still application data and may be sensitive; do not use object keys as a
source of patient identity or as a public URL path.

Provision a bucket only through approved IaC where possible. These POSIX AWS
CLI examples show the baseline controls, not a complete institutional policy:

```sh
# For regions other than us-east-1, include the LocationConstraint shown.
aws s3api create-bucket --bucket "$CLINICAL_BUCKET" --region "$AWS_REGION" \
  --create-bucket-configuration LocationConstraint="$AWS_REGION"
aws s3api create-bucket --bucket "$MODEL_BUCKET" --region "$AWS_REGION" \
  --create-bucket-configuration LocationConstraint="$AWS_REGION"

for bucket in "$CLINICAL_BUCKET" "$MODEL_BUCKET"; do
  aws s3api put-public-access-block --bucket "$bucket" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws s3api put-bucket-versioning --bucket "$bucket" \
    --versioning-configuration Status=Enabled
  aws s3api put-bucket-encryption --bucket "$bucket" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
done
```

For `us-east-1`, omit `--create-bucket-configuration`; AWS CLI syntax differs
there. Also add region-appropriate lifecycle, access-log/CloudTrail data-event,
backup, deletion, and retention policies through approved infrastructure code.

The clinical-object command intentionally configures **SSE-S3 (`AES256`)** to
match the present Python adapter. A customer-managed KMS requirement is a
valid organizational control, but the adapter currently sends an explicit
`AES256` header and has no `SSE-KMS`/KMS-key setting. Stop and implement/test a
storage-adapter change before enforcing an `aws:kms` bucket policy for app
writes. The separate model-release workflow can adopt stronger controls only
after its delivery tooling is tested with those controls.

Upload only the verified export bundle to the model bucket:

```sh
export MODEL_VERSION="<approved-model-version>"
aws s3 cp "exports/${MODEL_VERSION}/" \
  "s3://${MODEL_BUCKET}/ecg-models/${MODEL_VERSION}/" \
  --recursive --only-show-errors
aws s3 ls "s3://${MODEL_BUCKET}/ecg-models/${MODEL_VERSION}/"
```

### 6.3 Deliver `model.pt` to the Pods

The supplied API and worker manifests mount a PVC named
`ecg-platform-models` at `/models`. They require the final path
`/models/model.pt`. No PVC is supplied under `k8s/platform/`.

Choose one reviewed pattern:

1. **EFS-backed read-only PVC (shared model):** provision Amazon EFS and its
   CSI driver, create a namespace-scoped PV/PVC backed by an EFS access point,
   and use a controlled release Job to copy the verified native `model.pt` to
   the access point. Mount it read-only in both API and worker Pods. Ensure the
   non-root UID/GID 10001 can read it, but Pods cannot write it.
2. **Release-built immutable volume/snapshot:** materialize the verified model
   into an approved read-only volume/image mechanism and mount it as the PVC
   expected by the existing Deployments. Document how the API and worker can
   both read it and how rollback selects an earlier immutable version.
3. **S3 download init container:** add a reviewed overlay with an init
   container that uses a dedicated EKS Pod Identity role to download the model
   bundle, verify the checksum, and place `model.pt` into a shared emptyDir or
   volume before the application container starts. This behavior is **not** in
   the supplied manifests and must be tested for every rollout.

The EKS Pod Identity role used for a model-delivery job can have read-only
access to the one model-bucket version/prefix. That is distinct from the
application's current S3 object-storage implementation, which cannot yet use
Pod Identity credentials directly.

After delivery, verify from a controlled Pod or release job:

```sh
sha256sum /models/model.pt
test -r /models/model.pt
```

Compare that SHA-256 with the signed-off manifest before the API is exposed.
Never mount a writable training/data directory, encryption keys, or developer
home directory into the API or worker Pod.

## 7. Provision private data services

### 7.1 Amazon RDS for PostgreSQL

Create an encrypted, private RDS PostgreSQL instance or approved Multi-AZ
configuration in a DB subnet group spanning private subnets. Use a dedicated
application database/user and a separate, time-bounded migration identity if
your operational model supports it. The current app accepts one `DATABASE_URL`;
it does not itself separate migration permissions from runtime permissions.

An illustrative creation command, to be converted into reviewed IaC and
adapted to supported engine versions and organizational policies, is:

```sh
aws rds create-db-subnet-group \
  --db-subnet-group-name "${APP_NAME}-${ENVIRONMENT}-db" \
  --db-subnet-group-description "Private subnets for ${APP_NAME}" \
  --subnet-ids "<private-subnet-a>" "<private-subnet-b>"

aws rds create-db-instance \
  --db-instance-identifier "${APP_NAME}-${ENVIRONMENT}" \
  --engine postgres \
  --db-instance-class "<approved-instance-class>" \
  --allocated-storage "<approved-gib>" \
  --storage-encrypted \
  --kms-key-id "<approved-rds-kms-key-arn>" \
  --db-subnet-group-name "${APP_NAME}-${ENVIRONMENT}-db" \
  --vpc-security-group-ids "<rds-security-group-id>" \
  --no-publicly-accessible \
  --backup-retention-period "<approved-days>" \
  --multi-az \
  --manage-master-user-password \
  --master-username "<bootstrap-admin-name>"
```

The above is only an example; select engine version, instance/storage class,
maintenance window, backup retention, deletion protection, performance
insights/monitoring, and high-availability design under organizational control.
AWS documents the private-subnet and Multi-AZ prerequisites for PostgreSQL in
its [RDS guide](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/create-multi-az-db-cluster.html).

For the runtime connection string, use the `postgresql+psycopg` SQLAlchemy
scheme expected by the code and an approved TLS policy, for example:

```text
postgresql+psycopg://<application-user>:<encoded-password>@<rds-endpoint>:5432/<database>?sslmode=require
```

`sslmode=require` alone is not the same as full certificate validation. Decide
how the deployment mounts and verifies the current RDS CA bundle, then test it
with the actual psycopg driver. Do not put the URL/password in a ConfigMap,
Docker image, committed `.env`, or command line.

### 7.2 Amazon ElastiCache

Create a private Redis OSS/Valkey-compatible ElastiCache deployment with
encryption in transit, at-rest encryption, authentication/authorization, an
approved backup strategy, and a security group that admits only API and worker
paths. AWS supports TLS and at-rest controls as described in its
[ElastiCache security documentation](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/encryption.html).

Use a non-cluster-mode primary endpoint unless a Redis Cluster-compatible RQ
configuration has been separately proven. The source calls `redis.Redis` and
RQ directly; it does not supply a Redis Cluster client or cache failover test.

Store the complete Redis URL—including any authentication material—in Secrets
Manager, not in the supplied ConfigMap. A typical target form is:

```text
REDIS_URL=rediss://:<cache-auth-token>@<private-primary-endpoint>:6379/0
ASYNC_ANALYSIS=true
```

Validate TLS, certificate handling, auth token rotation, reconnect/failover,
RQ job timeout (currently 900 seconds), result TTL (3600 seconds), and failure
TTL (86400 seconds) with a non-sensitive test record. Redis is a queue/cache;
it is not the system of record and should not receive waveform bytes.

## 8. Secrets Manager, Kubernetes secret delivery, and IAM

### 8.1 Required configuration values

The API/worker need at least these values in a production-style deployment:

| Variable | Source / rule |
| --- | --- |
| `DATABASE_URL` | RDS application connection string; treat as secret. |
| `SECRET_KEY` | Strong distinct Flask secret from Secrets Manager. |
| `JWT_SECRET` | Strong secret distinct from `SECRET_KEY`; rotation strategy is required before shared use. |
| `OBJECT_STORAGE_ACCESS_KEY` | Interim restricted AWS access-key ID for the current S3 adapter; secret. |
| `OBJECT_STORAGE_SECRET_KEY` | Interim restricted AWS secret key for the current S3 adapter; secret. |
| `REDIS_URL` | Secret when it includes ElastiCache authentication material. |
| `OBJECT_STORAGE_BACKEND` | `s3`. |
| `OBJECT_STORAGE_ENDPOINT` | Required by current code even for AWS S3, e.g. `https://s3.<approved-region>.amazonaws.com`; validate with the selected region/bucket. |
| `OBJECT_STORAGE_BUCKET` | Private clinical-object bucket name. |
| `OBJECT_STORAGE_REGION` | Approved AWS Region. |
| `ALLOW_OBJECT_STORAGE_BUCKET_CREATE` | `false` in a shared environment. The app must never create a production bucket at startup. |
| `AI_MODEL_PATH` | Exactly `/models/model.pt`. |
| `AI_MODEL_VERSION` | Approved model-release identifier—not a claim of clinical validation. |
| `CORS_ORIGINS` | Exact approved HTTPS browser origin, no wildcard. |
| `ECG_PLATFORM_TRUSTED_HOSTS` | Exact API/ingress hostnames used by the deployment. |
| `MPLCONFIGDIR` | Writable temporary path such as `/tmp/matplotlib`. |

Create a secret in Secrets Manager through approved IaC or a guarded
administrative procedure. An example name—not a value—is:

```sh
aws secretsmanager create-secret \
  --name "${APP_NAME}/${ENVIRONMENT}/runtime" \
  --description "Runtime configuration for ${APP_NAME} ${ENVIRONMENT}" \
  --kms-key-id "<approved-secrets-manager-kms-key-arn>"
```

Do not include `--secret-string` with real credentials in shared terminals,
shell history, or source control. Supply secret content through an approved
pipeline or an authorized secret-management interface.

### 8.2 Current implementation constraint: static S3 credentials

It is important not to document a capability that the code does not have:

```text
Current S3PrivateStorage requirements:
  OBJECT_STORAGE_ENDPOINT
  OBJECT_STORAGE_BUCKET
  OBJECT_STORAGE_ACCESS_KEY
  OBJECT_STORAGE_SECRET_KEY
```

The adapter constructs boto3 with the explicit access key and secret. It does
not fall back to AWS SDK default credentials, so attaching an EKS Pod Identity
role to the API/worker will **not** currently remove the need for those two
environment variables.

Use the following only as an interim deployment boundary:

1. Create a dedicated IAM user/access key restricted to the single clinical
   bucket and permitted `organizations/*` actions required by the app.
2. Store the access-key pair in Secrets Manager.
3. Use an approved secret synchronization mechanism to materialize the current
   `ecg-platform-secrets` Kubernetes Secret used by `envFrom` in the API and
   worker Deployments.
4. Rotate/revoke the access key under an approved schedule, audit its use, and
   test a restart after rotation.

A file-only AWS Secrets and Configuration Provider (ASCP) / Secrets Store CSI
mount is not sufficient by itself: this application currently reads environment
variables, not secret files. Use a reviewed sync mechanism that creates the
Kubernetes Secret, a safe entrypoint wrapper that exports mounted secret files,
or—preferably—refactor `S3PrivateStorage` to use boto3's default credential
chain and EKS Pod Identity. The ASCP approach and its Pod Identity model are
described in the [AWS EKS Secrets Manager guide](https://docs.aws.amazon.com/eks/latest/userguide/manage-secrets.html).

Before native IAM-only S3 access is claimed, implement and test all of the
following in source:

- allow S3 client construction without explicit access keys so boto3 can use
  EKS Pod Identity/default credentials;
- remove static credentials from environment/Secrets Manager for the app role;
- provision an API/worker Kubernetes service account and a least-privilege Pod
  Identity association;
- test list/head/get/put/presign behavior, rotation, and denial cases; and
- decide whether to add selectable SSE-KMS/KMS-key support rather than the
  current hard-coded `AES256` request.

### 8.3 IAM role separation

Use separate IAM identities rather than one broad cluster role:

| Identity | Minimum purpose | Avoid |
| --- | --- | --- |
| CI image publisher | Push to the two named ECR repositories and read required image metadata. | Account-wide administrator, bucket/database access, or Kubernetes admin rights. |
| EKS cluster/node role | AWS-managed EKS/node operations only. | Application S3/Secrets permissions inherited by every Pod. |
| API/worker future Pod Identity | Clinical object bucket, exact needed prefix/actions, and perhaps secret retrieval after code refactor. | Model-bucket write, ECR push, RDS administrative actions, wildcard S3 access. |
| Model-delivery Job Pod Identity | Read only one approved model-bucket version/prefix. | Clinical object reads/writes or secret-management access. |
| Secret synchronization role | Read only the named runtime secret and KMS decrypt permission if required. | General Secrets Manager list/read or arbitrary KMS decrypt. |
| AWS Load Balancer Controller | Official controller policy limited to the cluster's tagged ALB resources. | Reusing the application role. |
| Migration release role | Apply reviewed schema changes during a controlled release. | Long-lived unrestricted database administration for API Pods. |

EKS Pod Identity associates a role to a Kubernetes service account and provides
temporary credentials to the workload. See the [official Pod Identity guide](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html).
Do not use a node instance role as a shortcut for application access.

The interim static S3 IAM user policy must be restricted to the clinical bucket
and the exact required prefixes. The application calls `HeadBucket`, `PutObject`,
`GetObject`, and can generate presigned GET URLs; scope any needed list or
location permissions narrowly and validate the final policy with negative tests.

## 9. EKS and ingress setup

### 9.1 Cluster prerequisites

Provision EKS with private worker nodes, cluster logging, supported add-ons,
and an approved Kubernetes version. Use an `eksctl` config file or Terraform/
CloudFormation rather than an unreviewed one-line command where network,
private endpoint, node groups, tags, and audit settings matter. AWS supports
creating EKS clusters from configuration as documented in the
[eksctl guide](https://docs.aws.amazon.com/eks/latest/eksctl/creating-and-managing-clusters.html).

The cluster needs, at minimum:

- worker capacity compatible with the platform image architecture;
- a CNI/network-policy design appropriate to the selected EKS mode;
- ECR pull access for nodes;
- an EFS CSI driver or another reviewed model-volume mechanism if using the
  current PVC-based deployment model;
- EKS Pod Identity Agent (or an institution-approved alternative) for
  model-delivery/secrets/controller roles;
- the AWS Load Balancer Controller and its dedicated IAM role for ALB Ingress;
- a policy decision for metrics, autoscaling, disruption budgets, image
  admission, and runtime security; and
- a private cluster-admin access path and audit trail.

Do not expose the Kubernetes API endpoint broadly. `kubectl` access must be
limited to approved administrators and release automation.

### 9.2 AWS overlay work required

Create an environment-specific Kustomize overlay outside the base templates.
At minimum it must:

1. replace the placeholder GHCR platform image with an immutable ECR digest;
2. add a frontend Deployment and Service using the separate ECR frontend image;
3. create/provide `ecg-platform-models` read-only PVC/PV;
4. inject the governed secret/config values described above without committing
   secret values;
5. add API/worker service accounts and any required Pod Identity annotations or
   associations;
6. add a controlled migration Job definition or release-pipeline step;
7. add restrictive ingress **and egress** policies compatible with the EKS CNI;
8. add a TLS ALB Ingress, frontend service routing, and approved hostname; and
9. add resource tuning, HPA/PDB only after load testing, plus observability
   labels/annotations required by the organization.

Do not apply `k8s/platform/secret.example.yaml` with placeholder values. It is
not included in the base Kustomization and must never become a populated Git
file.

### 9.3 ALB Ingress example

After the AWS Load Balancer Controller is installed with its own IAM role, an
overlay can contain a reviewed Ingress shaped like this. It assumes a frontend
Service named `ecg-platform-frontend` exists; that Service is not included by
this repository.

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ecg-platform
  namespace: ecg-clinical-research
  annotations:
    kubernetes.io/ingress.class: alb
    alb.ingress.kubernetes.io/scheme: internal # change only after approved exposure review
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/listen-ports: '[{"HTTPS":443}]'
    alb.ingress.kubernetes.io/certificate-arn: <approved-acm-certificate-arn>
spec:
  rules:
    - host: <approved-research-host>
      http:
        paths:
          - path: /api
            pathType: Prefix
            backend:
              service:
                name: ecg-platform-api
                port:
                  number: 8080
          - path: /
            pathType: Prefix
            backend:
              service:
                name: ecg-platform-frontend
                port:
                  number: 80
```

Add a separate HTTP-to-HTTPS redirect listener/configuration under the current
AWS Load Balancer Controller guidance if port 80 is exposed. Consider WAF,
private connectivity, IP restrictions, and an approved identity gateway before
any internet-facing exposure. The AWS controller manages ALB resources for an
EKS Ingress; review the [official controller documentation](https://docs.aws.amazon.com/eks/latest/userguide/lbc-helm.html)
for its current IAM policy and install sequence.

## 10. Controlled deployment order

Use a release ticket/change record and stop at any failed gate. Do not use this
sequence to bypass governance or to turn on real data processing.

1. **Approve scope and environment.** Confirm research-only status, account,
   Region, dataset/data classification, owners, budget alarms, networking,
   identity, and rollback criteria.
2. **Provision VPC perimeter.** Create subnet groups, routing, VPC endpoints
   as appropriate, security groups, DNS/ACM plan, and CloudTrail/Config/logging
   controls through approved IaC.
3. **Provision ECR and build artifacts.** Run tests, build a matching Linux
   architecture image, scan/approve it, push it to ECR, and capture immutable
   digests.
4. **Release the model.** Export a native bundle, verify model checksum against
   `model_manifest.json`, capture source/evaluation provenance, upload it to
   the separate model bucket, and approve a rollback version.
5. **Provision RDS and ElastiCache.** Use private subnets/security groups,
   encryption, backups, monitoring, and approved credentials. Do not make them
   public. Test connections with non-sensitive data.
6. **Provision clinical-object storage.** Create the separate private bucket,
   public-access block, versioning, matching SSE-S3 behavior, least-privilege
   interim credentials, lifecycle rules, data-event/audit plan, and a tested
   deny policy.
7. **Provision EKS dependencies.** Create/configure the cluster, ECR access,
   CNI/policy enforcement, model-volume driver, Pod Identity agent, secret-sync
   path, ALB controller, and observability components. Each add-on needs its
   own reviewed IAM role.
8. **Materialize the model volume.** Use the chosen controlled delivery
   pattern, confirm `/models/model.pt` exists, is readable by UID 10001, and
   matches the approved SHA-256. Keep it read-only to API and worker Pods.
9. **Create an AWS-specific overlay.** Set production config, ECR digest,
   frontend, model claim, service accounts, secret references, ALB Ingress, and
   network policies. Render it and inspect it before applying.
10. **Run the migration once.** With the migration identity/configuration,
    run `ecg-health-migrate` as a controlled Job/release action. It applies
    Alembic revisions and reference rows only; it intentionally creates no
    hospital, user, patient, or sample record.
11. **Deploy backend and worker.** Roll out API and worker only after migration
    succeeds. Confirm Pods run as non-root with a read-only root filesystem as
    declared by the source manifests.
12. **Deploy frontend and ingress.** Verify `/api/v1` route preservation, TLS,
    cookie behavior, CORS/host configuration, and no accidental public bucket
    or database/cache exposure.
13. **Run post-deploy verification.** Use synthetic/non-sensitive records only;
    validate authentication, tenant isolation, upload/inference/Grad-CAM/report
    workflow, queue behavior, audit records, download authorization, alarms,
    rollback, and backup/restore evidence.
14. **Observe before expanding use.** Review costs, logs, vulnerabilities,
    security findings, request errors, cache/RDS behavior, and model release
    provenance. A successful deployment does not authorize clinical use.

## 11. Configuration and migration checks

Before applying an overlay, render it locally and ensure placeholders are gone
without exposing values:

```sh
kubectl kustomize <path-to-reviewed-aws-overlay>
kubectl diff -k <path-to-reviewed-aws-overlay>
```

After the deployment, use namespace-scoped checks:

```sh
kubectl -n ecg-clinical-research get deploy,pods,svc,ingress
kubectl -n ecg-clinical-research rollout status deployment/ecg-platform-api
kubectl -n ecg-clinical-research rollout status deployment/ecg-platform-worker
kubectl -n ecg-clinical-research describe pod <api-pod-name>
kubectl -n ecg-clinical-research logs deployment/ecg-platform-api --tail=200
```

The supplied `/readyz` reports ready when the configured model path exists; it
does not load the model. Follow it with an authorized, synthetic workflow test
that proves model loading and the required dependencies without using real
clinical data.

For a temporary private verification path, use a controlled port forward only
from an approved administrator workstation:

```sh
kubectl -n ecg-clinical-research port-forward service/ecg-platform-api 8080:8080
curl --fail --silent http://127.0.0.1:8080/healthz
curl --fail --silent http://127.0.0.1:8080/readyz
```

Do not confuse a 200 response from these endpoints with a complete production
readiness check. Record the test account provenance and remove test access when
the controlled verification is complete.

## 12. CloudWatch, audit, backups, and operating signals

The application writes Gunicorn/API/worker output to standard streams in the
container image. Configure an approved EKS logging path to CloudWatch Logs and
set explicit retention, encryption, cross-account access, and redaction rules.
The repository does not configure a log router, CloudWatch dashboard, SIEM,
centralized immutable audit store, or alerting destination for you.

### Minimum signals to design and test

| Signal | Example observation | Why it matters |
| --- | --- | --- |
| ALB | Target health, HTTP 4xx/5xx, latency, rejected connections. | Detects route/service failures, not clinical correctness. |
| EKS/API/worker | Pod restarts, pending Pods, OOM kills, CPU/memory, failed jobs, worker queue backlog. | Detects capacity or application instability. |
| RDS | Connection count, CPU, free storage, failover events, backup success/failure, slow queries under approved monitoring. | Detects database availability/capacity issues. |
| ElastiCache | Evictions, memory pressure, replication/failover events, auth/TLS errors, queue depth. | Detects queued analysis delivery problems. |
| S3 | Access-denied/error trends, storage growth, unexpected public-access/config changes, CloudTrail data events if approved. | Detects object-storage control or cost issues. |
| Secrets / IAM | Secret retrieval failures, access-key use/rotation, denied calls, unexpected role assumptions. | Detects broken deployment or misuse. |
| Model release | Image digest, model SHA-256, `AI_MODEL_VERSION`, inference failures, latency distribution. | Makes a deployed analysis traceable to its reviewed artifact. |

Create alarms with named owners and runbooks—not only email addresses. Example
alarm classes include sustained ALB 5xx, zero healthy API targets, API/worker
CrashLoopBackOff, RDS low storage/high connections, Redis memory pressure,
secret retrieval failures, backup failures, and unexpected S3 access denials.

Application audit rows are not an independent tamper-proof audit system. The
source explicitly warns callers not to put raw ECG/PHI into audit metadata.
Treat all log/audit exports as potentially sensitive until reviewed, and do not
ship bearer tokens, passwords, raw waveforms, clinical note text, or raw stack
traces to general-purpose debug channels.

## 13. Security, privacy, and research limits

### Required design controls before sensitive data

- private RDS/Redis endpoints; no public database/cache IP;
- TLS at the ingress and validated internal transport design;
- least-privilege IAM, separation of deployment/controller/application roles,
  and rotation/audit for the interim S3 static access key;
- S3 public-access block, versioning, restrictive bucket policy, lifecycle,
  deletion/retention policy, and tested object-access paths;
- Secrets Manager backed by approved KMS/access policy and a non-Git secret
  delivery mechanism;
- immutable model/image release provenance and a tested rollback procedure;
- VPC and Kubernetes network policies with tested egress restrictions;
- image dependency/vulnerability scanning, patching, runtime hardening, and
  an approved container admission process;
- encrypted RDS backups, restore exercise, disaster-recovery objective, and
  incident-response process;
- centralized monitoring/alerting that avoids PHI and has retention/access
  controls; and
- independent security and clinical-safety review, including tenant-isolation,
  authorization, upload/parser, presigned URL, and adversarial tests.

### Explicitly not solved by this runbook

- clinical validation, calibration, drift monitoring, outcome evaluation, or
  patient-/recording-disjoint external validation;
- treatment recommendation, prescription, diagnosis, or emergency triage;
- institutional consent, retention/deletion, legal basis, data residency,
  HIPAA/DPDP/GDPR/other compliance, contractual or vendor review;
- SSO, MFA, password reset, step-up auth, account lockout, token-key rotation,
  full CSRF design, or enterprise session governance;
- malware scanning/content disarm, DLP, signed device upload, EHR integration,
  FHIR server conformance, or a full SIEM;
- transparent use of SSE-KMS for app-written clinical S3 objects while the
  current adapter forces SSE-S3; and
- direct S3 access through Pod Identity for the current application code.

No model score, Grad-CAM visualization, successful encryption/camouflage demo,
or successful AWS health check is a clinical conclusion. The model consumes a
single numerical 3,600-sample waveform, not a screenshot; it needs qualified
clinician review within an approved workflow.

## 14. Cost controls

Start in a dedicated research account with a written budget. Cost drivers often
include EKS control-plane/node capacity, NAT Gateways/data processing, ALB
hours/LCUs, RDS instance/storage/backups, ElastiCache nodes, EFS throughput,
CloudWatch ingestion/retention, S3 storage/requests, VPC endpoints, and data
transfer. Prices and free-tier eligibility are regional and change over time;
consult current AWS pricing before provisioning.

Set AWS Budgets and Cost Anomaly Detection alerts before deployment. Apply
resource tags such as these through IaC:

```text
application = ecg-health-platform
environment = research-dev
owner       = <team-or-cost-center>
data-classification = <approved-classification>
model-version = <approved-model-version>
```

Choose non-production capacity only after a workload test with synthetic data.
Do not disable encryption, backups, logging, or access controls merely to lower
cost. Conversely, do not create always-on multi-AZ/high-capacity services for a
short research demonstration without a budget owner and teardown date.

## 15. Troubleshooting guide

| Symptom | Likely area | Safe investigation |
| --- | --- | --- |
| API Pod is not ready | Model path/volume, image, permissions. | Confirm `/models/model.pt` exists in the Pod, is readable by UID 10001, and matches the approved SHA-256. Remember `/readyz` checks file existence only. |
| API starts but analysis fails | Model load, model contract, storage, database. | Inspect authorized API logs/request ID; verify native `model.pt`, 17-class label metadata, valid 3,600-sample MAT/CSV test input, and configuration. Do not paste waveform bytes into tickets/logs. |
| S3 startup fails with missing endpoint/keys | Current adapter constraint. | Confirm the required endpoint, bucket, access key, and secret were injected into the Pod from the approved secret path. Check least-privilege policy and bucket region. Do not add credentials to a ConfigMap. |
| `AccessDenied` on `PutObject` | Bucket/IAM/encryption policy. | Verify bucket, object prefix, static key policy, and that the current app requests `x-amz-server-side-encryption: AES256`. If the bucket mandates KMS, stop and implement/test adapter support; do not weaken policies without approval. |
| Model PVC stays pending | Missing AWS overlay storage definition. | The platform does not include `ecg-platform-models` PVC/PV. Check EFS/CSI or chosen storage class, access point permissions, AZ/network path, and mount policy. Do not reuse `k8s/base/ecg-workbench-data`. |
| Worker cannot connect to Redis | ElastiCache networking/TLS/auth/client compatibility. | Confirm private security group path, `rediss://` URL, auth/token delivery, TLS validation, non-cluster-mode endpoint, and RQ/redis-py compatibility. Test with synthetic analysis only. |
| Login fails after deployment | HTTPS/CORS/host/cookie/seed account. | Verify same-origin ALB routing, exact `CORS_ORIGINS`, trusted hosts, TLS, `Secure` refresh-cookie behavior, and approved user provisioning. Do not use the development seeding tool to create real users. |
| Browser `/api` requests hit nginx `backend:8080` error | Frontend routing. | Ensure ALB routes `/api` directly to `ecg-platform-api`; otherwise build/test an nginx config whose upstream is the EKS service DNS. |
| ALB has unhealthy targets | Ingress/service/target ports, health endpoint, security group. | Check Ingress events, target group port/path, Service selectors, Pod readiness, and ALB-to-Pod security rules. `/healthz` is process-only; investigate dependency checks separately. |
| Database errors after image rollout | Migration/release order/credentials. | Stop rollout, inspect controlled migration Job/logs, verify Alembic revision and RDS connectivity. Do not let API startup silently substitute SQLite in a shared environment. |
| Presigned download is unexpectedly usable | S3 policy/URL expiry/authorization. | Review the API's authorization path, presigned URL expiration, bucket policy, and CloudTrail events. A presigned URL is a bearer capability until it expires. |
| Costs grow unexpectedly | NAT, logs, EKS, RDS, cache, EFS, S3/data transfer. | Use cost allocation tags and Cost Explorer/Budgets; identify the service first, then make a reviewed capacity/lifecycle change. Do not delete research data or backups outside retention policy. |

## 16. Release acceptance checklist

Do not mark an AWS deployment ready until each applicable item has evidence:

- [ ] Dedicated approved AWS account/Region, environment owner, data-classification decision, and budget alerts exist.
- [ ] VPC/subnets/security groups/endpoints/TLS/DNS and administrative access are reviewed.
- [ ] ECR images use approved immutable digests and matching node architecture; build/test/scan evidence is recorded.
- [ ] Native `model.pt` hash matches `model_manifest.json`; model release provenance, limitations, reviewer, and rollback version are recorded.
- [ ] The model bucket and clinical-object bucket are distinct, private, versioned, non-public, and have approved lifecycle/audit controls.
- [ ] The current SSE-S3/static-credential limitations are explicitly accepted as interim or the code refactor has been completed and tested.
- [ ] RDS and ElastiCache are private, encrypted as approved, backed up, monitored, and reachable only through least-privilege paths.
- [ ] Secrets are in Secrets Manager and delivered without Git, ConfigMap plaintext, image layers, terminal history, or broad node-role access.
- [ ] `ecg-platform-models` is a reviewed read-only model volume with `/models/model.pt` readable by the API/worker and checksum-verified.
- [ ] Migration ran once successfully before API/worker rollout; no real users or data were created by the migration step.
- [ ] API, worker, frontend, ALB Ingress, CORS/host configuration, and network egress controls were rendered/reviewed and tested with synthetic data.
- [ ] Monitoring, alert routing, log retention/redaction, backup/restore, secret rotation, image/model rollback, and incident runbooks have owners.
- [ ] Tenant/RBAC/authorized-download tests and negative access tests passed in the target environment.
- [ ] A qualified governance process has confirmed that research use—not autonomous clinical decision making—is the only permitted scope.

Only after this checklist is complete should the team decide whether the
environment is usable for its specifically approved research purpose. It does
not make the platform suitable for real clinical care by itself.
