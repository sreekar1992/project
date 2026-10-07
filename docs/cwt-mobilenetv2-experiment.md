# CWT-MobileNetV2 experimental classifier

This is a separate, offline **research experiment**. It is not loaded by the
clinical API and does not replace the existing one-dimensional RAMNV2
checkpoint at `artifacts_final/model.pt`.

Its result is an uncalibrated 17-class model score, not a disease probability,
diagnosis, triage result, treatment recommendation, prescription, or clinical
performance claim. A clinician must not use it autonomously.

## What the command trains

`ecg-train-cwt-mobilenetv2` reads the supplied `MLII/` class-directory tree:

```text
MLII/
  1 NSR/*.mat
  2 APB/*.mat
  ...
  17 PR/*.mat
```

Each MAT fragment is loaded through `ecg_cvd.data.load_directory_dataset`. Its
numerical waveform is filtered and normalized with the explicit
`ecg_cvd.data.preprocess` contract, transformed to a CWT magnitude image, then
repeated identically into three channels for a locally implemented PyTorch
MobileNetV2-style classifier.

The native CWT implementation uses only NumPy and SciPy: explicit complex
Morlet convolution kernels plus `scipy.signal.resample`. It does **not** import
or require PyWavelets, OpenCV, torchvision, or a generic-image pretrained
model. The transform's scales, image resizing, intensity normalization, RGB
conversion, and dependency statement are recorded in every artifact manifest.

## Important evaluation limitation

The command uses a deterministic stratified **fragment-level** 80/20 split by
default. It is useful for a preliminary, reproducible experiment only. The
source records may contain fragments from the same record or person, so train
and validation can share correlated material. The metrics must never be
presented as patient-level, clinical, deployment, or generalization
performance. A patient/record-grouped, held-out external evaluation is needed
before any broader research claim.

To address the strongly imbalanced MLII class distribution, the default loss is
inverse-frequency weighted cross entropy. Weights are calculated from training
fragments only, capped at 5.0, and saved in the manifest. They do not alter the
validation split or make validation scores clinically meaningful.

## Preflight first

Use an empty, new artifact directory. `--dry-run` validates the data layout,
label map, 17-class split, CWT input contract, and architecture metadata. It
writes a label map, split indexes, and a manifest but deliberately writes no
weights or metrics.

```bash
cd /Users/sreekarvarma/Documents/project
source .venv/bin/activate

ecg-train-cwt-mobilenetv2 \
  --data MLII \
  --artifacts artifacts/cwt-mobilenetv2-preflight-v1 \
  --version cwt-mobilenetv2-preflight-v1 \
  --dry-run
```

## Full experimental training

The default architecture is full-width MobileNetV2 with 224×224 images. On a
CPU it can be slow. On Apple Silicon, let `--device auto` select MPS if the
installed PyTorch runtime supports it; otherwise use CPU. Do not reuse an
existing artifact directory: the command refuses overwrites so a checkpoint
and its manifest cannot be silently mixed.

```bash
ecg-train-cwt-mobilenetv2 \
  --data MLII \
  --artifacts artifacts/cwt-mobilenetv2-experimental-v1 \
  --version cwt-mobilenetv2-experimental-v1 \
  --epochs 30 \
  --batch-size 8 \
  --device auto
```

For a short engineering smoke test only—not a meaningful experiment—use a
different versioned directory and reduce both image/model capacity and epochs:

```bash
ecg-train-cwt-mobilenetv2 \
  --data MLII \
  --artifacts artifacts/cwt-mobilenetv2-smoke-v1 \
  --version cwt-mobilenetv2-smoke-v1 \
  --epochs 1 \
  --batch-size 4 \
  --image-size 96 \
  --analysis-samples 360 \
  --number-of-scales 12 \
  --maximum-scale 24 \
  --width-multiplier 0.25 \
  --device cpu
```

The smoke artifact has a different input and architecture contract and cannot
be compared directly with the 224×224 experiment.

## Output artifact contract

A completed run writes the following files to the requested versioned output
directory:

| File | Purpose |
| --- | --- |
| `cwt_mobilenetv2_experimental.pt` | CPU-portable PyTorch state dictionary plus architecture, CWT, split, and safety metadata. |
| `label_map.json` | The authoritative persisted mapping from MLII directory labels to output indexes. |
| `metrics.json` | Fragment-level accuracy, weighted/macro metrics, per-class report, confusion matrix, and training-loss history. |
| `manifest.json` | Full provenance: file-content fingerprint, split seed/index file, preprocessing, CWT image contract, architecture, package versions, code digests, class weighting, and safety caveat. |
| `split_indices.npz` | Exact training and validation fragment indexes used for the run. |

`label_map.json`, `manifest.json`, and `metrics.json` must travel with the
checkpoint. The checkpoint alone is not enough to reconstruct a valid input
image or interpret its output order.

## Reproducibility and next validation step

The command seeds Python, NumPy, PyTorch, and its zero-worker DataLoader. It
requests PyTorch deterministic algorithms and records package/OS versions and
hashes of the training, CWT-transform, and architecture modules. Exact
bit-for-bit equality still depends on identical PyTorch, OS, CPU/GPU, driver,
and accelerator kernels.

Before even considering an integration review, keep this artifact separate and
perform a record/patient-grouped evaluation plus an external held-out test.
Compare that result against RAMNV2 with the same label mapping and prespecified
protocol. Do not point `AI_MODEL_PATH` at this experimental checkpoint: the
clinical adapter expects the current 1-D RAMNV2 artifact format, not CWT RGB
images.
