# ECG Clinical Research Platform — Web Client

This is a separate React + TypeScript client for the ECG clinical **research and clinician-review** workflow. It contains no sample patients, hard-coded ECG records, or simulated predictions. All displayed clinical data is loaded from the configured REST API.

## Start locally

```bash
cd frontend
npm install
npm run dev
```

Vite serves the client at `http://localhost:5173` and proxies `/api` to `http://localhost:8080` by default. Copy `.env.example` to `.env.local` to point to another backend or set `VITE_API_BASE_URL` directly.

```bash
npm run build
```

## Expected API contract

The client sends requests below `/api/v1` by default. It expects JWT-like access tokens from `POST /auth/login` and `POST /auth/refresh`; a refresh token should be kept in a secure, HttpOnly cookie by the server. The access token is kept only in browser session storage.

Core resource routes:

- `GET /dashboard`
- `GET, POST /patients` and `GET /patients/:id`
- `GET, POST /encounters`
- `GET, POST /ecgs` (the create request uses `multipart/form-data` with a `file` field)
- `GET /ecgs/:id/waveform` for the authorized in-browser waveform viewer
- `GET, POST /analyses`
- `GET /ecgs/:id/explain.png` for the authorized stored Grad-CAM visualization
- `GET, POST /reviews`
- `GET /reports`

List responses may be arrays or objects with an `items`, `data`, or `results` array. The server remains the source of truth for role checks, tenant isolation, audit events, consent, retention, and clinician sign-off.

## Clinical safety boundary

The interface deliberately describes AI output as a research result that requires qualified clinician review. It does not provide automated diagnosis, treatment selection, triage, medication, or emergency guidance. Do not deploy it with real patient data until the backend has appropriate security controls, validation, governance, and institutional approval.
