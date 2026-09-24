# ECG Workbench and Health-Platform Foundation

For the complete macOS and Windows setup, model training/export/import, local
platform, Docker Compose, Kubernetes-boundary, verification, and troubleshooting
guide, read [Cross-platform AI model export, import, and operations](docs/model-export-cross-platform.md).

Cloud-specific deployment runbooks are separate because the current Kubernetes
files are only scaffolding: [AWS deployment](docs/aws-deployment.md) and
[Azure deployment](docs/azure-deployment.md).

## ECG health platform (new, research/CDS only)

The repository now includes a separate authenticated, multi-hospital ECG
health-platform foundation alongside the original local research workbench.
It preserves the real `src/ecg_cvd` inference pipeline and records its output
as **AI-generated research/clinical-decision-support material requiring
qualified clinician review**. It does not turn a model result into a diagnosis,
treatment recommendation, prescription, or claim of clinical validation.

The new platform provides a Flask REST API, PostgreSQL/SQLAlchemy/Alembic
deployment path, tenant-scoped RBAC, EMPI/MRN patient registration, encounters,
private ECG assets, actual model inference/Grad-CAM, clinician-authored reviews,
PDF reports with the authorized waveform, FHIR-compatible read mappings, audit
events, an RQ worker, and a React client with an interactive authorized waveform
viewer. The legacy one-page workbench and its public demonstration
encryption password remain separate and must not be used for patient records.

Read the actual preserved model contract first: [existing ECG AI interface](docs/existing-ecg-ai-interface.md).
The platform documentation starts at [development](docs/development.md) and
[architecture](docs/architecture.md).

### Run the platform locally

Use a development database and private local asset directory; PostgreSQL,
MinIO, and Redis are not required for this first local smoke test.

```bash
cd /Users/sreekarvarma/Documents/project
source .venv/bin/activate
export AI_MODEL_PATH="$PWD/artifacts_final/model.pt"
export AI_MODEL_VERSION="local-research-checkpoint"
ecg-health-api --project "$PWD"
```

In a second terminal, run the React client:

```bash
cd /Users/sreekarvarma/Documents/project/frontend
npm install
npm run dev
```

Open `http://localhost:5173`. To create clearly fake development users, choose
your own non-production password and run:

```bash
ecg-health-seed --project /Users/sreekarvarma/Documents/project --password 'your-development-only-password'
```

The seed command never prints or commits that password. It creates only
`example.test` accounts and clearly fake data.

### Run the Compose development stack

```bash
cp .env.example .env
# Replace every placeholder in .env with unique local-development secrets.
docker compose up --build -d
docker compose exec -e ECG_PLATFORM_ENV=development backend \
  ecg-health-seed --password 'your-development-only-password'
```

The Compose migration service runs `ecg-health-migrate` before the API and
worker start; it applies Alembic revisions and creates only role, permission,
and feature reference rows—never users, hospitals, or patient data. Do not
bypass it or rely on automatic schema creation. The stack
mounts `./artifacts_final/model.pt` read-only. Do not add
MLII, actual clinical recordings, keys, or populated `.env` files to the image
or Git repository. The platform frontend is at `http://localhost:5173` and the
private backend API is at `http://localhost:8080`.

### Platform verification

```bash
MPLCONFIGDIR=/private/tmp/ecg-platform-mpl .venv/bin/python -m unittest tests.test_clinical_platform -v
cd frontend && npm run build
```

The platform test creates a temporary checkpoint and temporary fake clinical
records. It verifies a real adapter path from MAT upload through inference,
Grad-CAM, clinician review, report PDF, FHIR mapping, RBAC, and cross-hospital
isolation.

---

## Legacy ECG Workbench

A single-page local interface for your ECG research project. The default **camouflage + AES recovery** mode creates altered MAT waveforms intended to change the selected classifier's prediction, while storing an authenticated encrypted copy of the exact original inside each camouflaged MAT. Enter the fixed demonstration password `987654321` to recover the original and compare original, altered, and recovered predictions. AES-only encryption, adversarial stress tests, and model training remain available separately.

**Camouflage is not encryption or proven attack prevention.** The camouflaged MAT's `val` array is intentionally visible and may reveal information about the original signal. Its altered model prediction is a model-specific research result, not a privacy guarantee or protection against other models/adaptive attacks. AES protects the recovery payload. The transformation is not algebraically reversed: decryption recovers the original bytes stored in that payload, including original MATLAB metadata and data types.

