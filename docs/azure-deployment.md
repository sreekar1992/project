# Azure deployment runbook — ECG health platform

This is a deployment design and operator runbook for the **authenticated ECG
health platform** in this repository. It is deliberately separate from the
legacy `ecg-gui` workbench. The workbench is a localhost research tool and is
not an Azure deployment target.

> **Status and safety boundary**
>
> No Azure resource, Azure subscription, AKS cluster, model registry, private
> endpoint, or production deployment has been created or validated by this
> repository. The Kubernetes files in `k8s/platform/` are unvalidated
> scaffolding, not an Azure-ready release. This document is a plan to adapt and
> verify them; it is not evidence of HIPAA, DPDP, GDPR, ISO, SOC 2, clinical,
> medical-device, security, or privacy compliance.
>
> The RAMNV2 result is research/clinician-review support only. It is not a
> diagnosis, calibrated disease probability, triage system, treatment
> recommender, prescription system, or emergency-use service. Do not upload
> patient data until the institution has completed governance, security,
> privacy, clinical-safety, legal, and validation approvals.

Read this together with [architecture.md](architecture.md),
[security.md](security.md), [deployment.md](deployment.md),
[ecg-ai-integration.md](ecg-ai-integration.md), and the cross-platform
[model-export-cross-platform.md](model-export-cross-platform.md) guide.

## 1. What is being deployed

The supported deployable application is the Flask health-platform API and its
RQ worker. They use the repository's real, native PyTorch checkpoint and must
remain separate from model training and dataset-wide encryption/camouflage
experiments.

| Repository component | Azure target | Current state and deployment implication |
| --- | --- | --- |
| `Dockerfile.platform` | API and RQ-worker image in Azure Container Registry (ACR) | CPU-oriented Python 3.12 image. Build and publish an immutable digest. |
| `frontend/Dockerfile` | Frontend image in ACR, served behind a selected Azure ingress path | Its checked-in nginx proxy names the Compose service `backend`; it will **not** route to the AKS Service automatically. Rebuild/configure it for an Azure reverse proxy, or add and review a Docker build `ARG`/`ENV` for an exact `VITE_API_BASE_URL=https://<approved-api-host>/api/v1` before `npm run build`, then configure direct CORS deliberately. |
| `k8s/platform/` | Starting point for an AKS API/worker overlay | It has API/worker deployments, Service, ConfigMap, Secret example and an ingress-only NetworkPolicy. It does **not** provision ingress, frontend, migration Job, Azure identities, secrets, model delivery, databases, Redis, object storage, or egress policy. |
| `artifacts_final/model.pt` or exported bundle `model.pt` | Verified native model artifact delivered read-only to API and worker | The application accepts **only native PyTorch `model.pt`** at `AI_MODEL_PATH`; it cannot use `model.ts` or `model.onnx` directly. |
| PostgreSQL | Azure Database for PostgreSQL Flexible Server | Use a private connection and an explicitly run Alembic migration. SQLite is development-only. |
| Redis/RQ | Azure Managed Redis (preferred) or an approved compatible Redis service | Worker jobs receive identifiers only; they must still have private network access to Redis, PostgreSQL, model, and object storage. |
| Clinical waveform, explanation, and report objects | Azure Blob Storage is the desired native target | **Not supported by the current application without code changes.** See the mandatory storage decision in section 4. |
| Secrets | Azure Key Vault plus AKS workload identity / Secrets Store CSI Driver | The current app reads environment variables; it does not natively call Key Vault. A controlled secret-sync or application change is required. |

The model accepts numerical `.mat` or `.csv` waveforms under a fixed
single-lead MLII-style contract: 3,600 finite samples (10 seconds at 360 Hz),
then 0.5–45 Hz filtering, median/MAD normalization, and fourfold decimation to
a float32 tensor shaped `(batch, 1, 900)`. ECG screenshots, Grad-CAM PNGs, and
encrypted-byte images are not model inputs.

## 2. Reference Azure architecture

Choose **one approved internet/enterprise edge design** rather than exposing an
AKS `LoadBalancer` service or the Flask port directly. The API Service should
remain `ClusterIP`, and the RQ worker must not have an ingress route.

```text
Approved users / institutional network
                |
                | HTTPS, institutional identity, WAF/rate limiting
                v
  Option A: Application Gateway WAF v2 / AGIC or Application Gateway for Containers
  Option B: Azure Front Door Premium + WAF -> validated private origin
                |
                v
        Frontend deployment / reverse proxy
                |
                | /api/v1 only, same approved origin where feasible
                v
       AKS ClusterIP Service: ecg-platform-api
                |
       +--------+-------------------+-------------------+
       |        |                   |                   |
       v        v                   v                   v
 API pods    RQ worker       PostgreSQL Flexible   Azure Managed Redis
 (read-only  (no public      Server, private        private endpoint
 model)       ingress)       access / private DNS
       |
       +-- approved clinical-object storage path
       |      (native Azure Blob adapter required before Blob use)
       |
       +-- verified model delivery path from private Blob container
              to a read-only `/models/model.pt` volume

Cross-cutting: ACR, Key Vault, Microsoft Entra workload identity,
private DNS/private endpoints, Azure Monitor/Log Analytics, backup and
retention controls, Azure Policy, and an approved artifact/release process.
```

### Edge choices

Use a private institutional/VPN-only pilot until authentication and governance
have been tested. For HTTP(S) traffic, Azure documents Application Gateway for
Containers as a recommended AKS application-networking option; Application
Gateway WAF v2 with AGIC is also a viable existing-Ingress pattern. Azure Front
Door Premium is a separate global edge option and needs a verified
Private-Link-capable origin design. It is not automatically safer merely
because it is added in front of the cluster.

| Option | Suitable when | Required checks |
| --- | --- | --- |
| **Application Gateway WAF v2 with AGIC** | A regional application needs L7 routing, WAF, TLS policy, and a controlled AKS ingress. | Confirm the ingress class, backend health probes, WAF exclusions, approved listener hosts, certificate lifecycle, and that AGIC does not overwrite unrelated gateway configuration. |
| **Application Gateway for Containers / Gateway API** | A new AKS architecture can adopt the Azure-managed gateway direction. | Confirm regional availability, supported features, Gateway API policy, and the exact controller/CRDs before implementation. |
| **Azure Front Door Premium + WAF** | A globally distributed, internet-facing research service has an approved private-origin design. | Verify the exact origin type, Private Link support, WAF policy, custom domain/certificates, data residency, logging, and end-to-end TLS design. Do not assume direct private connectivity to an arbitrary AKS Service. |

