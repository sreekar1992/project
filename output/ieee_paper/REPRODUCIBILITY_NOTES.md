# Manuscript reproducibility notes

Title: Adversarial ECG Camouflage with Authenticated Recovery and Residual Attention Classification

Authors supplied by the user: Muddisetty Sreekar varma and Sachikanta dash.

## Publication preparation

This is an original IEEE-style, two-column conference manuscript draft, not a published or accepted IEEE article. No DOI, acceptance date, grant, affiliation, email, ethics approval, or publication status has been invented. Author departments, institutions, contact information, and the intended conference or journal remain to be confirmed. Venue-specific formatting, length, author-disclosure requirements, and PDF checks must be applied before submission. The supplied RNAF article informed the background and scope comparison; its text, figures, and reported results were not copied into this manuscript.

Layout: US Letter; 0.625-inch side margins; 0.75-inch top and 1-inch bottom margin; two 3.5-inch columns with a 0.25-inch gap; Times New Roman; 24-point title and 10-point body. Figures are 3.45 inches wide at 400 dpi. The title spans the columns. See the official template source: https://www.ieee.org/conferences/publishing/templates.html .

## Evidence and provenance

- Classification checkpoint: `artifacts_final/model.pt`
- Checkpoint SHA-256: `f2ea83714317c0b98cc36f314775e0c69001f810b6ed0d860429b756a71daa35`
- Saved evaluation: `output/paper_results/results.json`
- Split manifest: `output/paper_results/tables/split_manifest.csv`
- Prediction rows: `output/paper_results/tables/validation_predictions.csv`
- Duplicate pairs: `output/paper_results/tables/cross_split_duplicates.csv`
- Audited camouflage batch: `artifacts_dataset/f5af518e8dc04ebb9bd4504b31c8f052`
- Batch creation UTC: `2026-09-19T06:43:58.488562+00:00`
- Encrypted manifest SHA-256: `9a71c3e513969c5b164400ac486d4c4e4520815ee5124c7f53e0b7a704caf786`
- Camouflage configuration: raw epsilon 0.5; maximum 20 steps; same checkpoint digest.
- Figure-level evidence: `output/ieee_paper/figure_evidence.json`

The legacy checkpoint contains no training or split metadata. The original user-provided command specifies 30 epochs and batch size 32; seed 42 is reconstructed from original defaults and reproduces saved metrics. All 200 validation fragments overlap training recording-ID proxies, and three are exact training duplicates. The manuscript does not claim independent patient evaluation.

The 1,000 label changes are authenticated saved creation-time reports, including training records. The 1,000 exact recovery results were independently rechecked during manuscript preparation without rerunning the whole-dataset classifier. The illustrative NSR example was reclassified for the new figure, and it belongs to training. No new training was performed.

The software test suite passed 114 tests on 2026-09-19 before manuscript preparation. This is functional regression evidence, not clinical or formal security certification.

## Reproduction

From the original project root, with its existing dataset and checkpoint:

```sh
MPLCONFIGDIR=/private/tmp/ecg-paper-mpl .venv/bin/python scripts/make_paper_results.py
MPLCONFIGDIR=/private/tmp/ecg-paper-mpl .venv/bin/python scripts/make_ieee_figures.py
```

The manuscript builder requires python-docx:

```sh
python scripts/build_ieee_manuscript.py
```

The delivered Word document was rendered using the bundled LibreOffice renderer and every page inspected. Preserve the complete batch archive, including wrapped key and authenticated manifest, for recovery. The public fixed password is a demonstration configuration only, not a production security recommendation.

## Submission checks still needed

1. Confirm affiliations, corresponding author, email addresses, and author-name capitalization.
2. Confirm the target IEEE venue and its current template and page limit.
3. Obtain author approval for every reported method and result, and complete any required AI-assistance disclosure.
4. Replace the leakage-affected benchmark with patient- or recording-disjoint evaluation before making generalization claims.
