# Use this project on another laptop

This file is a reusable prompt for a coding assistant **and** a short setup
checklist. Choose one of two distinct goals:

- **Access the existing deployed portal:** connect the new laptop to the GKE
  frontend. The application and its existing data remain in Google Cloud.
- **Run an independent local development copy:** install the code, supply a
  compatible private model checkpoint, and create only fake local records.
  Cloning GitHub does not copy the deployed database or patient assets.

The repository contains an authenticated ECG research platform and a separate
legacy ECG workbench. The model is research/decision-support only; its output
is not a diagnosis or treatment recommendation.

## Copyable AI prompt

Paste the following into an assistant on the new laptop, then replace the two
bracketed values. Never paste passwords, tokens, patient data, or private keys
into the prompt.

> Help me set up `https://github.com/sreekar1992/project` on my
> **[macOS / Windows / Linux]** laptop. My goal is **[access the existing GKE
> portal / run an independent local development copy]**. First inspect
> `README.md`, `aiprompt.md`, `localconnect.md`, `docs/development.md`, and
> `docs/model-export-cross-platform.md` in the cloned repository. Check my
> installed tools and show commands appropriate for my operating system and
> shell. Do not assume a checkpoint, ECG dataset, credentials, or a running
> Docker daemon exists. For GKE access, verify the Google Cloud CLI, `kubectl`,
> GKE auth plugin, account authorization, and private-network connectivity;
> start a loopback-only port-forward and verify the login page. For local
> development, install dependencies in isolated environments, confirm a
> compatible native `artifacts_final/model.pt` exists, start the API and
> frontend, seed only fake development users with a password I enter locally,
> and verify `/healthz`, `/readyz`, and the frontend. If a prerequisite is
> missing, stop and explain how to obtain it safely. Never copy production
> patient records into development, print secrets, expose a public port,
> silently change the model input contract, or claim the model is clinically
> validated. Report what actually worked and what remains unavailable.

## Before either route

1. Install Git and clone the repository on the new laptop:

   ```sh
   git clone https://github.com/sreekar1992/project.git
   cd project
   git switch main
   ```

   On Windows, run these commands in PowerShell. A clone contains **pushed
   commits only**; uncommitted changes on the old laptop do not transfer.
2. Decide whether you need the **same deployed data** (use the GKE route) or
   a **new fake local database** (use the local development route). Do not
   export real clinical records merely to make the local example work.
3. The checkpoint, `MLII/` dataset, `.env`, keys, and `var/` data are
   Git-ignored. Transfer a permitted, verified checkpoint privately from the
   original laptop or an approved artifact store if local inference is needed.
   Do not upload those files to GitHub. For the research training dataset, see
   the [Mendeley source and layout](README.md#dataset).

## Route A: open the existing GKE portal

This route needs **no local model or patient-data copy**. Install the Google
Cloud CLI, `kubectl`, and the GKE authentication plugin on the new laptop.
Confirm that `gke-gcloud-auth-plugin --version` works; if it does not, install
the plugin using the method appropriate for your Google Cloud CLI installation
and ensure it is on `PATH`. Sign in with an account authorized for the GCP
project and connect to the required private network or VPN.

Run in a terminal (the same commands work in PowerShell):

```sh
gcloud auth login
gcloud container clusters get-credentials ecg-research-gke --region asia-south1 --project banded-torus-510415-g2
kubectl -n ecg-clinical-research get service ecg-platform-frontend
kubectl -n ecg-clinical-research port-forward --address 127.0.0.1 service/ecg-platform-frontend 8081:80
```

Keep that terminal running. Open
[http://127.0.0.1:8081/login](http://127.0.0.1:8081/login) **on the new
laptop**. In a second terminal, `curl -I http://127.0.0.1:8081/login`
(Windows: `curl.exe -I http://127.0.0.1:8081/login`) should return `200`.
A `401` from `/api/v1/auth/me` before sign-in is expected. If
port `8081` is busy, forward `8082:80` and open port `8082` instead. This
loopback URL is not reachable from another computer or from Cloud Shell.

Read [local connection troubleshooting](localconnect.md) and the
[GKE deployment guide](docs/gke-deployment.md) if credentials, network access,
or the tunnel fail. Do not run the development seed against GKE.

## Route B: run a separate local development copy

Use Python 3.10+ (3.12 recommended), Node.js compatible with the project's
Node 22 tooling, and npm. A native PyTorch checkpoint at
`artifacts_final/model.pt` is required for AI readiness and inference;
TorchScript and ONNX files are not drop-in substitutes. Follow the
[cross-platform model guide](docs/model-export-cross-platform.md) to obtain
or train a compatible checkpoint. The expected numerical ECG input contract
is documented there; arbitrary MAT files or ECG images are **not** guaranteed
valid model inputs. Before starting the API, verify the private
`artifacts_final/model.pt` file exists on the new laptop; if it does not,
obtain a permitted compatible checkpoint first.

### macOS or Linux shell

From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
export AI_MODEL_PATH="$PWD/artifacts_final/model.pt"
export AI_MODEL_VERSION=local-research-checkpoint
ecg-health-api --project "$PWD" --host 127.0.0.1 --port 8080
```

In another terminal opened at the repository root, activate the same virtual
environment and create fake development users. The command prompts for a new
12+ character password:

```sh
source .venv/bin/activate
ecg-health-seed --project "$PWD"
```

In a third terminal opened at the repository root:

```sh
cd frontend
npm ci
npm run dev
```

### Windows PowerShell

From the repository root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
$env:AI_MODEL_PATH = Join-Path (Get-Location).Path 'artifacts_final\model.pt'
$env:AI_MODEL_VERSION = 'local-research-checkpoint'
ecg-health-api --project (Get-Location).Path --host 127.0.0.1 --port 8080
```

In a second PowerShell terminal opened at the repository root, activate the
environment and run
`ecg-health-seed --project (Get-Location).Path`; enter a new local-only
password when prompted. In a third terminal, run `cd frontend`, `npm ci`,
then `npm run dev`. If PowerShell blocks activation, use the approved
per-process execution-policy procedure in the
[cross-platform guide](docs/model-export-cross-platform.md), not a permanent
system-wide policy change.

Open [http://localhost:5173](http://localhost:5173). Verify
`http://127.0.0.1:8080/healthz` and `http://127.0.0.1:8080/readyz` in another
terminal. `/healthz` checks the API process; `/readyz` checks the configured
model file, **not** clinical validity. The local database and private files
are created under Git-ignored `var/`. The seeded `example.test` accounts are
fake and use the password you entered, not any deployed account password.

For the PostgreSQL/Redis/MinIO Docker development stack instead, follow
[the Compose instructions](README.md#run-the-compose-development-stack).
Create `.env` from `.env.example` with new local secrets; do not reuse cloud
secrets. The legacy workbench (`ecg-gui`, port `8765`) is a different app.