Do not deploy a separate unauthenticated public frontend that calls a public API
from arbitrary origins. Prefer one approved HTTPS origin with `/api` routed to
the API, then configure `CORS_ORIGINS` to that exact origin. If distinct
origins are unavoidable, enumerate only exact HTTPS origins—never `*`.

## 3. Preflight, ownership, and deployment gates

Before creating cloud resources, record accountable owners and receive an
explicit go/no-go decision for every item below.

| Gate | Minimum decision or evidence |
| --- | --- |
| Research scope | Approved use case, intended user population, access route, and statement that outputs are research/clinician-review only. |
| Data classification | Whether any data are synthetic, de-identified, or patient information; permitted Azure region; retention, deletion, export, and incident requirements. |
| Identity | Institutional SSO/MFA/OIDC approach. The source currently has local HS256 JWT/password authentication only and no SSO, MFA, key rotation, password reset, CSRF design, or account lockout flow. |
| Network | VNet address plan, hub/spoke or equivalent, DNS ownership, private endpoints, egress allow-list, VPN/ExpressRoute requirement, and edge choice. |
| Platform | Subscription, resource group separation, Azure Policy, tags, RBAC, break-glass procedure, support plan, cost centre, and an infrastructure-as-code review process. |
| Model | Approved native `model.pt`, SHA-256, exporter `model_manifest.json`, source Git revision, dataset provenance, preprocessing contract, limitations, rollback artifact, and smoke-test evidence. |
| Database | Backup/PITR settings, migration owner, restore test, least-privilege role, data-retention plan, and tested connection from AKS. |
| Security operations | Key Vault owner, rotation schedule, vulnerability/image scanning, logging destination, alert owner, audit review, and incident response procedure. |
| Clinical safety | Independent validation plan, intended-use statement, model monitoring/rollback plan, clinician review workflow, and prohibition on treatment automation. |

Do not use `ecg-health-seed`, `*.example.test` accounts, the public
camouflage password, MLII training data, local SQLite files, or local MinIO
files as a production identity/data bootstrap path.

### Operator tools

Use a recent Azure CLI, `kubectl`, Docker Buildx or ACR Tasks, Git, and a
compatible `az` extension set. The commands in this document are **illustrative
placeholders**, not a copy-and-run production template. Pin a reviewed Azure
CLI version and implement the final design using Bicep, Terraform, or another
institution-approved infrastructure-as-code system.

The same Azure CLI commands work from macOS Terminal and Windows PowerShell.
Set variables with the syntax for your shell; do not put real secrets in shell
history or a tracked file.

#### macOS / Linux shell placeholders

```bash
export SUBSCRIPTION_ID="<subscription-id>"
export LOCATION="<approved-azure-region>"
export RG="<resource-group>"
export ACR_NAME="<globally-unique-acr-name>"
export AKS_NAME="<aks-cluster-name>"
export KEYVAULT_NAME="<globally-unique-key-vault-name>"
export STORAGE_ACCOUNT="<globally-unique-storage-account>"
export POSTGRES_NAME="<postgres-flexible-server-name>"
export REDIS_NAME="<managed-redis-name>"
export IMAGE_REPOSITORY="ecg-health-platform"
export IMAGE_TAG="<immutable-git-sha-or-release-tag>"

az login
az account set --subscription "$SUBSCRIPTION_ID"
az account show --output table
```

#### Windows PowerShell placeholders

```powershell
$env:SUBSCRIPTION_ID = "<subscription-id>"
$env:LOCATION = "<approved-azure-region>"
$env:RG = "<resource-group>"
$env:ACR_NAME = "<globally-unique-acr-name>"
$env:AKS_NAME = "<aks-cluster-name>"
$env:KEYVAULT_NAME = "<globally-unique-key-vault-name>"
$env:STORAGE_ACCOUNT = "<globally-unique-storage-account>"
$env:POSTGRES_NAME = "<postgres-flexible-server-name>"
$env:REDIS_NAME = "<managed-redis-name>"
$env:IMAGE_REPOSITORY = "ecg-health-platform"
$env:IMAGE_TAG = "<immutable-git-sha-or-release-tag>"

az login
az account set --subscription $env:SUBSCRIPTION_ID
az account show --output table
```

The multi-line resource examples below use Bash `$VARIABLE` syntax and `\`
continuations to keep them readable. In PowerShell, use `$env:VARIABLE` and a
backtick continuation, or put the Azure CLI command on one line. For example:

```powershell
az group create --name $env:RG --location $env:LOCATION
az acr build --registry $env:ACR_NAME --image "$($env:IMAGE_REPOSITORY):$($env:IMAGE_TAG)" --file Dockerfile.platform .
```

## 4. Mandatory application-compatibility decisions

### 4.1 Azure Blob Storage is not a direct current runtime backend

This is the most important implementation boundary in an Azure design.

`src/ecg_cvd/clinical/storage.py` accepts only `local` and `s3` backends. The
current `S3PrivateStorage` constructor requires all of the following:

- `OBJECT_STORAGE_ENDPOINT`;
- `OBJECT_STORAGE_BUCKET`;
- `OBJECT_STORAGE_ACCESS_KEY`; and
- `OBJECT_STORAGE_SECRET_KEY`.

It uses Boto3/S3 calls and asks for S3-style `ServerSideEncryption="AES256"`.
It has **no** Azure Blob SDK adapter, Blob URI support, Microsoft Entra
credential flow, managed-identity flow, customer-managed-key integration, or
Azure Storage connection-string handling. Therefore, do **not** set
`OBJECT_STORAGE_ENDPOINT` to `https://<account>.blob.core.windows.net` and
claim that Blob Storage is working. It is not a supported configuration.

Choose and document one of these paths before enabling upload, analysis,
explanation, report, or download routes:

| Path | Use | Current status |
| --- | --- | --- |
| **A. Implement a native Azure Blob adapter** | Preferred for an Azure-native deployment. Add an `azure_blob` backend using `azure-storage-blob`, private Blob containers, `DefaultAzureCredential`/workload identity, integrity checks, explicit content type, no overwrites, and bounded/authorized download behavior. | **Required code, tests, threat model, and integration validation; not included today.** |
| **B. Approved S3-compatible private object store** | A temporary compatibility route while the application remains S3-only. It still requires a private endpoint, least-privilege static credentials, Key Vault delivery, private bucket, encryption/backup policy, and testing. | Compatible in principle only if the service genuinely supports the S3 API used by Boto3. It is not an Azure Blob configuration. |
| **C. Local/MinIO storage** | Local development or synthetic demonstration only. | Not a multi-replica Azure clinical-object design. Do not use it as a production Azure substitute. |

