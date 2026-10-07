# Run the ECG Research Clinical Platform Locally

This guide starts the complete local development environment: the Python API, the React web application, the configured ECG research model, and the demo users. It is for development and research with synthetic/demo data only; do not use it with real patient data or for clinical decisions.

## What you need

- Python 3.10 or later
- Node.js 20 or later (Node.js 22 is recommended)
- The research model at `artifacts_final/model.pt`

## macOS or Linux

### 1. Create the Python environment (first time only)

```bash
cd /Users/sreekarvarma/Documents/project
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

If the `.venv` folder already exists, activate it and run `pip install -e .` only when dependencies have changed.

### 2. Start the API

Open a terminal and run:

```bash
cd /Users/sreekarvarma/Documents/project
source .venv/bin/activate
export AI_MODEL_PATH="$PWD/artifacts_final/model.pt"
export AI_MODEL_VERSION="local-research-checkpoint"
ecg-health-api --project "$PWD" --host 127.0.0.1 --port 8080
```

Keep this terminal running. The API is available at `http://127.0.0.1:8080`.

### 3. Start the web application

Open a second terminal and run:

```bash
cd /Users/sreekarvarma/Documents/project/frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

Open [http://127.0.0.1:5173/](http://127.0.0.1:5173/) in your browser.

### 4. Create or reset demo users

Run this only when you need a fresh local demo database or need to reset the development password:

```bash
cd /Users/sreekarvarma/Documents/project
source .venv/bin/activate
ecg-health-seed --project "$PWD"
```

Choose a development password of at least 12 characters when prompted. The seeded demo accounts share that password:

| Role | Demo email |
| --- | --- |
| Super administrator | `superadmin@example.test` |
| Hospital administrator | `hospitaladmin@example.test` |
| Doctor | `doctor@example.test` |
| Technician | `technician@example.test` |
| Patient | `patient@example.test` |

## Windows setup (PowerShell)

Use two PowerShell windows: one for the Python API and one for the React web application. Install Python 3.10+ and Node.js 20+ before continuing. Node.js 22 is recommended.

> If PowerShell blocks virtual-environment activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that PowerShell window, then run the activation command again. This affects only the current terminal session.

### 1. Create the Python environment (first time only)

```powershell
cd C:\path\to\project
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

If `.venv` already exists, skip the `py -m venv .venv` command and activate it instead.

### 2. Start the API

```powershell
cd C:\path\to\project
.\.venv\Scripts\Activate.ps1
$env:AI_MODEL_PATH = "$PWD\artifacts_final\model.pt"
$env:AI_MODEL_VERSION = "local-research-checkpoint"
ecg-health-api --project "$PWD" --host 127.0.0.1 --port 8080
```

### 3. Start the web application in a second PowerShell window

```powershell
cd C:\path\to\project\frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

Then open [http://127.0.0.1:5173/](http://127.0.0.1:5173/).

### 4. Create or reset Windows demo users

In another PowerShell window, run:

```powershell
cd C:\path\to\project
.\.venv\Scripts\Activate.ps1
ecg-health-seed --project "$PWD"
```

Enter a development password of at least 12 characters. Use the same demo emails listed in the macOS/Linux section above.

## Verify the local services

From a third terminal, run:

```bash
curl http://127.0.0.1:8080/healthz
curl http://127.0.0.1:8080/readyz
```

Expected responses include `"status":"ok"` from `healthz` and `"status":"ready"` from `readyz`.

If `readyz` reports that the model is not configured, stop the API and start it again after setting `AI_MODEL_PATH` to the absolute path of `artifacts_final/model.pt`.

## Local-use notes

- Restarting the API invalidates local browser sessions. Sign in again after a restart.
- Native `.mat` and `.csv` records are numerical ECG inputs. A `.jpg` or `.jpeg` ECG is retained as an image and can only be digitized through the explicit experimental, research-only workflow.
- The active platform model is the 17-class RAMNV2 research classifier. Its displayed score is an uncalibrated research score, not a diagnosis or disease probability.
- The CWT MobileNetV2 model is a separate experimental artifact; see `docs/cwt-mobilenetv2-experiment.md`. It is not the active clinical-platform model.
- Press `Ctrl+C` in each server terminal to stop the local services.
