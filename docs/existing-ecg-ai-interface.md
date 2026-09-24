# Existing ECG AI interface

## Scope and boundary

The existing implementation in `src/ecg_cvd` is a single-lead ECG **research
classifier**.  It must not be presented as a clinical diagnosis, disease
probability, treatment recommendation, or validated clinical decision system.
The platform integration records its output as an immutable, model-generated
observation that requires qualified clinician review.

The legacy local workbench (`ecg_cvd.gui:create_app`) remains a separate,
localhost-only research tool.  The hospital-platform API must not reuse its
demo encryption password, in-memory job state, or unauthenticated routes.

## Accepted ECG input

The compatibility adapter accepts exactly the formats and signal contract
validated by [`ecg_cvd.gui.read_signal`](../src/ecg_cvd/gui.py):

| Input | Accepted form |
| --- | --- |
| MATLAB | `.mat` containing a numeric `val` array, or exactly one numeric one-dimensional array with 3,600 values |
| CSV | One comma-delimited numeric waveform with 3,600 values |
| Signal | One finite, non-flat, real-valued, single-lead waveform |
| Acquisition contract | 10 seconds at 360 Hz (3,600 samples) |

Other leads, sampling rates, durations, images, and malformed MATLAB files are
rejected instead of silently being resampled or interpreted as an ECG.

## Preprocessing and model contract

The integration preserves the checkpoint-compatible pipeline in
[`ecg_cvd.data`](../src/ecg_cvd/data.py):

1. `preprocess(raw[None])` applies a fourth-order zero-phase 0.5--45 Hz
   Butterworth band-pass filter and normalizes each trace with median/MAD.
2. `model_input(...)` takes every fourth sample, producing a 900-sample
   90 Hz normalized trace.
3. `RAMNV2(num_classes)` from [`ecg_cvd.model`](../src/ecg_cvd/model.py)
   receives a tensor with shape `(batch, 1, 900)` and returns class logits.
4. Softmax converts logits to model scores.  These scores are not calibrated
   clinical probabilities.

The current checkpoint format, written by
[`ecg_cvd.train`](../src/ecg_cvd/train.py), contains `state_dict`,
`num_classes`, and `label_map` (plus training/split/preprocessing metadata in
newer checkpoints).  `label_map` is ordered by its numeric index; the adapter
does **not** use the presentation-only `ecg_cvd.LABELS` constant to infer an
order.

## Reused implementation

| Platform responsibility | Existing source reused |
| --- | --- |
| File validation | `ecg_cvd.gui.read_signal(data, filename)` |
| Signal preprocessing | `ecg_cvd.data.preprocess`, `ecg_cvd.data.model_input` |
| Model construction | `ecg_cvd.model.RAMNV2` |
| Explanation | `ecg_cvd.explain.grad_cam(model, normalized_signal, class_index, output, fs=90.0)` |
| Class display names | `ecg_cvd.gui.class_name` |

`grad_cam` writes a PNG overlay for the current RAMNV2 last residual block.
It is model-generated explanatory material, not a causal explanation or
clinical finding.

## Actual prediction result

The existing local route `/api/analyze` computes a report with the following
fields: `sample_name`, `label`, `label_name`, `confidence`,
`reference_label`, `model`, `samples`, `sampling_rate_hz`,
`duration_seconds`, `model_sampling_rate_hz`, `top_predictions`, and `note`.
It also generates an in-memory Grad-CAM PNG.  The platform adapter uses the
same inference calculation, retains the checkpoint's model/version/checksum,
and persists the actual top predictions.  It never fabricates a diagnosis or
metric.

## Integration plan

1. Keep `src/ecg_cvd` and the `ecg-gui` workbench compatible and unchanged.
2. Add a small hospital-platform inference adapter that calls the functions
   above rather than copying the model logic or calling the legacy HTTP API.
3. Store the uploaded ECG file and explanation as private assets; store only
   URI, checksum, metadata, and model-run records in the relational database.
4. Require a clinician-authored review before a diagnosis, note, prescription,
   or report is treated as a clinical document.
5. Keep model training, adversarial/camouflage experiments, and the Mendeley
   research dataset in a separate research workflow from clinical records.

## Known limits carried into the platform

The available 1,000-fragment dataset uses a fragment-level evaluation split;
the saved 87% validation figure is not a patient-disjoint clinical performance
estimate.  It must not be displayed as clinical accuracy.  The platform will
show only metrics actually packaged with a registered model and will label all
AI results as research/clinical-decision-support output requiring review.