For Path A, the engineering acceptance criteria should include at least:

1. Add and unit-test `OBJECT_STORAGE_BACKEND=azure_blob`; reject unknown values.
2. Authenticate with AKS workload identity rather than account keys in pods.
3. Use a private Blob endpoint, container-level public access disabled, TLS,
   least-privilege data-plane roles, and a separate container/account boundary
   decided by data classification.
4. Preserve the current object-key validation, immutable/no-overwrite behavior,
   SHA-256 calculation/checking, content types, authorization before download,
   and short-lived download behavior.
5. Test `put`, `get`, download/authorization, collision, tampering/hash
   failure, timeout/retry behavior, connection loss, and private-DNS resolution
   against a non-production Azure account.
6. Decide whether provider encryption, customer-managed keys, immutable/blob
   versioning, soft-delete, backup, retention, legal hold, and audit logs meet
   the approved data policy. Those are governance choices, not defaults made by
   this application.

### 4.2 Native model delivery is a separate Blob use case

Azure Blob Storage can be used as a private **artifact source** for the native
model even before the clinical-object adapter exists, but the model-delivery
mechanism must still be built and verified. The API and worker both require a
regular read-only file at `/models/model.pt`; the current manifests assume a
PVC named `ecg-platform-models`. They do not download from Blob, validate a
manifest, or use a Blob CSI volume.

Choose one reviewed implementation:

- **Controlled PVC promotion:** an approved release process verifies the
  bundle, then populates a read-only model volume with exactly `model.pt`.
- **Blob CSI or an init-container pull:** implement a read-only mount or an
  init container that uses a managed identity to fetch a versioned Blob into an
  `emptyDir`, verifies SHA-256 against the approved manifest, changes no model
  in place, and exposes the verified file read-only to the API and worker.
- **A dedicated model registry/delivery service:** add only after its
  compatibility, identity, provenance, and rollback behavior are specified.

Do not bake `MLII/`, `artifacts*/`, checkpoint files, data, passwords, or
encryption keys into the platform image. The repository `.dockerignore`
intentionally excludes those paths.

The base platform manifests request two API replicas and one worker, all of
which mount the same `ecg-platform-models` claim. A normal single-node
ReadWriteOnce disk cannot be assumed to support those simultaneous mounts on
different AKS nodes, especially during rollouts. Select and test a storage or
init-container pattern that provides the required multi-node read behavior and
never treats a writable shared model file as the release mechanism.

### 4.3 Key Vault and managed identity need an integration layer

AKS Microsoft Entra Workload ID can give a Kubernetes service account a
federated identity for Azure resources such as Key Vault. The Key Vault
Secrets Store CSI Driver can mount secrets and can synchronize them into a
Kubernetes Secret. However, the application currently reads its configuration
from environment variables and the supplied deployments use `envFrom` with a
Kubernetes Secret.

Until the application is changed to use Azure SDK credentials directly, the
safe implementation pattern is:

1. Make Key Vault the authoritative secret store.
2. Use workload identity and the CSI driver to read only the required secrets.
3. If the app still needs environment variables, use a reviewed secret-sync
   pattern to create/update the namespace-scoped Kubernetes Secret consumed by
   `envFrom`; understand that this deliberately materializes a copy in the
   cluster.
4. Restrict who can read that Kubernetes Secret, rotate it, roll pods safely,
   and audit both Key Vault and Kubernetes access.

Do not claim that mounting Key Vault files automatically makes them environment
variables. It does not. Do not grant cluster-wide Key Vault access to every
workload identity.

## 5. Network and resource foundation

Create infrastructure with reviewed IaC. The snippets below explain ordering;
they do not replace a production Bicep/Terraform module or an organization’s
landing-zone standards.

### 5.1 Resource groups, tags, and providers

Separate at least non-production from approved research/production-like
environments. Use resource locks, budgets, diagnostic settings, and required
tags such as `environment`, `system`, `owner`, `data-classification`,
`cost-centre`, and `managed-by`.

```bash
# Illustrative only; pick an approved region and names.
az group create --name "$RG" --location "$LOCATION"

# Register only the resource providers required by the approved design.
az provider register --namespace Microsoft.ContainerService
az provider register --namespace Microsoft.ContainerRegistry
az provider register --namespace Microsoft.DBforPostgreSQL
az provider register --namespace Microsoft.Cache
az provider register --namespace Microsoft.Storage
az provider register --namespace Microsoft.KeyVault
az provider register --namespace Microsoft.Network
az provider register --namespace Microsoft.OperationalInsights
```

Use least privilege. A person who can change network rules, write an image,
approve a model, read database records, and read secrets should not be the only
control point for a sensitive deployment.

### 5.2 VNet, subnets, private DNS, and private endpoints

Plan non-overlapping CIDRs before provisioning. Common segmentation is:

| Subnet/purpose | Examples of resources | Notes |
| --- | --- | --- |
| AKS node/pod network | API, worker, frontend workloads | Use the CNI/network model selected for AKS; size for node, pod, upgrade, and private-endpoint demand. |
| Gateway subnet | Application Gateway, where used | Follow Azure’s dedicated-subnet requirements. |
| PostgreSQL delegated subnet | PostgreSQL Flexible Server with private VNet integration | It must be dedicated/delegated for the Flexible Server model. |
| Private-endpoint subnet | Storage, Key Vault, Redis, PostgreSQL when using Private Link | Set network policies/DNS according to the approved Azure pattern. |
| Build/admin path | Bastion/CI agent/VPN/ExpressRoute, if required | Avoid public SSH/RDP just to administer the cluster. |

Private networking is more than a private endpoint. Create and link the
appropriate Private DNS zones, then verify that clients **inside the intended
VNet** resolve service host names to the intended private addresses. Azure
Database for PostgreSQL Flexible Server private access requires Private DNS
planning. Azure Storage, Key Vault, and Redis private endpoints also need
correct DNS linkage. Do not hard-code Azure service IP addresses—use the
service FQDNs.

Recommended network posture after validation:

- AKS private cluster or tightly controlled API access;
- public network access disabled for PostgreSQL, Redis, Storage, and Key Vault
  when the approved design supports private endpoints;
