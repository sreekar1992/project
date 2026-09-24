# Cross-platform AI model export, import, and operations guide

This guide explains how to prepare, train, export, verify, and run the ECG
research model on macOS and Windows. It covers the native PyTorch model, optional
TorchScript and ONNX exports, the local research workbench, the authenticated
research/clinical-decision-support platform, Docker Compose, and the Kubernetes
scaffolding.

The repository deliberately contains two separate applications:

| Application | Entry point | Local address | Purpose |
| --- | --- | --- | --- |
| Legacy ECG Workbench | **ecg-gui** | http://127.0.0.1:8765 | Local ECG research, visualization, training, encryption/camouflage, and robustness experiments. |
| ECG health platform API | **ecg-health-api** | http://127.0.0.1:8080 | Authenticated multi-hospital research and qualified-clinician-review workflow. |
| ECG health platform web client | **npm run dev** in **frontend/** | http://localhost:5173 | React interface for the health-platform API. |

The supported application model is the PyTorch **model.pt** checkpoint. The
exporter can also create TorchScript or ONNX runtime files, but the included
health-platform API currently loads **only** a native PyTorch checkpoint. A
TorchScript or ONNX export is not a drop-in replacement for **AI_MODEL_PATH**.

## Safety and scope

This repository is a research baseline for ECG rhythm classification. It is not
a validated diagnostic system, disease-probability calculator, treatment
recommender, prescription system, or emergency-triage tool. Softmax values are
uncalibrated model scores, not clinical probabilities.

The supplied Mendeley data use a fragment-level split. Saved validation metrics
must not be represented as patient-level, hospital-level, device-level, or
clinical performance. The data do not contain treatment or outcome targets, so
the model cannot support treatment recommendations.

Keep these concepts separate:

- **Native checkpoint** — model.pt produced by ecg-train and consumed by the
  included prediction command, legacy workbench, and health-platform API.
- **Export bundle** — a new directory made by ecg-export-model that carries a
  copied native checkpoint, label map, provenance manifest, and available
  sibling metrics.
- **TorchScript / ONNX export** — optional inference-runtime files for a
  separately implemented integration. They take an already preprocessed tensor;
  they do not read MATLAB/CSV files or perform ECG preprocessing.
- **Legacy workbench** — a localhost research tool. Its encryption and
  camouflage demonstrations are not patient-data protection or a clinical
  deployment path.
- **Health platform** — a separate authenticated research/clinician-review
  foundation. It needs operational security, governance, and independent
  validation before any real patient data could be considered.

Read [the existing ECG AI interface](existing-ecg-ai-interface.md) before
changing the input contract. For platform architecture and deployment limits,
also read [architecture](architecture.md), [deployment](deployment.md), and
[security](security.md).

## Supported systems and prerequisites

The commands below work with macOS Terminal using zsh, Windows PowerShell, and
Windows Command Prompt (cmd.exe).

| Requirement | Why it is needed | Notes |
| --- | --- | --- |
| Python 3.10 or later | Training, prediction, export, API, and tests | Prefer Python 3.12 for this project because the Docker images use it and current PyTorch warns that TorchScript support is deprecated on Python 3.14+. |
| pip and venv support | Isolated Python dependencies | There is no requirements.txt; install through pyproject.toml. |
| Node.js with npm, compatible with Node 22 tooling | React frontend | The frontend Docker image uses Node 22. |
| Git | Clone and record source provenance | Optional if the project is already present locally. |
| Docker Desktop and Compose plugin | Containerized development stack | Optional for native local use; available on macOS and Windows. |
| kubectl and cluster access | Kubernetes inspection/deployment | Optional; supplied manifests are scaffolding, not turnkey production deployment. |
| Dataset and trusted checkpoint | Training and inference | Raw ECG data and artifacts are intentionally excluded from Git. |

MATLAB is not required. MATLAB MAT files are read through SciPy.

CPU execution works on both platforms. Training accepts **--device auto**,
**--device cpu**, **--device mps**, and **--device cuda**.

- On Apple Silicon, auto may select PyTorch MPS when it is available.
- On Windows, cuda needs a CUDA-capable GPU plus a PyTorch build compatible with
  the installed driver. The ordinary project install is sufficient for CPU
  training.
- The supplied Docker images install CPU PyTorch. GPU training needs a separate
  CUDA-enabled image and GPU-enabled cluster/node configuration; it is not
  provided by this repository.

The native bundle works independently of TorchScript. If you need the optional
TorchScript export, use a validated Python/PyTorch combination such as the
project's Python 3.12 container baseline. Current PyTorch releases emit a
deprecation warning for torch.jit tracing/loading on Python 3.14 and later.

Always quote paths containing spaces, especially Mendeley file names such as
100m (0).mat.

## Get the code and create the Python environment

If the project is not already on the machine, clone the repository and enter
the main branch.

### macOS Terminal / zsh

~~~zsh
git clone https://github.com/sreekar1992/project.git
cd project
git switch main

python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
~~~

### Windows PowerShell

~~~powershell
git clone https://github.com/sreekar1992/project.git
Set-Location project
git switch main

py -3 --version
py -3 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
~~~

Set-ExecutionPolicy changes policy only for the current PowerShell process. If
your institution manages the policy, follow its approved process instead.

### Windows Command Prompt

~~~bat
git clone https://github.com/sreekar1992/project.git
cd /d project
git switch main

py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e .
~~~

After pulling a newer revision, rerun the editable install before using a newly
added console command such as ecg-export-model:

~~~text
python -m pip install -e .
~~~

Use the **python** command from the activated virtual environment throughout
this guide. Do not use a hard-coded POSIX path such as .venv/bin/python on
Windows.

### Install optional ONNX export support

The normal install supports the native bundle and TorchScript export. ONNX
export additionally requires the optional **onnx** and **onnxscript**
dependencies; the `export` extra installs both.

#### macOS Terminal / zsh

~~~zsh
python -m pip install -e '.[export]'
~~~

#### Windows PowerShell

~~~powershell
python -m pip install -e '.[export]'
~~~

#### Windows Command Prompt

~~~bat
python -m pip install -e ".[export]"
~~~

## Dataset placement and input requirements

The project does not download the dataset automatically. Obtain the ECG research
dataset from Pawel Plawiak, *ECG signals (1000 fragments)*, Mendeley Data V3,
DOI [10.17632/7dybx7wyfn.3](https://data.mendeley.com/datasets/7dybx7wyfn/3),
and follow its license and attribution requirements.

Extract it under the repository root using the class-directory layout expected
by ecg-train:

~~~text
project/
  MLII/
    1 NSR/
      100m (0).mat
      ...
    2 APB/
      ...
    ...
    17 PR/
      ...
~~~

MLII/ is ignored by Git on purpose. Do not add raw recordings, patient data, or
externally licensed data to a public commit merely to make a training command
work.

### Established model input contract

The clinical adapter and single-file prediction workflow accept the following
contract. Do not silently reinterpret another lead, device format, duration, or
sampling rate as this model's input.

| Property | Required value |
| --- | --- |
| Input form | A MAT file containing numeric val, or exactly one numeric one-dimensional 3,600-sample array; alternatively a comma-delimited CSV waveform. |
| Signal | One finite, non-flat, real-valued, single-lead waveform. |
| Length and acquisition assumption | 3,600 samples: 10 seconds at 360 Hz. |
| Filtering | Fourth-order, zero-phase 0.5–45 Hz Butterworth band-pass filter. |
| Normalization | Per-record median/MAD normalization. |
| Model input | Every fourth normalized sample, producing 900 samples at 90 Hz. |
| Tensor shape | (batch, 1, 900) with float32 values. |
| Model | RAMNV2(num_classes), which returns class logits. |

Rendered ECG charts, Grad-CAM PNGs, encrypted byte visualizations, and other
images are not classifier inputs. The model operates on numerical ECG waveform
samples.

The broad training loader can also read a conventional matrix-and-label MATLAB
file using --signal-key and --label-key. Keep its records compatible with the
baseline's 3,600-sample MLII contract if the resulting checkpoint will be used
by the clinical adapter. MATLAB v7.3/HDF5 files may require additional reader
support; validate them before relying on a batch run.

## Train a native PyTorch checkpoint

From an activated environment in the repository root, train the baseline on the
extracted MLII/ directory. Command syntax is the same after shell-specific
environment activation.

~~~text
ecg-train --data MLII --epochs 30 --batch-size 32 --artifacts artifacts_final
~~~

Choose a new artifacts directory for each experiment rather than overwriting a
checkpoint that has already been reviewed:

~~~text
ecg-train --data MLII --epochs 30 --batch-size 32 --lr 0.001 --seed 42 --device auto --artifacts artifacts_experiment_001
~~~

For a bounded adversarial-training experiment, use explicit options. This is an
ECG research robustness experiment, not a privacy, encryption, or clinical
safety guarantee.

~~~text
ecg-train --data MLII --epochs 30 --batch-size 32 --artifacts artifacts_robust --adversarial-epsilon 0.05 --adversarial-weight 0.5
~~~

The training command writes these files:

| Output | Meaning |
| --- | --- |
| model.pt | Required native PyTorch checkpoint. It contains state_dict, num_classes, label_map, plus training/split/preprocessing metadata in newly trained checkpoints. |
| label_map.json | Human-readable label-to-index mapping. The checkpoint's embedded numeric map remains the runtime source of truth. |
| metrics.json | Saved fragment-level evaluation metrics, run configuration, and warning. |
| confusion_matrix.png | Validation visualization from the training split. |

The training/validation split is stratified by **fragment**, not patient. Keep
the command line, source-data version, Git commit, artifact SHA-256, and
evaluation limitations with every model version. A new patient-/recording-
disjoint and externally validated evaluation is required before stronger claims
could be made.

### Current local baseline model card

The following values describe the supplied `artifacts_final/model.pt` at the
time this guide was prepared. Treat them as provenance for that exact file, not
as a clinical claim or a target that a retrained model must reproduce.

| Item | Value |
| --- | --- |
| Architecture | RAMNV2 one-dimensional residual-attention CNN |
| Parameters | 27,633 |
| Input / output | `(batch, 1, 900)` float32 samples → 17 logits |
| Source file SHA-256 | `f2ea83714317c0b98cc36f314775e0c69001f810b6ed0d860429b756a71daa35` |
| Saved validation result | 87.0% accuracy; 86.22% weighted F1 |
| Evaluation split | 800 training / 200 validation fragments |

Those scores are fragment-level results from this dataset, where fragments can
share a recording source across splits. They are not patient-level performance
or calibrated disease probabilities. Always read class order from the exported
`label_map.json` or `model_manifest.json`; do not recreate it manually from
folder names.

## Export the AI model

### What the exporter does

ecg-export-model creates a new, immutable-by-convention export directory. It
validates that the source is a supported RAMNV2 state-dict checkpoint with a
contiguous numeric label map, then loads the weights strictly on CPU. It never
overwrites an existing output directory.

~~~text
ecg-export-model --checkpoint <source-model.pt> --output <new-output-directory> --format <bundle|torchscript|onnx|all>
~~~

The --format option may be repeated. If omitted, the exporter creates a bundle.
A verified native bundle is always created, even when torchscript, onnx, or all
is requested.

| Requested format | Files created in the new export directory | Verification performed by the exporter |
| --- | --- | --- |
| bundle | model.pt, label_map.json, model_manifest.json, and metrics.json when the source checkpoint has a sibling metrics file | Checkpoint structure, label-map continuity, strict RAMNV2 weight load, and payload checksums. |
| torchscript | All bundle files plus model.ts | Traces a (1, 1, 900) float32 CPU tensor, reloads the result, and rejects a maximum logit difference over 1e-5. |
| onnx | All bundle files plus model.onnx | Requires the optional ONNX dependencies; exports with opset 17 and structurally validates with `onnx.checker.check_model`. It does not run ONNX Runtime or establish numerical parity. |
| all | All bundle files plus model.ts and model.onnx | Runs the relevant TorchScript and ONNX checks. |

The export manifest records the source checkpoint SHA-256, architecture,
ordered labels, preprocessing contract, training configuration and split when
present, payload checksums, runtime-export metadata, and the research-only
safety statement. Preserve it with the model. The manifest deliberately does
not hash itself; calculate and retain a separate manifest SHA-256 if your
release process requires one.

When `metrics.json` sits beside the source checkpoint, it is copied and
checksummed as a provenance attachment. The exporter can verify its bytes, but
cannot prove that an arbitrary sibling metrics file was produced by that exact
checkpoint. The release owner must verify the training run, source data, and
recorded checkpoint hash before presenting metrics with a model.

### Export the native portable bundle

The destination directory must not exist before the command runs.

#### macOS Terminal / zsh

~~~zsh
mkdir -p exports
ecg-export-model --checkpoint artifacts_final/model.pt --output exports/ramnv2-ecg-bundle --format bundle
~~~

#### Windows PowerShell

~~~powershell
New-Item -ItemType Directory -Force -Path .\exports | Out-Null
ecg-export-model --checkpoint .\artifacts_final\model.pt --output .\exports\ramnv2-ecg-bundle --format bundle
~~~

#### Windows Command Prompt

~~~bat
if not exist exports mkdir exports
ecg-export-model --checkpoint artifacts_final\model.pt --output exports\ramnv2-ecg-bundle --format bundle
~~~

### Export the native bundle plus TorchScript

TorchScript needs no extra project dependency beyond PyTorch:

~~~text
ecg-export-model --checkpoint artifacts_final/model.pt --output exports/ramnv2-with-torchscript --format torchscript
~~~

The integration contract for `model.ts` is an already preprocessed float32
tensor shaped `(batch, 1, 900)`. The traced model does not validate an arbitrary
caller tensor for you, so the integrating service must enforce that contract. It
does not contain the MAT/CSV reader or the 360 Hz filtering, normalization, and
downsampling pipeline.

### Export the native bundle plus ONNX

Install the optional extra first, then choose a new output directory:

~~~text
python -m pip install -e ".[export]"
ecg-export-model --checkpoint artifacts_final/model.pt --output exports/ramnv2-with-onnx --format onnx
~~~

The exporter checks that the ONNX file is structurally valid. Before an ONNX
file is used in a separate runtime, run an explicit numerical-parity test with
that runtime against the native CPU checkpoint on authorized test waveforms.

### Export all supported runtime files

After installing .[export], this creates the bundle, TorchScript, and ONNX files
together:

~~~text
ecg-export-model --checkpoint artifacts_final/model.pt --output exports/ramnv2-all-formats --format all
~~~

Equivalent repeated-format form:

~~~text
ecg-export-model --checkpoint artifacts_final/model.pt --output exports/ramnv2-runtime-formats --format torchscript --format onnx
~~~

Do not point --output at artifacts_final/ or another existing directory. The
exporter intentionally fails rather than replacing an existing model export.

### Integrity and transfer checks

The copied model.pt should have the same SHA-256 as the source checkpoint. Record
both source and export hashes before moving an artifact.

#### macOS Terminal / zsh

~~~zsh
shasum -a 256 artifacts_final/model.pt exports/ramnv2-ecg-bundle/model.pt
shasum -a 256 exports/ramnv2-ecg-bundle/model_manifest.json
ditto -c -k --keepParent exports/ramnv2-ecg-bundle exports/ramnv2-ecg-bundle.zip
~~~

#### Windows PowerShell

~~~powershell
Get-FileHash -Algorithm SHA256 .\artifacts_final\model.pt, .\exports\ramnv2-ecg-bundle\model.pt
Get-FileHash -Algorithm SHA256 .\exports\ramnv2-ecg-bundle\model_manifest.json
Compress-Archive -Path .\exports\ramnv2-ecg-bundle -DestinationPath .\exports\ramnv2-ecg-bundle.zip
~~~

#### Windows Command Prompt

~~~bat
certutil -hashfile "artifacts_final\model.pt" SHA256
certutil -hashfile "exports\ramnv2-ecg-bundle\model.pt" SHA256
certutil -hashfile "exports\ramnv2-ecg-bundle\model_manifest.json" SHA256
~~~

Use a private, approved artifact repository or transfer channel for model
artifacts. The repository ignores paths matching artifacts*/, raw MLII data,
.env, local database/storage, keys, and ZIP archives. A generic exports/
directory is not necessarily ignored: do not stage an export by accident; keep
it outside the repository or apply an approved ignore policy. A checksum
establishes file identity; it does not authorize an artifact for clinical use.


