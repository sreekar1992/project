# REST API reference

The clinical API is versioned under `/api/v1`. JSON endpoints require `application/json`; ECG upload uses `multipart/form-data`. Protected endpoints require `Authorization: Bearer <access-token>`. The server, not the client, determines permissions and tenant scope.

Every response receives `X-Request-ID`; callers may provide an 8–64 character `X-Request-ID` for correlation. Error responses use this shape:

```json
{"error": {"code": "ERROR_CODE", "message": "Safe client message", "request_id": "..."}}
```

The service does not return raw tracebacks, private storage paths, or raw ECG bytes in error bodies.

## Health and authentication

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/healthz` | Process health; no model guarantee |
| `GET` | `/readyz` | Ready only when a compatible configured model file exists |
| `POST` | `/api/v1/auth/login` | Login; returns access-token metadata and sets refresh cookie |
| `POST` | `/api/v1/auth/refresh` | Rotates a valid refresh token from cookie or JSON body |
| `POST` | `/api/v1/auth/logout` | Revokes a supplied/current refresh token when possible |
| `GET` | `/api/v1/auth/me` | Current principal identity, tenant, and roles |

## Core workflow routes

| Area | Routes |
| --- | --- |
| Dashboard | `GET /dashboard`, `GET /admin/dashboard` |
| Hospital administration | `GET, POST /admin/hospitals`; `GET, PATCH /admin/hospitals/{organization_id}`; `GET, POST /admin/users`; `GET, POST, PATCH /admin/features`; `GET /admin/ai-models`; `GET /admin/audit-logs` |
| Patient identity | `POST /patients/matches`; `GET, POST /patients`; `GET /patients/{patient_id}`; `GET /patients/{patient_id}/identifiers` |
| Encounters | `GET /encounters?patient_id=...`; `POST /encounters`; `GET /encounters/{encounter_id}`; `GET /patients/{patient_id}/encounters` |
| ECG assets | `GET /ecgs?patient_id=...`; `POST /ecgs` or `/ecgs/upload`; `GET /ecgs/{ecg_id}`; `GET /ecgs/{ecg_id}/file`; `GET /ecgs/{ecg_id}/waveform`; `GET /ecgs/{ecg_id}/download-url` |
| AI analysis | `POST /ecgs/{ecg_id}/analyze` or `POST /analyses`; `GET /ecgs/{ecg_id}/analysis`; `GET /analyses?patient_id=...`; `POST /ecgs/{ecg_id}/explain`; `GET /ecgs/{ecg_id}/explain.png` |
| Clinician review | `POST /ecgs/{ecg_id}/review`; `POST /reviews`; `GET /reviews?patient_id=...` |
| Reports | `GET /reports?patient_id=...`; `GET /reports/{report_id}`; `POST /reports/{report_id}/generate-pdf`; `GET /reports/{report_id}/download` |
| Audit and compatibility | `GET /audit`; `GET /fhir/{resource_type}/{resource_id}` |

`POST /ecgs` needs an existing `encounter_id`/`encounter_uuid`, plus a `file` field. The current adapter accepts `.mat` and `.csv` waveform files only. It validates the file against the existing ECG reader before persisting it.

`POST /ecgs/{ecg_id}/review` accepts a clinician assessment and may include an optional clinician-authored diagnosis, note, prescription instructions, and prescription items. It is not an AI treatment endpoint.

## Analysis behavior

Analysis responses include the status, predicted label/name, uncalibrated confidence, top predictions, model version and artifact hash, timing, and whether a stored Grad-CAM image exists. They also include an explicit research/clinician-review safety statement. With asynchronous analysis enabled, creation returns `202`; otherwise it returns a completed result when inference succeeds.

`GET /ecgs/{ecg_id}/waveform` returns the exact authorized, validated single-lead samples and acquisition metadata for the client-side viewer; it is audited and is not a public chart endpoint. The API supports authorized direct file downloads. S3-backed storage may instead return a short-lived presigned URL from `/download-url`; local storage is always streamed by Flask after authorization.

## Compatibility and contract notes

The React client supports the response aliases `id`, `patient_id`, `encounter_id`, and similar API-facing fields. The documented API contract should be treated as the application contract, not as a public, stable third-party integration guarantee yet. There is no OpenAPI document, pagination envelope standard, idempotency-key support, webhook contract, or FHIR conformance statement at this stage.