- only the approved edge may accept external HTTPS;
- private API service with no public Kubernetes `LoadBalancer` service;
- default-deny ingress and egress NetworkPolicies, then explicit allowances
  for DNS, edge-to-frontend/API, API/worker-to-PostgreSQL/Redis/object storage,
  model delivery, monitoring, and required identity endpoints;
- egress control/firewall policy that is tested with package/image/model update
  paths, not merely assumed.

The current `k8s/platform/networkpolicy.yaml` restricts **only API ingress**
and currently allows all pods in that namespace. It provides no egress policy,
no worker policy, no DNS rule, no frontend source selector, and no cross-
namespace/edge policy. Replace it with tested policies appropriate to the
selected Azure CNI/NetworkPolicy implementation before exposure.

## 6. Create Azure services

### 6.1 Azure Container Registry

Use an ACR SKU, image-retention policy, geo-replication approach, private
network setup, and vulnerability scanning process appropriate to the approved
environment. Do not use mutable `latest` tags in workload manifests.

```bash
# Illustrative only. ACR names are globally unique and have naming restrictions.
az acr create \
  --resource-group "$RG" \
  --name "$ACR_NAME" \
  --sku Premium

# Later, give the AKS kubelet identity pull access through the supported AKS/ACR path.
# For an existing cluster, review this command and the registry permission model first.
az aks update \
  --resource-group "$RG" \
  --name "$AKS_NAME" \
  --attach-acr "$ACR_NAME"
```

`--attach-acr` grants the appropriate pull role in common AKS/ACR setups. If
the registry uses Azure ABAC repository permissions, use the documented
repository-reader role path rather than assuming `--attach-acr` applies.

Build both images separately. The platform Dockerfile intentionally excludes
the checkpoint and dataset from its build context.

```bash
# Run only from a clean, reviewed source revision. This is an example ACR Task build.
az acr build \
  --registry "$ACR_NAME" \
  --image "$IMAGE_REPOSITORY:$IMAGE_TAG" \
  --file Dockerfile.platform \
  .

az acr build \
  --registry "$ACR_NAME" \
  --image "ecg-health-frontend:$IMAGE_TAG" \
  --file frontend/Dockerfile \
  frontend
```

Capture and approve the **image digest**, not only the tag:

```bash
az acr repository show-manifests \
  --name "$ACR_NAME" \
  --repository "$IMAGE_REPOSITORY" \
  --orderby time_desc \
  --output table
```

Before promoting an image, run the repository test suite, frontend build,
dependency/image vulnerability scans, and a non-sensitive integration test.
Sign or attest artifacts if the organization’s supply-chain policy requires it.

### 6.2 AKS

Create AKS only after networking and the container registry are ready. Enable
OIDC issuer and Microsoft Entra Workload ID at creation time or according to
the reviewed existing-cluster migration procedure. Choose node OS, Kubernetes
version/support window, autoscaling, maintenance windows, availability zones,
upgrade strategy, and system/user node-pool separation through IaC.

```bash
# Illustrative skeleton; add approved networking, identity, monitoring,
# availability, autoscaling, and policy flags in reviewed IaC.
az aks create \
  --resource-group "$RG" \
  --name "$AKS_NAME" \
  --location "$LOCATION" \
  --node-count 2 \
  --generate-ssh-keys \
  --enable-oidc-issuer \
  --enable-workload-identity \
  --attach-acr "$ACR_NAME"

az aks get-credentials --resource-group "$RG" --name "$AKS_NAME"
kubectl get nodes
```

The API image is CPU-oriented. Keep model training, future GPU fine-tuning,
and broad data transformation out of request-serving pods. If a separately
approved GPU job is later introduced, use a distinct image, node pool, resource
limits, data-access identity, scheduling policy, validation plan, cost ceiling,
and lifecycle—not the API deployment.

### 6.3 Azure Database for PostgreSQL Flexible Server

Use PostgreSQL Flexible Server with private connectivity. Create the database
using a dedicated/delegated subnet or an approved Private Link design, correct
Private DNS zone, TLS-required connection path, backup/PITR settings, and a
least-privilege application user. Place the `DATABASE_URL` secret in Key Vault;
do not put it in a ConfigMap or source file.

The source expects a SQLAlchemy Psycopg URL such as:

```text
postgresql+psycopg://<app-user>:<url-escaped-password>@<server-fqdn>:5432/<database>?sslmode=require
```

The actual hostname must be the server FQDN chosen by Azure, not a manually
copied IP address. Test the connection from an API/worker pod before deploying
the application. A successful `/readyz` does **not** test database readiness.
The current source uses a conventional username/password URL; it does not
implement Microsoft Entra database-token acquisition. Keep that password in
Key Vault/approved secret delivery until a token-based database integration is
implemented and tested.

Run migrations exactly once per release from a controlled Job or pipeline step:

```bash
# The final Job manifest is an implementation deliverable, not included in
# k8s/platform/. It must use the same reviewed image digest, DATABASE_URL,
# namespace, network policy, and required non-model configuration.
kubectl apply -f <reviewed-migration-job-manifest.yaml>
kubectl -n ecg-clinical-research wait \
  --for=condition=complete job/<migration-job-name> \
  --timeout=10m
```

The command applies Alembic revisions and creates reference role/permission/
feature rows. It does not create legitimate users, hospitals, patients, or an
identity-provider integration. Do not auto-run unreviewed migrations from every
API pod start.

### 6.4 Redis: Azure Managed Redis / Azure Cache for Redis

The source uses Redis and RQ only when `ASYNC_ANALYSIS=true` and `REDIS_URL`
are configured. Redis receives identifiers for queued analysis work, not raw
ECG bytes, but queue metadata is still sensitive operational data.

Azure Cache for Redis has published retirement guidance for its existing SKUs;
for a new design, evaluate **Azure Managed Redis** in the chosen region. If an
organization requires Azure Cache for Redis temporarily, document its migration
plan, SKU retirement impact, and the exact supported private-endpoint/TLS
configuration.

Set up a private endpoint, disable public network access after validation, use
the canonical service hostname and TLS port identified by the chosen Redis
service/SKU, and test Redis-py/RQ connection behavior. A likely URL shape is:

```text
rediss://:<url-escaped-secret>@<approved-redis-host>:<tls-port>/0
```