**Demo security warning:** `987654321` is a weak, publicly specified password and is configured in the source code by request. This mode demonstrates encryption/decryption; it must not be used to protect private patient data. The earlier user-created-password workflow has been removed from the main UI.

## Open the GUI

On this Mac, double-click **Launch ECG Workbench.command** in Finder. It opens your browser and starts the server. Keep its terminal window open while using the app; press Ctrl+C to stop it.

Or run:

```bash
cd /Users/sreekarvarma/Documents/project
source .venv/bin/activate
ecg-gui
```

Open **http://127.0.0.1:8765**. If that port is occupied, use `ecg-gui --port 8766`.

All operations run on your computer. The server binds to the loopback address. Batch encryption saves encrypted MAT files and encrypted PNG images under `artifacts_dataset/<batch-id>/`, alongside a public catalog, completion summary and password-wrapped encryption key. Camouflage batches additionally contain `<record-id>.camouflaged.mat`: a public altered `val` array plus an encrypted original-file recovery payload. The random data-encryption key is not stored in plaintext. No plaintext original PNG is saved by this workflow. Downloads are saved where your browser normally saves them. **Download restored MAT (plaintext)** intentionally exports the recovered original only after password authentication and an explicit click. Training creates a new directory under `artifacts_gui/`.

Your original `MLII/` files, existing model, and earlier plaintext results are **not** overwritten or deleted. All dataset records receive separate encrypted copies; the originals remain plaintext. This is not operating-system access control. The localhost service is for one trusted local user, not shared or remote deployment. Passwords and decrypted content temporarily exist in process/browser memory; Python/browser runtimes do not guarantee secure memory erasure or prevent OS swap, screenshots, or developer-tool access.

## One-page workflow

1. **Camouflage and encrypt all:** select **camouflage + AES recovery**, choose the model and transformation budget, then start the batch. The algorithm changes each original raw 3,600-sample waveform, evaluates every candidate through the real preprocessing/classifier, and records whether the predicted label actually changed. It does not force labels or substitute another patient's waveform. The original MAT bytes and waveform PNG are encrypted, and a valid camouflaged MAT carries its visible altered waveform and encrypted original recovery payload. Every exact recovery is verified before the batch is marked complete. The default budget is 0.5 times the original filtered waveform's robust MAD scale, up to 20 iterations. The AES-only mode retains the earlier unmodified encrypted-copy workflow. Neither mode asks you to create an encryption password; a fresh random dataset key is wrapped using the fixed demonstration password.
2. **Select and decrypt:** choose an encrypted batch, a recording, and a model. Before entering a password, the results panel displays a noise-like visualization of the selected recording's actual encrypted image bytes. This replaces the lock illustration; it is not the original waveform, a prediction, or an authenticity check. Enter `987654321`, then click **Decrypt, compare & predict**. Wrong passwords or modified encrypted files are rejected before inference.
3. **Compare before and after:** the decrypted waveform image appears immediately after successful authentication, alongside the original image and a grayscale visualization of encrypted byte values. Separate prediction panels below the graphs show the original and restored labels, scores, and top three classes. "After encryption" means **after encryption and authenticated decryption**: the current model cannot predict from ciphertext. The byte visualization is not a viewable encrypted PNG and is not evidence of security strength. The comparison reports MAT/PNG byte equality, waveform mean squared error, maximum sample error, SHA-256 hashes, and independently computed prediction labels/scores before and after decryption. Both predictions are computed now using the same selected checkpoint, not read from a historical prediction. For unchanged inputs, lossless recovery should give zero error and unchanged predictions. If source files are missing or altered, the UI must not claim a successful original-file comparison.
4. **Keep outputs encrypted:** prediction Grad-CAM images and comparison reports are saved encrypted, with encrypted-download buttons. The temporary original/restored previews are cleared using **Hide decrypted images** or when selections change; a public altered preview can be viewed without a password. Download the entire batch ZIP to preserve its catalog, authenticated manifest and wrapped key together. Camouflage ZIPs contain visible altered MAT waveforms as well as ciphertext. Individual files require the batch key and are not standalone password envelopes. The optional restored-MAT download is explicitly plaintext.
5. **Robustness:** select the model and FGSM perturbation size, then run the validation test. Progress and clean/perturbed accuracy appear on the page. The test operates on normalized ECG waveform amplitudes.
6. **Train:** expand the training controls, choose epochs, batch size, and optional adversarial epsilon. New models are saved separately and appear in the model selector when training finishes. Processing jobs run one at a time.