## Import and verify an exported model

### Native checkpoint: supported by this repository

On the receiving machine, unpack the exported folder, recreate the compatible
Python environment, and use the bundle's model.pt with the same code version or
an explicitly tested compatible version.

Run a real numerical-waveform smoke test before wiring the artifact to the UI or
API. Replace the example signal path with a validated 3,600-sample MAT or CSV
file.

~~~text
ecg-predict --checkpoint exports/ramnv2-ecg-bundle/model.pt --signal "MLII/1 NSR/100m (0).mat" --output verification/exported-model-gradcam.png
~~~

On Windows, the same command works with backslash paths:

~~~text
ecg-predict --checkpoint .\exports\ramnv2-ecg-bundle\model.pt --signal ".\MLII\1 NSR\100m (0).mat" --output .\verification\exported-model-gradcam.png
~~~

Create the separate verification/ directory first if it does not already exist.
Do not add a smoke-test PNG inside an immutable export bundle.

ecg-predict loads the native checkpoint, reuses the project preprocessing, and
emits a research-only prediction plus a Grad-CAM PNG. It does not make a
diagnosis or treatment recommendation.

### TorchScript and ONNX: optional, separate integration work

The repository UI, ecg-predict, and health-platform API do not load model.ts or
model.onnx. They load the PyTorch model.pt checkpoint and use the project RAMNV2
architecture and preprocessing functions.