This is a template, not a promise that every Azure Redis SKU has the same host,
port, authentication method, certificate chain, or feature compatibility.
Test the actual `Redis.from_url` call used by the worker and asynchronous API in
a non-production environment. The app has a local in-memory Flask limiter; it
does not automatically turn its rate limiter into a shared Redis-backed control
when Redis is present. Put a controlled edge rate-limit/WAF policy in front of
the service and separately improve/application-test rate limiting if required.

### 6.5 Azure Blob Storage

Create a storage account and private containers only after choosing the model
delivery and clinical-object-storage paths in section 4. Blob’s recommended
network posture is private endpoints with public network access disabled where
the intended clients are in Azure or connected by Private Link. Disable
anonymous container/blob access and scope roles to the smallest identity.

```bash
# Illustrative artifact-storage foundation. This does not configure the app's
# current clinical-object storage backend.
az storage account create \
  --resource-group "$RG" \
  --name "$STORAGE_ACCOUNT" \
  --location "$LOCATION" \
  --sku Standard_RAGRS \
  --kind StorageV2 \
  --allow-blob-public-access false \
  --min-tls-version TLS1_2

# Use `--auth-mode login` from a suitably authorized operator identity; do not
# paste a storage account key into a tracked shell script.
az storage container create \
  --account-name "$STORAGE_ACCOUNT" \
  --name "model-artifacts" \
  --auth-mode login
```

Use distinct containers/prefixes and RBAC for model artifacts, clinical
objects, reports/explanations, logs/exports, and backups where the approved
data policy requires separation. Never make a clinical-object or model
container public to simplify testing.

### 6.6 Key Vault and managed identities

Create Key Vault with RBAC authorization, private networking, diagnostic logs,
and an owner/rotation process. Create distinct user-assigned managed identities
or appropriately scoped service-account identities for workloads that need
different capabilities. At minimum, avoid reusing a broad human owner identity
as the API/worker identity.

```bash
az keyvault create \
  --resource-group "$RG" \
  --name "$KEYVAULT_NAME" \
  --location "$LOCATION" \
  --enable-rbac-authorization true

az identity create \
  --resource-group "$RG" \
  --name "<ecg-platform-workload-identity-name>"
```

Store only values such as `DATABASE_URL`, `SECRET_KEY`, `JWT_SECRET`, Redis
credentials, and—only during a temporary S3-compatible path—object-store
credentials. Do not store raw ECG files, model files, or application source in
Key Vault.

The existing platform needs **different** random `SECRET_KEY` and `JWT_SECRET`
values. Rotating either changes session/token behavior; test a coordinated
rollout and user-impact plan. An Azure Key Vault key does not automatically
replace the source code’s HS256 shared-secret JWT design.

### 6.7 Azure Monitor and operational logs

Create/choose a Log Analytics workspace, Azure Monitor workspace/managed
Prometheus configuration, alert routes, and a dashboard owner before exposure.
AKS integrates with Azure Monitor, Container Insights, managed Prometheus, and
Azure Managed Grafana, but enabling collection is not sufficient: decide what
to collect, who can read it, how long it is retained, and whether logs could
contain sensitive identifiers.

At minimum monitor:

| Signal | Why |
| --- | --- |
| AKS node, pod, restart, pending, eviction, CPU/memory and volume signals | Detect capacity, deployment, and scheduling failures. |
| API status, latency, 4xx/5xx, `/healthz`, `/readyz`, request IDs | Detect availability and correlate errors without logging waveform content. |
| Worker/RQ queue depth, age, failure count, job duration | Detect stalled analysis work. |
| PostgreSQL availability, connections, storage/backup/PITR alerts | Protect durable workflow data. |
| Redis availability, connection errors, memory/evictions | Protect queue behavior. |
| Blob/Key Vault/ACR private-endpoint and authorization failures | Detect broken identity/DNS/policy paths. |
| Edge WAF blocks, TLS/certificate expiry, backend health | Detect public-edge abuse and outages. |
| Image/model release identifiers and rollback events | Maintain research provenance. |

Do not log raw waveform bytes, uploaded file contents, access/JWT tokens,
passwords, database URLs, storage keys, presigned URLs, or full patient data in
application logs, diagnostic settings, dashboards, alerts, support tickets, or
screenshots. The app’s audit metadata must not be used as a raw-PHI log sink.

## 7. Model export, promotion, and rollback

### 7.1 Produce a release bundle

Run the exporter only in a trusted environment holding the approved checkpoint:

```bash
ecg-export-model \
  --checkpoint artifacts_final/model.pt \
  --output /secure/export-staging/ramnv2-<release-id>
```

The exporter creates a native bundle containing at least:

```text
model.pt
label_map.json
model_manifest.json
metrics.json                 # when it existed beside the source checkpoint
```

The native bundle is the deployable contract. TorchScript/ONNX files are
optional runtime artifacts for a separately implemented integration and must
not replace `AI_MODEL_PATH`.

Validate before promotion:

```bash
shasum -a 256 /secure/export-staging/ramnv2-<release-id>/model.pt
ecg-predict \
  --checkpoint /secure/export-staging/ramnv2-<release-id>/model.pt \
  --signal "<authorized-non-sensitive-3600-sample-waveform.mat>" \
  --output /secure/verification/ramnv2-<release-id>-gradcam.png
```

Record the export manifest, source/commit revision, model SHA-256, label map,
metrics/split limitations, exact smoke-test input identity under the approved
research governance process, and the approver. A checksum proves file identity,
not clinical fitness or authorization.

### 7.2 Stage a model artifact to Azure Blob

Use a private, versioned artifact location. This example demonstrates an
operator upload only; it does not configure a live pod’s Blob access.

```bash
az storage blob upload \
  --auth-mode login \
  --account-name "$STORAGE_ACCOUNT" \
  --container-name "model-artifacts" \
  --name "ramnv2/<release-id>/model.pt" \
  --file "/secure/export-staging/ramnv2-<release-id>/model.pt" \
  --overwrite false

az storage blob upload \
  --auth-mode login \
  --account-name "$STORAGE_ACCOUNT" \
  --container-name "model-artifacts" \
  --name "ramnv2/<release-id>/model_manifest.json" \
  --file "/secure/export-staging/ramnv2-<release-id>/model_manifest.json" \
  --overwrite false
```

Verify the uploaded content from an authorized controlled path and capture the
server object version/ETag as additional provenance. Do not use an open
anonymous Blob URL, a long-lived SAS in a pod environment variable, or a model
object name that can be silently overwritten.

### 7.3 Deploy only a content-addressed/reviewed release

The final Azure overlay must set a release-specific native path:

