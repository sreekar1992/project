# ECG AI integration

## Real model adapter

`ECGModelAdapter` wraps the existing repository model rather than fabricating a result. It loads a configured checkpoint from `AI_MODEL_PATH`, verifies its label-map metadata, loads weights with PyTorch `weights_only=True`, and calculates a SHA-256 artifact fingerprint. The checkpoint is registered in `ai_models` and `ai_model_versions` with its discovered preprocessing/metrics metadata.

The adapter reuses the existing project functions:

- `read_signal` for `.mat`/`.csv` parsing and validation;
- the baseline filtering, median/MAD normalization, and model-input conversion;
- the `RAMNV2` one-dimensional waveform classifier; and
- Grad-CAM for a saved PNG explanation.

It accepts numerical waveform files, not raster ECG images. The clinical upload route may retain a validated `.jpg`/`.jpeg` as an **image-only source artifact**, but it never passes the image itself to this adapter. An explicitly confirmed, quality-gated research workflow can create a separate numerical derivative from a narrowly supported JPEG chart; only that derivative is passed to RAMNV2. The baseline input contract is the existing 10-second, 3,600-sample MLII-style waveform transformed to the checkpoint-compatible 900-sample model input. A different lead arrangement, sampling protocol, acquisition device, preprocessing pipeline, or a separately trained image model requires separate compatibility and validation work.

## Analysis lifecycle

1. The upload service validates a `.mat` or `.csv` waveform before writing it to private storage. It can also store a server-decoded JPEG image as `IMAGE_ONLY`; an image is not a normal analysis input.
2. It creates an `ECGRecord` and `ECGFile`, including an input SHA-256.
3. An authorized analysis creates an `AIAnalysis` tied to the exact model-version record.
4. Inference records the top three labels, uncalibrated score, timing, raw model output, and optional Grad-CAM asset.
5. The service marks the record for review. A clinician can then document an independent assessment and optional care records.

Before analysis, users with the `ecg.analyze` permission can call `GET /api/v1/ai/model-info` to inspect server-derived model provenance. The local configuration reports **RAMNV2 ECG research classifier**, `local-research-checkpoint`, and **PyTorch** without exposing a local checkpoint path. The endpoint is metadata only; it does not make JPEG source images classifier inputs or make the research model clinically validated.

When Redis/RQ is enabled, the queued job contains identifiers only; the worker reloads the authorized waveform from private storage. If Redis is not configured, the API runs the analysis synchronously.

## Explicit JPEG-to-waveform research workflow

The patient-recording page exposes **Convert & analyze** only for an authorized JPEG source and an authorized research-model user. The user selects CSV or MAT output and must acknowledge that the process is experimental. The client calls:

```http
POST /api/v1/ecgs/{source-ecg-id}/digitize
Content-Type: application/json

{"confirm_experimental": true, "output_format": "csv"}
```

This is intentionally separate from upload and from `POST /ecgs/{id}/analyze`; it prevents a JPEG from being silently treated as a model input. The service:

1. authorizes the image record, decrypts it only for the request, and verifies its stored SHA-256;
2. runs strict, fail-closed quality gates for a clean, mostly neutral, single-trace chart and rejects uncertain input rather than inventing a waveform;
3. estimates a 3,600-sample, 10-second/360 Hz **research representation** from the accepted trace, then validates the generated CSV/MAT using the same parser as ordinary numerical uploads;
4. persists a new, separately encrypted record with `source_kind: JPEG_DIGITIZED_WAVEFORM`, a source-record link, checksums, audit entries, and `EXPERIMENTAL_JPEG_TRACE_DIGITIZATION` provenance; and
5. runs one synchronous RAMNV2 analysis on that derivative and returns its uncalibrated result, provenance, quality measurements, and limitations.

The source JPEG remains unchanged and encrypted at rest. Under the local storage adapter, both source and derivative use AES-256-GCM authenticated encryption with a fresh nonce per object; cloud storage follows the configured provider storage-encryption policy. The derivative is not an encryption/decryption result, a lossless conversion, or an original acquisition. CSV versus MAT only chooses its stored container.

This feature intentionally rejects blank images, interface screenshots, multi-panel/non-chart images, and photographs or scans whose trace cannot be isolated with sufficient confidence. A screenshot of the application UI is therefore not a valid ECG chart source. Rejection is the expected safe outcome for ambiguous input; it does not mean the image represents a negative ECG finding.

The derived trace can be viewed and downloaded as a distinct research artifact. It must be labelled as image-derived in any downstream report or export. Its inferred samples have unknown lead identity, amplitude calibration, timing accuracy, and diagnostic fidelity, so neither the waveform nor RAMNV2's score may be used for diagnosis, triage, treatment, or urgent clinical decisions.

## Interpretation boundary

An analysis response always says that it is a research/clinical-decision-support result requiring qualified clinician review. The score is not a calibrated disease probability. The classifier does not produce medication, dosage, treatment, triage, emergency, or autonomous diagnostic recommendations. Grad-CAM is a model visualization, not clinical evidence or causal explanation.

The current model was trained on the supplied 1,000-fragment research dataset. Its reported fragment-level validation split may contain recordings from the same person across subsets and is not a clinical performance estimate. The model must not be represented as validated for patients, devices, hospitals, leads, demographics, or conditions beyond an appropriately governed validation study.

## Model operations

Use immutable checkpoint paths/digests, retain the model artifact hash with each analysis, and do not replace an artifact at the same path without version governance. A production model-management process would also need approval, testing, drift monitoring, calibration assessment, patient-level/external validation, rollback controls, and post-market monitoring; those workflows are not implemented here.
