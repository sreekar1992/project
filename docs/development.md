# Development workflow

## Prerequisites

- Python 3.10 or later and a virtual environment.
- A compatible local model artifact, normally `artifacts_final/model.pt`, to run upload validation and inference.
- Node.js 22-compatible tooling for the React client.
- Docker/Compose only when exercising the PostgreSQL, Redis, MinIO, and containerized services.

Install the Python package from the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Start a local API using safe development defaults:

```sh
AI_MODEL_PATH="$PWD/artifacts_final/model.pt" \
  ecg-health-api --project "$PWD" --host 127.0.0.1 --port 8080
```

The server creates a local SQLite schema only in development/test mode. For a PostgreSQL environment, set `DATABASE_URL`, required secrets, object-storage settings, and run `alembic upgrade head` before serving requests.

## Fake development fixtures

The seed command makes explicitly fake users and a demo organization. It prompts for a 12+ character development password and does not print or commit it:

```sh
ecg-health-seed --project "$PWD"
```

Do not use the seed command, seed identities, local database, or local object storage with real patient data.

## Frontend

```sh
cd frontend
npm install
npm run dev
```

The Vite server runs on port 5173 and proxies `/api` to port 8080 unless `VITE_API_PROXY_TARGET` or `VITE_API_BASE_URL` changes it. `npm run build` runs TypeScript checking and creates the production static bundle. The client contains no sample patients or fabricated model outputs; it expects an authorized API.

## Tests and checks

Run the applicable test suite after changes, for example:

```sh
python -m unittest discover -s tests -v
cd frontend && npm run build
```

For schema changes, generate/review an Alembic revision, apply it to a disposable PostgreSQL database, and test upgrade/downgrade behavior. For AI changes, preserve the existing raw-waveform model contract or version the adapter/checkpoint together; do not silently reinterpret a saved model artifact.

## Working conventions

Keep the legacy workbench and clinical platform entry points separate. Do not hard-code credentials, patient data, model paths, storage keys, or environment secrets. Use fake or de-identified records in tests and screenshots. Treat generated reports, Grad-CAM images, and logs as potentially sensitive artifacts even in development.