```text
AI_MODEL_PATH=/models/model.pt
AI_MODEL_VERSION=<approved-release-id-and-sha-prefix>
```

It must also pin a platform image by digest, for example:

```text
<acr-name>.azurecr.io/ecg-health-platform@sha256:<approved-image-digest>
```

Keep the prior approved image/model pair, migration record, configuration
revision, and validation report ready for rollback. Never replace model bytes
under a path used by running pods. To roll back, deploy the previous immutable
pair through the same reviewed release process, not by copying over
`/models/model.pt` in a live pod.

## 8. Build an Azure-specific Kubernetes overlay

Do not apply `k8s/platform/` directly to AKS until an overlay has been reviewed.
Create a separate non-secret overlay outside or alongside the base manifests,
with environment-specific secret references kept out of Git. The overlay needs
at least the following changes.

| Area | Required Azure overlay behavior |
| --- | --- |
| Image reference | Replace placeholders with ACR digest references for API/worker and add a frontend deployment/image. |
| Namespace/service accounts | Use dedicated service accounts. Bind the correct workload identity only to workloads that require it. |
| Secrets | Replace `secret.example.yaml` placeholders with Key Vault/CSI-derived values through an approved process. Never commit populated Secret YAML. |
| Model | Replace bare PVC assumption with an implemented/verified native model-delivery mechanism; mount it read-only at `/models`. |
| Storage | Do not set Blob endpoint values into the current S3 backend. Use a tested S3-compatible temporary path or wait for the Blob adapter. |
| Database/Redis | Use private FQDNs, TLS URLs, secret injection, and explicit egress policy. |
| Frontend/API routing | Replace the Compose-only `backend` nginx target. Either build a cloud reverse proxy that routes approved `/api` traffic to `ecg-platform-api`, or first add/review a frontend Docker build `ARG`/`ENV` before `npm run build`, then build the client with `VITE_API_BASE_URL=https://<approved-api-host>/api/v1` and test credentialed CORS/cookie/TLS behavior. Set exact CORS/trusted hosts. |
| Networking | Default-deny egress/ingress then explicit traffic permits. Include DNS, private endpoints, monitoring, identity, gateway, and required API/worker connections. |
| Resource controls | Keep/tune API/worker requests/limits using load tests. Set disruption budgets, replica policy, autoscaling policy, topology/availability controls, and a safe rollout strategy. |
| Probes | Retain `healthz` and `readyz`, but understand they do not test database, Redis, storage, migrations, or clinical validity. |
| Migration | Add a controlled release Job/pipeline stage before API/worker rollout. Do not run migration on every replica start. |
| TLS/edge | Add the selected gateway/Front Door config, private/internal service, WAF policy, certificates, domain/DNS validation, and request-size/time-out policy. |

The existing deployment hardening is a useful starting point: runs as non-root
UID/GID 10001, drops Linux capabilities, uses a read-only root filesystem,
mounts `/tmp` as `emptyDir`, and mounts `/models` read-only. Preserve and test
these properties. A security context alone does not prove the end-to-end
deployment is secure.

### Required environment inventory

These values must be configured in the final release, with secret values from
Key Vault/approved injection rather than a ConfigMap:

| Variable | Source | Notes |
| --- | --- | --- |
| `ECG_PLATFORM_ENV=production` | ConfigMap/overlay | Production rejects missing `SECRET_KEY`/`JWT_SECRET`. |
| `DATABASE_URL` | Secret | PostgreSQL Flexible Server URL with TLS. |
| `SECRET_KEY` | Secret | Unique random app secret; distinct from JWT secret. |
| `JWT_SECRET` | Secret | Unique random JWT signing secret. |
| `AI_MODEL_PATH=/models/model.pt` | ConfigMap/overlay | Native verified `.pt` only. |
| `AI_MODEL_VERSION` | ConfigMap/overlay | Immutable approved model identifier. |
| `REDIS_URL` | Secret | Required for async RQ workflow; test `rediss://` and chosen Azure service details. |
| `ASYNC_ANALYSIS=true` | ConfigMap/overlay | Requires a running worker and Redis. |
| `OBJECT_STORAGE_*` | Secret + ConfigMap only for a proven supported path | Current code needs S3-compatible endpoint/bucket/static key/secret. Do not label Azure Blob as configured until the native adapter is implemented. |
| `ALLOW_OBJECT_STORAGE_BUCKET_CREATE=false` | ConfigMap | Pre-provision private storage; avoid app-created production buckets. |
| `CORS_ORIGINS` | ConfigMap/overlay | Exact approved HTTPS browser origin(s); never wildcard. |
| `ECG_PLATFORM_TRUSTED_HOSTS` | ConfigMap/overlay | Exact API/edge hosts expected after the gateway. |
| `MPLCONFIGDIR=/tmp/matplotlib` | ConfigMap/overlay | Needs writable `/tmp` mount. |
| `PUBLIC_BASE_URL` | Do not rely on it for routing today | The settings object reads this value, but the current source does not consume it elsewhere. Treat URL-generation behavior as implementation work, not a configured platform capability. |

`MAX_UPLOAD_BYTES` defaults to 16 MiB. Select edge/gateway and application
request body limits deliberately; do not increase them without upload abuse,
malware/content-validation, resource, and data-governance review.

## 9. Deployment order

Use a promotion pipeline with human approval gates. A recommended order is:

1. **Plan and validate IaC.** Run policy/security checks and obtain the gate
   approvals in section 3.
2. **Create network/DNS foundations.** VNet/subnets, Private DNS zones, private
   endpoint plan, approved edge subnet, and egress controls.
3. **Create observability and guardrails.** Log Analytics/Azure Monitor,
   diagnostics, budgets, alerts, tags, RBAC, policies, and resource locks.
4. **Create ACR and build images.** Scan, attest where required, record digest,
   and grant only pull rights to the AKS kubelet identity.
5. **Create data services.** PostgreSQL private access/backup/PITR, Azure
   Managed Redis private endpoint, Key Vault, and private Blob containers.
6. **Resolve the storage compatibility decision.** Do not deploy clinical
   upload routes to native Azure Blob before Path A in section 4.1 is
   implemented and tested.
7. **Create AKS and identities.** Enable workload identity, install/configure
   the Key Vault CSI driver, configure service accounts/federated credentials,
   and validate private DNS from a test pod.
8. **Promote the model.** Export, verify, approve, store artifact/manifest, and
   deliver it to the read-only model mount. Validate its checksum from the
   deployed volume.
