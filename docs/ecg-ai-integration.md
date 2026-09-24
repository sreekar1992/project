# ECG AI integration

## Real model adapter

`ECGModelAdapter` wraps the existing repository model rather than fabricating a result. It loads a configured checkpoint from `AI_MODEL_PATH`, verifies its label-map metadata, loads weights with PyTorch `weights_only=True`, and calculates a SHA-256 artifact fingerprint. The checkpoint is registered in `ai_models` and `ai_model_versions` with its discovered preprocessing/metrics metadata.

The adapter reuses the existing project functions:

- `read_signal` for `.mat`/`.csv` parsing and validation;
- the baseline filtering, median/MAD normalization, and model-input conversion;
- the `RAMNV2` one-dimensional waveform classifier; and
- Grad-CAM for a saved PNG explanation.

It accepts numerical waveform files, not raster ECG images. The baseline input contract is the existing 10-second, 3,600-sample MLII-style waveform transformed to the checkpoint-compatible 900-sample model input. A different lead arrangement, sampling protocol, acquisition device, or preprocessing pipeline requires separate compatibility and validation work.

## Analysis lifecycle

1. The upload service validates a `.mat` or `.csv` waveform before writing it to private storage.
2. It creates an `ECGRecord` and `ECGFile`, including an input SHA-256.
3. An authorized analysis creates an `AIAnalysis` tied to the exact model-version record.
4. Inference records the top three labels, uncalibrated score, timing, raw model output, and optional Grad-CAM asset.
5. The service marks the record for review. A clinician can then document an independent assessment and optional care records.

When Redis/RQ is enabled, the queued job contains identifiers only; the worker reloads the authorized waveform from private storage. If Redis is not configured, the API runs the analysis synchronously.

## Interpretation boundary

An analysis response always says that it is a research/clinical-decision-support result requiring qualified clinician review. The score is not a calibrated disease probability. The classifier does not produce medication, dosage, treatment, triage, emergency, or autonomous diagnostic recommendations. Grad-CAM is a model visualization, not clinical evidence or causal explanation.

The current model was trained on the supplied 1,000-fragment research dataset. Its reported fragment-level validation split may contain recordings from the same person across subsets and is not a clinical performance estimate. The model must not be represented as validated for patients, devices, hospitals, leads, demographics, or conditions beyond an appropriately governed validation study.

## Model operations

Use immutable checkpoint paths/digests, retain the model artifact hash with each analysis, and do not replace an artifact at the same path without version governance. A production model-management process would also need approval, testing, drift monitoring, calibration assessment, patient-level/external validation, rollback controls, and post-market monitoring; those workflows are not implemented here.