### General care information and the RNAF comparison

Below the graphs, **General care information** explains what a clinician may discuss for a confirmed rhythm, with links to American Heart Association and NHLBI sources. It is a static educational lookup for the unperturbed model label, not a learned treatment recommender, prescription, diagnosis, or clinically validated decision-support system. The dataset has no treatment/outcome targets. Even an NSR prediction does not exclude disease. Clinical care requires a qualified professional to review the ECG, symptoms, history, and other findings; urgent symptoms should never wait for this app. No care guidance is generated from an adversarially changed label.

For a separate research comparison, select **Include adversarial attack comparison (FGSM)** before entering the decryption password. This applies one untargeted FGSM step to a temporary copy of the normalized 90 Hz model input. Epsilon defaults to 0.05 and is measured in normalized amplitude units, not image pixel values or clinical units. The panel shows clean and perturbed waveforms, both predictions, the measured maximum perturbation, and whether the class actually changed. The dataset label supplies the attack objective when known; otherwise the clean prediction is used. A pre-existing error is not counted as a successful attack, and no class change is forced. The graph and result report are stored encrypted; source recordings, model weights, and encryption comparisons remain unchanged.

The supplied [RNAF paper](https://doi.org/10.1109/ACCESS.2025.3636942), Figures 3-4 (PDF pages 5-6), illustrates a fish-to-dog adversarial/trigger misclassification. Its Methods section describes PGD/FGSM attacks plus PPO-based defense and steganalysis. These are distinct from reversible AES encryption. This workbench adds a **single-record FGSM experiment**, not a reproduction of that full image-based RNAF/PPO architecture or its reported results. The existing model takes ECG numerical waveforms, not the rendered PNG images. Adversarial outcomes are model sensitivity measurements, not evidence that a person's rhythm or treatment need changed.

The GUI defaults to `artifacts_final/model.pt`, your existing 30-epoch model, when available. Displayed validation metrics come from its saved `metrics.json`; predictions and robustness scores are computed when requested.

### Exact recovery of a camouflaged MAT

The visible transformed signal is used for the camouflage prediction. Its original-file recovery payload uses AES-256-GCM with a fresh nonce; authenticated associated data bind the payload to the transformed float32 `val`, batch/record context and format version. The encrypted manifest also authenticates the camouflaged artifact's hash and generation report. Altering the visible waveform or recovery payload causes verification to fail. After the password unwraps the batch key, the app decrypts the original **from that embedded payload**, verifies the saved original SHA-256 and exact bytes, and runs the original ECG prediction. The original `MLII` file is not needed for recovery.

Before password entry, only the intentionally public altered signal and saved prediction metadata are previewed; those saved labels are explicitly unverified until authentication. After authentication, the camouflaged and restored predictions are recomputed with the selected model. Changing models can change whether camouflage fools that model. Creation-time results remain in the authenticated report. Cases with no label change are reported honestly. The report distinguishes a label change from true misclassification relative to the dataset reference: an already-wrong clean prediction can even become correct after alteration.

## Dataset

Source: Pawel Plawiak, *ECG signals (1000 fragments)*, Mendeley Data V3, DOI [10.17632/7dybx7wyfn.3](https://data.mendeley.com/datasets/7dybx7wyfn/3), CC BY 4.0.

The dataset contains 1,000 10-second MLII recordings from 45 patients, sampled at 360 Hz. Your directory layout is supported directly:

```text
MLII/
  1 NSR/100m (0).mat
  2 APB/...
  ...
  17 PR/...
```

The parent folder supplies the label and the MATLAB `val` array supplies the waveform. Labels cover rhythms and conduction patterns, including normal sinus rhythm and pacemaker rhythm.

## Implemented methods and limits

| Component | Implementation |
| --- | --- |
| Preprocessing | 0.5-45 Hz zero-phase Butterworth filtering, median/MAD normalization, then 4× subsampling to the existing model's 90 Hz input |
| Classifier | Compact 1-D residual attention CNN using depthwise convolutions; RAMNV2-inspired research baseline |
| Explanation | Grad-CAM at the last residual block, plotted over the full 10-second input |
| Encryption | Random AES-256-GCM dataset key, fresh 96-bit nonce and 128-bit tag per file/recovery payload; key wrapped using scrypt N=131072, r=8, p=1 and the fixed demo password |
| Raw-signal camouflage | Iterative model-input gradient lifted to the 360 Hz raw waveform, amplitude-budget projection, and actual pipeline verification; a heuristic, not exact raw-space PGD or full RNAF |
| Robustness | Input-gradient FGSM and optional mixed clean/adversarial training |
| Evaluation | Stratified 800/200 fragment split, weighted classification metrics, and clean versus FGSM accuracy |

This code does **not** implement the original project PDF's unspecified ISSMCA, JPMC, ANF-ECCA, TDA/MI fusion, Grad-MRPCAM, or DTV-RRL methods. The FGSM layer is a limited resilience experiment, not a reproduction of the complete ResilienceNet/RNAF paper. Personalized treatment recommendations are not implemented because the supplied data contain no treatment/outcome records; the GUI offers only sourced general care education.

The batch manifest is encrypted and authenticated, including record names, labels and original MAT/PNG hashes. The public catalog is for selection only; decryption resolves files through the authenticated manifest. Scrypt derives the wrapping key once per batch or unlock (about 128 MiB memory), not once per file. Every MAT, waveform PNG, manifest, and prediction artifact uses a fresh nonce. Completed batches are listed only after all records pass roundtrip verification. Existing key/password APIs and CLI commands remain compatible for previous artifacts, but the new UI uses `/api/dataset/*` and never falls back to unauthenticated prediction.

The existing classifier uses simple subsampling. That preprocessing is retained for checkpoint compatibility; improved anti-aliasing/resampling needs retraining and comparison. Model scores are uncalibrated and the split is by fragment, so the same patient's recordings can occur in both sets. Dataset samples selected in the GUI may come from the training set. This is a research interface, not a clinical diagnostic system.

**Correction to the earlier robustness result:** the previous FGSM helper clipped all inputs to `[-8,8]`, which could exceed the stated perturbation bound. The corrected helper changes every sample by at most epsilon (within floating-point tolerance). Re-evaluation of the existing model at epsilon 0.05 gives 87% clean and 59.5% perturbed accuracy; the earlier 47% result should not be used. The GUI computes current results and does not reuse the old `robustness.json`.

## Installation on another computer

Use Python 3.10 or newer on a supported modern OS:

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .
ecg-gui --project /path/to/project
```

Download and extract the source dataset into `MLII/`, and retain `artifacts_final/model.pt` plus its metrics and label map. The GUI discovers local `artifacts*/model.pt` and `artifacts_gui/*/model.pt` files.

## CLI alternatives

```bash
python scripts/create_camouflaged_dataset.py --project /Users/sreekarvarma/Documents/project \
  --checkpoint artifacts_final/model.pt --epsilon 0.5 --steps 20

python -m ecg_cvd.batch_crypto --project /Users/sreekarvarma/Documents/project

ecg-train --data MLII --epochs 30 --batch-size 32 --artifacts artifacts_new

ecg-predict --checkpoint artifacts_final/model.pt \
  --signal "MLII/1 NSR/100m (0).mat" --output artifacts/ecg_explanation.png

ecg-encrypt --input artifacts/ecg_explanation.png \
  --output secure/ecg_explanation.png.ecgenc \
  --generate-key-file ~/ecg-keys/project-aes256.key

ecg-decrypt --input secure/ecg_explanation.png.ecgenc \
  --output restored/ecg_explanation.png --key-file ~/ecg-keys/project-aes256.key

ecg-robustness --data MLII --checkpoint artifacts_final/model.pt \
  --epsilon 0.05 --output artifacts_final/robustness_corrected.json

ecg-train --data MLII --epochs 30 --artifacts artifacts_robust \
  --adversarial-epsilon 0.05 --adversarial-weight 0.5
```

Tests: `.venv/bin/python -m unittest discover -s tests -v`.

Encryption uses the [cryptography AESGCM API](https://cryptography.io/en/latest/hazmat/primitives/aead/) and [scrypt key derivation](https://cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/#scrypt), based on [NIST SP 800-38D](https://csrc.nist.gov/pubs/sp/800/38/d/final). Encryption and adversarial testing address separate properties: recovering an authentic encrypted image does not guarantee a correct classifier prediction. Encryption does not change or artificially increase model scores.