9. **Render the Azure overlay.** Pin image/model releases, set exact hosts and
   policy names, validate Kustomize output, and confirm it includes no secret
   values or placeholders.
10. **Run migration exactly once.** Confirm backup/restore posture before and
    capture the migration Job log/status.
11. **Deploy worker before enabling asynchronous traffic, then API/frontend.**
    Keep ingress private or restricted initially. Watch rollouts and events.
12. **Configure selected edge/DNS/TLS.** Verify WAF, certificate, route,
    backend health, allowed hosts, CORS, request ID propagation, and no direct
    public service exposure.
13. **Run acceptance checks.** Use synthetic/non-sensitive test inputs only;
    complete the validation list in section 10.
14. **Approve progressive release.** Document the image digest, model hash,
    configuration/IaC revision, migration revision, test evidence, owners, and
    rollback target.

Only deploy after placeholder detection passes:

```bash
kubectl kustomize <azure-overlay-path> > /tmp/ecg-azure-rendered.yaml
rg -n "replace-with|REPLACE_|example\.test|<[^>]+>" /tmp/ecg-azure-rendered.yaml
```

The expected result is **no output**. Inspect the rendered file safely; do not
upload it to a ticket or chat if it contains secret values.

## 10. Acceptance and operational validation

Perform these checks in a non-production environment before a controlled
research pilot. Use only fake or otherwise approved non-sensitive data.

### Platform and network checks

```bash
kubectl -n ecg-clinical-research get deploy,pods,svc
kubectl -n ecg-clinical-research rollout status deployment/ecg-platform-api
kubectl -n ecg-clinical-research rollout status deployment/ecg-platform-worker
kubectl -n ecg-clinical-research get events --sort-by=.lastTimestamp

# Temporary private diagnostic route only; remove after validation.
kubectl -n ecg-clinical-research port-forward service/ecg-platform-api 8080:8080
curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/readyz
```

Interpret the endpoints correctly:

| Endpoint | What it checks | What it does not check |
| --- | --- | --- |
| `/healthz` | Flask process answers an HTTP request. | Database, Redis, private storage, migration state, external identity, model correctness, or clinical performance. |
| `/readyz` | Configured `AI_MODEL_PATH` exists as a file. | Checkpoint compatibility beyond the endpoint’s file check, database, Redis, storage, clinical validation, or model accuracy. |

Add authenticated non-sensitive checks for:

- browser route and API request routing through the selected edge;
- exact host and CORS enforcement, including rejection of an unapproved origin;
- login/session behavior and RBAC/tenant-isolation negative tests;
- valid MAT/CSV parser handling and invalid/oversized/malformed file rejection;
- synchronous and asynchronous analysis lifecycle; worker queue success and
  failure handling; verify raw waveform data are not in Redis;
- approved model SHA/version captured with analysis provenance;
- only authorized roles can view waveform, Grad-CAM, report, and download;
- PostgreSQL private DNS/connection/TLS, backup job and a documented restore
  exercise;
- Redis TLS/private DNS, queue restart/recovery, and job-failure alert;
- object-storage integrity, private access, no-overwrite behavior, authorized
  download, and the native Blob-adapter tests if that path is selected;
- Key Vault least privilege and secret rotation rehearsal;
- WAF, certificate, edge backend-health and intended public/private reachability;
- no raw ECG bytes, tokens, passwords, database URLs, or object credentials in
  logs, audit metadata, browser responses, crash reports, or monitoring exports;
- rate-load and resource tests within the uploaded-file size and expected
  concurrency envelope; and
- rollback of an image/model/configuration release in a non-production setting.

Run repository checks before building an approved image:

```bash
export MPLCONFIGDIR=/tmp/ecg-platform-mpl
mkdir -p "$MPLCONFIGDIR"
python -m unittest discover -s tests -v
(cd frontend && npm ci && npm run build)
docker compose config --quiet
kubectl kustomize k8s/platform
```

These are code/configuration checks, not Azure deployment validation and not
clinical validation.

## 11. Security, data, and clinical research limits

### Infrastructure controls to design and verify

- Use Microsoft Entra RBAC, least privilege, separate human and workload
  identities, periodic access reviews, and break-glass governance.
- Use Key Vault with private access after private-DNS validation; rotate
  secrets/certificates through a tested deployment process.
- Keep ACR private, restrict image push rights, pin deploys by digest, scan and
  attest supply-chain artifacts as required, and retain rollback images.
- Use private endpoint/DNS controls for PostgreSQL, Redis, Storage, and Key
  Vault; maintain explicit egress policy and a testable DNS/failover plan.
- Use TLS at the edge and to data services; set an approved TLS/certificate
  policy and never use a self-signed workaround in a patient-data environment.
- Set encryption, backup, PITR, retention, immutable/legal-hold, deletion, and
  data-residency policies through institutional governance—not assumptions
  about a cloud provider default.
- Enable diagnostic logs and alerts while minimizing sensitive content. Define
  access, retention, and incident response for those logs.
- Add malware scanning/content policy, DLP, security testing, penetration
  testing, audit durability, and tenant-isolation testing before any real
  patient data. They are not implemented by this repository.

### Source-code limitations that Azure does not fix

- The platform has local password/JWT authentication and no SSO/OIDC/SAML/MFA,
  token-key rotation, password reset, account lockout, CSRF-token strategy, or
  privileged-action confirmation.
- The Flask rate limiter is configured as in-memory storage even when Redis is
  used for RQ. It is not a shared multi-replica abuse-control system.
- Health probes do not check PostgreSQL, Redis, storage, migration state, or
  clinical/model quality.
- The model metric is an uncalibrated research score from a fragment-level
  split. It is not patient-level, hospital-level, device-level, clinical, or
  treatment performance.
- No model drift, calibration, external/patient-level validation, formal model
  registry approval, clinician credentialing, consent, legal-hold, full
  retention/deletion workflow, tamper-evident audit store, or FHIR server is
  implemented.
- Image/camouflage/encryption experiments are not a patient-data protection or
  clinical deployment control.

Azure services can support a governed design, but they do not convert these
source limitations into clinical or regulatory readiness.

## 12. Cost and capacity controls

Cost must be planned before deployment. Major cost drivers commonly include
AKS nodes/system pools, Application Gateway/Front Door/WAF, PostgreSQL compute
and backup/PITR retention, Azure Managed Redis tier, ACR storage/egress,
private endpoints, Blob capacity/transactions/replication, Log Analytics
ingestion/retention, managed Prometheus/Grafana, data egress, and support.
Current pricing and regional availability must be confirmed using official
Azure pricing tools for the selected subscription/region; this repository makes
no cost estimate.

