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
| ECG assets | `GET /ecgs?patient_id=...`; `POST /ecgs` or `/ecgs/upload`; `GET /ecgs/{ecg_id}`; `GET /ecgs/{ecg_id}/file`; `GET /ecgs/{ecg_id}/waveform`; `GET /ecgs/{ecg_id}/download-url`; `GET /ecgs/{ecg_id}/security`; `POST /ecgs/{ecg_id}/digitize` |
| AI analysis | `GET /ai/model-info`; `POST /ecgs/{ecg_id}/analyze` or `POST /analyses`; `GET /ecgs/{ecg_id}/analysis`; `GET /analyses?patient_id=...`; `GET /ecgs/{ecg_id}/assessment-suggestion`; `POST /ecgs/{ecg_id}/explain`; `GET /ecgs/{ecg_id}/explain.png` |
| Medicine-name catalogue | `GET /medicines?query=<prefix>&limit=8` |
| Clinician review | `POST /ecgs/{ecg_id}/review`; `POST /reviews`; `GET /reviews?patient_id=...` |
| Reports | `GET /reports?patient_id=...`; `GET /reports/{report_id}`; `POST /reports/{report_id}/generate-pdf`; `GET /reports/{report_id}/download` |
| Audit and compatibility | `GET /audit`; `GET /fhir/{resource_type}/{resource_id}` |

`POST /ecgs` needs an existing `encounter_id`/`encounter_uuid`, plus a `file` field. It accepts validated `.mat` and `.csv` waveform files, plus server-decoded `.jpg`/`.jpeg` source images. A JPEG is stored as an `IMAGE_ONLY` record (`source_kind: ECG_IMAGE`, `content_type: image/jpeg`) after magic-byte, decoder, and pixel-limit validation; the server does not trust the browser MIME type or filename alone. New local assets, including source images and derived files, use authenticated AES-256-GCM storage with a fresh nonce per object.

An image-only record can be authorized for download and integrity/security inspection, but `GET /ecgs/{ecg_id}/waveform` and the normal AI-analysis routes return `409 ECG_IMAGE_ONLY`. The configured RAMNV2 model never receives the JPEG itself. The only image-to-model path is the explicit, confirmation-gated experimental endpoint described below.

`GET /ai/model-info` is a read-only provenance endpoint for users with the `ecg.analyze` permission (normally doctors and technicians; the platform super administrator also has the wildcard permission). It reports the server-derived model name, version, framework, artifact fingerprint, preprocessing contract, and the JPEG image-only boundary without exposing the checkpoint's filesystem path. It reports configuration metadata, not clinical validation or treatment advice.

`GET /ecgs/{ecg_id}/assessment-suggestion` requires the clinician-only
`diagnosis.create` permission. It returns an editable research observation based
only on the stored completed RAMNV2 result and its uncalibrated score. It does
not create a review and it never supplies a diagnosis, triage, medicine, dose,
route, frequency, timing, duration, or prescription recommendation.

`GET /medicines?query=<prefix>&limit=8` requires the doctor-only
`medication.catalog.read` permission. It searches the locally indexed,
separately installed medicine-name source and returns at most 20 display-name
matches plus limited source composition/form metadata when available. It does
not return dose, interaction, contraindication, route, or treatment advice.

`POST /ecgs/{ecg_id}/review` accepts a clinician assessment and may include an optional clinician-authored diagnosis, note, prescription instructions, and prescription items. It is not an AI treatment endpoint.

## Experimental JPEG trace digitization

`POST /ecgs/{ecg_id}/digitize` is a narrow research workflow for a previously uploaded, authorized `IMAGE_ONLY` JPEG. It requires `ecg.analyze` permission and an explicit acknowledgement:

```json
{
  "confirm_experimental": true,
  "output_format": "csv"
}
```

`output_format` may be `csv` (the default) or `mat`. The value changes only the persisted representation of the separate numerical derivative; it does not make the extraction more accurate or clinically valid. A missing/false acknowledgement or unsupported format is rejected. A non-JPEG or normal numerical ECG source returns `409 DIGITIZATION_SOURCE_REQUIRED`.

Before extraction, the service decrypts the authorized source in memory and verifies its SHA-256 checksum. It then applies deliberately conservative image-quality gates intended only for a clean, simple, single-trace chart on a mostly neutral background. Inputs such as blank images, dashboard/browser screenshots, multi-panel records, photographs/scans with ambiguous traces, broad coloured panels, short traces, or non-chart images are rejected with `422 ECG_IMAGE_DIGITIZATION_REJECTED`; no derivative or model analysis is created for a rejected source.

On success, the service produces exactly 3,600 estimated samples at the checkpoint's 360 Hz/10-second input shape, serializes them as the requested CSV or MAT file, and revalidates that file through the normal numerical waveform parser. It then writes a new, separately encrypted `JPEG_DIGITIZED_WAVEFORM` record. The source JPEG is never overwritten or converted in place. The derived record and its analysis carry a provenance object such as:

```json
{
  "source_ecg_id": "<source-uuid>",
  "derived_ecg": {
    "source_kind": "JPEG_DIGITIZED_WAVEFORM",
    "provenance": {
      "kind": "EXPERIMENTAL_JPEG_TRACE_DIGITIZATION",
      "source_ecg_id": "<source-uuid>",
      "research_only": true
    }
  },
  "digitization": {
    "status": "COMPLETED",
    "source_format": "image/jpeg",
    "output_format": "csv",
    "source_integrity_verified": true
  }
}
```

The endpoint runs one RAMNV2 analysis of the **derived numerical signal** before returning `201 Created`; it does not send the JPEG to RAMNV2. Its result is an uncalibrated research score, not a diagnosis, treatment recommendation, or a restoration of the original device waveform. The response includes quality/provenance metadata and explicit limitations because calibration, lead identity, amplitude units, timing, and diagnostic fidelity cannot be recovered reliably from chart pixels.

## Analysis behavior

Analysis responses include the status, predicted label/name, uncalibrated confidence, top predictions, model version and artifact hash, timing, and whether a stored Grad-CAM image exists. They also include an explicit research/clinician-review safety statement. With asynchronous analysis enabled, creation returns `202`; otherwise it returns a completed result when inference succeeds.

`GET /ecgs/{ecg_id}/waveform` returns the exact authorized, validated single-lead samples and acquisition metadata for the client-side viewer; it is audited and is not a public chart endpoint. The API supports authorized direct file downloads. S3-backed storage may instead return a short-lived presigned URL from `/download-url`; local storage is always streamed by Flask after authorization.

## Compatibility and contract notes

The React client supports the response aliases `id`, `patient_id`, `encounter_id`, and similar API-facing fields. The documented API contract should be treated as the application contract, not as a public, stable third-party integration guarantee yet. There is no OpenAPI document, pagination envelope standard, idempotency-key support, webhook contract, or FHIR conformance statement at this stage.