An external TorchScript or ONNX service may use optional runtime files only after
it implements and validates all of the following itself:

1. MAT/CSV parsing and the exact single-lead 3,600-sample validation contract.
2. The 0.5–45 Hz zero-phase filter, per-record median/MAD normalization, and
   every-fourth-sample conversion to 900 values.
3. For the current ONNX export, an input named `ecg` with the fixed float32
   shape `[1, 1, 900]` and a logits output. It is not exported with dynamic
   shapes. For TorchScript, supply `(batch, 1, 900)` as the positional model
   input and enforce that contract in the integration.
4. Logit-to-label mapping using the ordered labels in label_map.json or
   model_manifest.json.
5. Independent numerical-equivalence, security, and research-validation checks.

Do not configure AI_MODEL_PATH to model.ts or model.onnx. For the included
platform, configure it to the native exported file:

~~~text
<export-directory>/model.pt
~~~

## Run the legacy ECG Workbench locally

The legacy workbench is local-only research software. It discovers native
checkpoints under artifacts*/model.pt and artifacts_gui/*/model.pt beneath the
selected project root. It does not discover a model hidden only in an export
directory unless you deliberately place/copy the native checkpoint into one of
those supported locations.

### macOS Terminal / zsh

~~~zsh
cd "/path/to/project"
source .venv/bin/activate
ecg-gui --project "$PWD" --open
~~~

### Windows PowerShell

~~~powershell
Set-Location "C:\path\to\project"
.\.venv\Scripts\Activate.ps1
$project = (Get-Location).Path
ecg-gui --project $project --open
~~~

### Windows Command Prompt

~~~bat
cd /d "C:\path\to\project"
.\.venv\Scripts\activate.bat
ecg-gui --project "%CD%" --open
~~~

Open http://127.0.0.1:8765 if the browser does not open automatically. Keep
the terminal open while the workbench is running; press Ctrl+C in that terminal
to stop it.

Launch ECG Workbench.command is a macOS Finder/zsh launcher only. It is not a
Windows launcher; use the activated PowerShell or Command Prompt commands
instead.

## Run the authenticated health platform locally

The local platform uses development-only SQLite at var/ecg_health.db and private
local files under var/clinical_objects when a database URL and object-storage
settings are not supplied. Those defaults are for fake, local development data
only.

The API readiness endpoint needs a valid native checkpoint at AI_MODEL_PATH. Use
a native model.pt, not an optional TorchScript or ONNX file.

### Start the API on macOS Terminal / zsh

~~~zsh
cd "/path/to/project"
source .venv/bin/activate
export AI_MODEL_PATH="$PWD/artifacts_final/model.pt"
export AI_MODEL_VERSION="local-research-checkpoint"
ecg-health-api --project "$PWD" --host 127.0.0.1 --port 8080
~~~

### Start the API on Windows PowerShell

~~~powershell
Set-Location "C:\path\to\project"
.\.venv\Scripts\Activate.ps1
$project = (Get-Location).Path
$env:AI_MODEL_PATH = Join-Path $project "artifacts_final\model.pt"
$env:AI_MODEL_VERSION = "local-research-checkpoint"
ecg-health-api --project $project --host 127.0.0.1 --port 8080
~~~

### Start the API on Windows Command Prompt

~~~bat
cd /d "C:\path\to\project"
.\.venv\Scripts\activate.bat
set "AI_MODEL_PATH=%CD%\artifacts_final\model.pt"
set "AI_MODEL_VERSION=local-research-checkpoint"
ecg-health-api --project "%CD%" --host 127.0.0.1 --port 8080
~~~

Check process health and model readiness from a second terminal:

| Check | macOS / Windows Command Prompt | Windows PowerShell |
| --- | --- | --- |
| Process health | curl http://127.0.0.1:8080/healthz | Invoke-RestMethod http://127.0.0.1:8080/healthz |
| Model readiness | curl http://127.0.0.1:8080/readyz | Invoke-RestMethod http://127.0.0.1:8080/readyz |

/healthz only confirms that the Flask process responds. /readyz confirms that
a compatible configured model file exists. Neither endpoint establishes clinical
accuracy, database availability, storage availability, or production readiness.

### Start the React frontend

Open a second terminal. No Python environment is needed in that terminal; run
the frontend from its own directory.

#### macOS Terminal / zsh

~~~zsh
cd "/path/to/project/frontend"
npm ci
npm run dev
~~~

#### Windows PowerShell

~~~powershell
Set-Location "C:\path\to\project\frontend"
npm ci
npm run dev
~~~

#### Windows Command Prompt

~~~bat
cd /d "C:\path\to\project\frontend"
npm ci
npm run dev
~~~

Open http://localhost:5173. Vite proxies /api to http://localhost:8080 by
default. The local API permits both http://localhost:5173 and
http://127.0.0.1:5173 as explicit development origins.

For a fresh local development database, ecg-health-seed can create clearly fake
development users and records after prompting for a development-only password
of at least 12 characters:

~~~zsh
# macOS Terminal / zsh, from the repository root with .venv activated
ecg-health-seed --project "$PWD"
~~~

~~~powershell
# Windows PowerShell, after setting $project above
ecg-health-seed --project $project
~~~

~~~bat
REM Windows Command Prompt, from the repository root with .venv activated
ecg-health-seed --project "%CD%"
~~~

Do not use seed data, seed identities, the local database, or local storage for
real patient data. The command does not provision a production administrator.

For a fresh fake local database, sign in using the password supplied to
`ecg-health-seed` and one of these deliberately non-real addresses:

| Local fixture role | Email |
| --- | --- |
| Super administrator | `superadmin@example.test` |
| Hospital administrator | `hospitaladmin@example.test` |
| Doctor | `doctor@example.test` |
| ECG technician | `technician@example.test` |
| Patient | `patient@example.test` |

The seed utility does not reset the password of an account that already exists.
Use a new, fake development database if you need a different local fixture
password; never turn this utility into a production identity-provisioning path.

If you deliberately change the frontend host or port, configure the API
CORS_ORIGINS as a narrowly scoped comma-separated list before starting it. Do
not use a wildcard origin.

## Docker Compose on macOS and Windows

Docker Compose is the supplied cross-platform, production-like **development**
stack. It starts PostgreSQL, Redis, MinIO, a one-shot migration service, the
Flask API, an RQ worker, and the nginx-served React frontend.

Before starting it:

1. Install and start Docker Desktop.
2. Make sure Docker Desktop can read the project directory, especially on
   Windows.
3. Confirm the host contains the required native model at
   artifacts_final/model.pt.
4. Create a private .env from .env.example and replace every placeholder with
   unique local-development secrets. Never commit the populated .env.

### Create .env

#### macOS Terminal / zsh

~~~zsh
cp .env.example .env
~~~

#### Windows PowerShell

~~~powershell
Copy-Item .env.example .env
~~~

#### Windows Command Prompt

~~~bat
copy .env.example .env
~~~

Set the database, application/JWT, MinIO, bucket, and model-version values in
.env. Do not paste a real patient credential, production secret, or unapproved
object-storage key into an issue, screenshot, or source commit.

### Start and inspect the stack

The Docker commands are identical in all three shells:

~~~text
docker compose up --build -d
docker compose ps
~~~

The Compose bindings are intentionally loopback-only:

| Component | Local address |
| --- | --- |
| Frontend | http://127.0.0.1:5173 |
| API | http://127.0.0.1:8080 |
| MinIO console | http://127.0.0.1:9001 |
| PostgreSQL, Redis, MinIO S3 API | Internal Compose network only |

Use the same /healthz and /readyz checks described above. The Compose migrate
service runs ecg-health-migrate before the API and worker start. It applies
Alembic revisions and initializes only role, permission, and feature reference
rows; it does not create users, hospitals, patients, or demo data.

If a strictly fake local fixture is needed, start an interactive seed command
without putting a password in shell history:

~~~text
docker compose exec -it -e ECG_PLATFORM_ENV=development backend ecg-health-seed
~~~

The Compose API and worker consume /models/model.pt, the read-only mount of the
host artifacts_final/ directory. An export bundle can be used by copying its
verified native model.pt into that mounted model directory under a controlled
release process. Do not mount raw training data, encryption keys, or real
patient records into the API container.

To stop the development stack without deleting volumes:

~~~text
docker compose down
~~~

Do not run docker compose down -v unless you intentionally want to delete local
PostgreSQL, Redis, and MinIO volumes.

## Kubernetes boundaries and model delivery

The repository contains two separate Kubernetes areas:

| Path | Scope |
| --- | --- |
| k8s/base/ | Older private legacy-workbench deployment. |
| k8s/platform/ | Authenticated health-platform API and RQ worker scaffolding. |
| k8s/jobs/retrain-baseline-gpu.yaml | Optional example retraining Job for the legacy baseline; not a pretrained-model fine-tuning workflow. |

k8s/platform/ is not a complete production deployment. It has not been
validated against a production cluster and deliberately does not deploy a
frontend, ingress, database, Redis, object storage, Secret values, model PVC,
or migration Job for you.

Before any kubectl apply, an operator must:

1. Build and publish Dockerfile.platform with an immutable image digest matching
   the target Kubernetes node architecture. Typical Intel Windows/cloud nodes
   use linux/amd64; Apple Silicon local clusters commonly use linux/arm64. Do
   not assume one architecture fits every cluster.
2. Provision private PostgreSQL, Redis, and S3-compatible object storage.
3. Provision a read-only PVC named ecg-platform-models containing a verified
   native model.pt at its root. Keep the export manifest and provenance in the
   approved model registry or volume-management process.
4. Copy k8s/platform/secret.example.yaml outside the repository, replace all
   values through an approved secret-management process, and create the
   ecg-platform-secrets Secret. Do not commit or apply the placeholder file as a
   real secret.
5. Replace ConfigMap placeholder hosts, bucket, CORS origin, trusted hosts, and
   registered model version with approved values.
6. Run ecg-health-migrate as a controlled release step against the intended
   PostgreSQL database before serving requests.
7. Provide a separate authenticated TLS frontend/Ingress and validate network
   policies, backups, monitoring, retention, identity, privacy, and clinical
   governance.

These basic commands are the same from macOS Terminal, PowerShell, and Command
Prompt:

~~~text
kubectl kustomize k8s/platform
kubectl apply -k k8s/platform
kubectl -n ecg-clinical-research rollout status deployment/ecg-platform-api
kubectl -n ecg-clinical-research port-forward service/ecg-platform-api 8080:8080
~~~

Run only the first command until the prerequisites are complete. The service is
intentionally ClusterIP; port forwarding is suitable for private inspection, not
a public clinical interface.

The manifests mount /models/model.pt read-only and set AI_MODEL_PATH to that
native checkpoint. They do not understand model.ts or model.onnx. The supplied
platform image is CPU-oriented; do not treat the GPU example Job as a CUDA-ready
image definition.

For the detailed current Kubernetes checklist, see
[k8s/platform/README.md](../k8s/platform/README.md) and
[k8s/README.md](../k8s/README.md).

For provider-specific adaptation requirements, use the separate
[AWS deployment runbook](aws-deployment.md) or
[Azure deployment runbook](azure-deployment.md). Neither runbook represents a
completed cloud deployment or clinical authorization.

## Tests and reproducibility checks

Set MPLCONFIGDIR to a writable temporary directory before running the Python
suite. This prevents Matplotlib from trying to write its font cache into an
unwritable default location.

### macOS Terminal / zsh

~~~zsh
cd "/path/to/project"
source .venv/bin/activate
export MPLCONFIGDIR="/tmp/ecg-platform-mpl"
mkdir -p "$MPLCONFIGDIR"

python -m unittest discover -s tests -v
(cd frontend && npm run build)
~~~

### Windows PowerShell

~~~powershell
Set-Location "C:\path\to\project"
.\.venv\Scripts\Activate.ps1
$env:MPLCONFIGDIR = Join-Path $env:TEMP "ecg-platform-mpl"
New-Item -ItemType Directory -Force -Path $env:MPLCONFIGDIR | Out-Null

python -m unittest discover -s tests -v
Push-Location frontend
try {
  npm run build
} finally {
  Pop-Location
}
~~~

### Windows Command Prompt

~~~bat
cd /d "C:\path\to\project"
.\.venv\Scripts\activate.bat
set "MPLCONFIGDIR=%TEMP%\ecg-platform-mpl"
if not exist "%MPLCONFIGDIR%" mkdir "%MPLCONFIGDIR%"

python -m unittest discover -s tests -v
pushd frontend
npm run build
popd
~~~

Optional configuration-only checks, after Docker Compose and kubectl are
installed:

~~~text
docker compose config --quiet
kubectl kustomize k8s/platform
~~~

For model-specific reproducibility, retain the following together:

- Git commit or source release identifier.
- Dataset source/version and applicable license attribution.
- Exact training command, random seed, device, and run date.
- Model.pt SHA-256 and model_manifest.json from the export bundle.
- Metrics.json, confusion matrix, and explicit split limitations.
- A smoke-test result from ecg-predict on an authorized, non-sensitive test
  waveform.

## Troubleshooting

| Symptom | Likely cause | Resolution |
| --- | --- | --- |
| ecg-export-model is not recognized | The virtual environment is inactive or the editable package was installed before the exporter was added. | Activate .venv and run python -m pip install -e . again. |
| Export says the output exists | The exporter never overwrites an existing directory. | Choose a new output directory; do not delete a reviewed export merely to reuse its name. |
| ONNX export says onnx or onnxscript is missing | The optional export extra is not installed. | Run python -m pip install -e '.[export]' in zsh/PowerShell, or python -m pip install -e ".[export]" in Command Prompt. |
| Platform /readyz returns 503 | AI_MODEL_PATH is missing, points to a non-file, or is not a compatible native checkpoint. | Set it to an existing model.pt; do not point it at model.ts or model.onnx. |
| Browser says its origin is not authorized | The UI origin is outside CORS_ORIGINS. | Use the default localhost:5173/127.0.0.1:5173, or configure the exact approved frontend origin before starting the API. |
| ECG upload/prediction rejects a file | The file is an image, malformed, wrong-length, complex/non-finite/flat, or not one 3,600-sample waveform. | Supply a valid numerical MAT/CSV waveform matching the documented contract. |
| PowerShell blocks Activate.ps1 | Execution policy blocks local activation. | Use the current-process Set-ExecutionPolicy command, follow organization policy, or use Command Prompt activation. |
| Matplotlib font-cache warning/error | The cache directory is not writable. | Set MPLCONFIGDIR as shown in the test section and create it first. |
| Port 8080, 5173, or 8765 is in use | Another local API, frontend, or workbench process is still running. | Stop the prior process or choose a different supported port. For the workbench, use ecg-gui --port 8766. |
| Docker API is not ready | Host artifacts_final/model.pt is missing or Docker Desktop cannot read the project directory. | Restore the verified native artifact and enable appropriate project-directory sharing. |
| Seed command does not create a production login | The seed utility is intentionally limited to fake development fixtures. | Provision real identities only through an approved administrative and identity-management process. |
| A transferred checkpoint cannot load | It changed, was truncated, was made by incompatible code, or lacks supported checkpoint fields. | Compare SHA-256 values, retain the export bundle/manifest, and use compatible RAMNV2/preprocessing code. |

## Final operational checklist

Before sharing or deploying a model, confirm all of the following:

- The artifact is a verified native model.pt with a recorded SHA-256.
- The export directory was created by ecg-export-model and includes
  model_manifest.json and label_map.json.
- Optional TorchScript/ONNX files are labeled as separate runtime exports, not as
  files consumed by the included clinical API.
- The receiving environment uses the exact waveform preprocessing contract.
- The model version, source data, training command, and evaluation limitations
  travel with the artifact.
- No raw MLII files, real ECGs, patient data, database files, keys, or populated
  .env values are placed in Git or container images.
- Development-only fixture users are not reused outside a fake local database.
- Docker Compose and Kubernetes are treated as infrastructure scaffolding, not
  evidence of clinical safety, privacy compliance, regulatory clearance, or
  production readiness.
- A qualified clinician and appropriate institutional governance—not an AI
  score—remain responsible for every clinical decision.