Set at least:

- subscription/resource-group budgets and cost alerts;
- environment TTL/cleanup policy for non-production resources;
- ACR retention for unreferenced images and a separate retention policy for
  approved/reproducible releases;
- Blob lifecycle policy that does not delete approved model provenance or
  required clinical/research records prematurely;
- Log Analytics ingestion/retention budget and explicit sensitive-log policy;
- AKS resource requests/limits, autoscaling bounds, node-pool scale-down rules,
  PodDisruptionBudgets, and a capacity/load-test evidence record;
- database backup/PITR and high-availability cost decision; and
- a model/training policy that keeps GPU/long-running training jobs out of the
  API cost envelope.

Do not set aggressive scale-to-zero, blob lifecycle deletion, queue TTL, or
database retention values until their effect on active reviews, job recovery,
audit evidence, rollback, and approved data-retention rules is understood.

## 13. Troubleshooting matrix

| Symptom | Likely cause | Safe investigation / resolution |
| --- | --- | --- |
| API pod is `CrashLoopBackOff` immediately | Required production secret, storage setting, or configuration is missing; startup rejects missing `SECRET_KEY`/`JWT_SECRET` outside development. | Inspect sanitized pod events/logs, validate Key Vault/secret sync and env names, never paste a secret in `kubectl describe` output into a ticket. |
| `/readyz` returns 503 | `/models/model.pt` is missing or the model path is not a file. | Inspect read-only mount and deployed `AI_MODEL_PATH`; validate native bundle SHA. Do not point it at `model.ts`/`model.onnx`. |
| Pods pull images locally but AKS returns `ImagePullBackOff` | ACR role, image digest/name, private DNS, firewall, or registry endpoint problem. | Verify ACR digest exists and AKS kubelet pull role; test only from authorized cluster diagnostics. |
| API starts but analysis fails | Database, object storage, model metadata, input contract, or asynchronous queue issue. | Correlate by request ID; test an authorized synthetic waveform; inspect worker and API logs without extracting ECG content. |
| Worker fails or jobs stay queued | `ASYNC_ANALYSIS=true` but Redis/worker connectivity, TLS URL, RQ process, or egress policy is wrong. | Test `Redis.from_url` behavior in a non-production worker image; verify private DNS and NetworkPolicy permits. |
| Storage startup says endpoint/bucket/access key/secret missing | Current app is using the S3 adapter and its contract is incomplete. | Do not substitute Azure Blob endpoint values. Use a tested S3-compatible interim path or implement the native Blob adapter. |
| Blob private endpoint exists but app cannot reach it | Private DNS zone/link, role assignment, network policy, public-access posture, or missing adapter is wrong. | Validate FQDN resolution from a test pod, then check identity/role and adapter integration. A private endpoint alone does not add Blob API support. |
| Browser sees `CORS_ORIGIN_DENIED` or 403 | Edge/frontend origin differs from exact `CORS_ORIGINS`. | Set the exact approved HTTPS origin(s); never widen to wildcard as a workaround. |
| Gateway returns 502/503 | Backend health probe, gateway route, service selector, port, WAF, or TLS configuration mismatch. | Validate Service endpoints, `/healthz` inside cluster, gateway backend-health view, host header, and intended probe route. |
| PostgreSQL connection fails after private deployment | DNS zone/delegated subnet/private endpoint, TLS URL, firewall, role/password, or egress policy issue. | Use the server FQDN, not an IP; test from a controlled pod; validate DNS and `sslmode=require`. |
| Key Vault CSI mount fails | Workload identity federation/service-account annotation, role, Key Vault network/DNS, or SecretProviderClass issue. | Verify identity and private DNS with the platform owner; do not fall back to committing a populated Secret. |
| Model version changes unexpectedly | Mutable model path/object or unpinned image/model configuration. | Stop promotion, redeploy the last approved immutable image/model pair, review storage immutability and release controls. |

## 14. Evidence to retain for each release

For every approved environment/release, retain access-controlled evidence of:

- source Git revision, dependency/image scan results, ACR image digest, and
  rendered non-secret manifest/IaC revision;
- native `model.pt` SHA-256, exporter `model_manifest.json`, label map, model
  version, metrics/split limitations, smoke-test result, and approval record;
- database migration version/job result and backup/restore test record;
- private-endpoint/DNS/egress/identity validation evidence;
- Key Vault secret rotation and access-review evidence without secret values;
- API/worker/frontend rollout, probe, synthetic workflow, RBAC/tenant-negative
  test, and edge/WAF/TLS validation result;
- monitoring/alert test, capacity/load test, incident/rollback exercise, and
  release owner/approval; and
- explicit statement that this remains a research/clinician-review deployment,
  not a clinical diagnostic or treatment system.

## 15. Official Azure references to re-check during implementation

Azure services, command switches, regional availability, retirement dates, and
pricing change. Re-check the current official documentation and organization
policies when implementing the final IaC:

- [AKS application networking options](https://learn.microsoft.com/azure/aks/plan-application-networking)
- [Application Gateway Ingress Controller overview](https://learn.microsoft.com/azure/application-gateway/ingress-controller-overview)
- [Azure Front Door Premium Private Link origins](https://learn.microsoft.com/azure/frontdoor/private-link)
- [AKS–ACR integration](https://learn.microsoft.com/azure/aks/cluster-container-registry-integration)
- [AKS workload identity](https://learn.microsoft.com/azure/aks/workload-identity-overview)
- [Key Vault CSI Driver identity access from AKS](https://learn.microsoft.com/azure/aks/csi-secrets-store-identity-access)
- [PostgreSQL Flexible Server private networking](https://learn.microsoft.com/azure/postgresql/flexible-server/concepts-networking-private)
- [Azure Managed Redis with Private Link](https://learn.microsoft.com/azure/redis/private-link)
- [Azure Storage security and private endpoints](https://learn.microsoft.com/azure/storage/common/secure-storage)
- [AKS monitoring with Azure Monitor](https://learn.microsoft.com/azure/aks/monitor-aks)

Review the [Azure service availability](https://azure.microsoft.com/explore/global-infrastructure/products-by-region/)
and [Azure pricing](https://azure.microsoft.com/pricing/) pages for the selected
region and subscription before approving an architecture or cost estimate.
