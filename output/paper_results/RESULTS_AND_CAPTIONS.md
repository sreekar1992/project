# ECG manuscript results and figure captions

## Reproducible results

On a reconstructed stratified validation split of 200 ECG fragments, the 1-D residual-attention baseline correctly classified 174 fragments (accuracy 87.00%). Weighted precision, recall and F1 were 86.40%, 87.00% and 86.22%, respectively. Macro F1 was 76.09%, and balanced accuracy was 76.79%. Macro one-versus-rest ROC AUC was 0.9907. The model contains 27,633 trainable parameters.

## Evaluation limitations (retain in the paper)

The validation split is fragment-based. All 200 validation fragments have a filename-derived recording ID also present in training (44 overlapping recording IDs). These IDs are recording proxies rather than independently verified patient identities. Three validation waveforms also exactly match training waveforms; their file pairs are listed in cross_split_duplicates.csv. Therefore, these results contain recording overlap and duplicate leakage and do not estimate performance on unseen recordings or unseen patients. Several classes have only 2-4 validation samples. Fusion and SVTA have zero recall. The legacy checkpoint does not store its seed; seed 42 is reconstructed from the original training default and reproduces the saved accuracy and weighted metrics exactly.

The results evaluate the implemented residual-attention CNN baseline and Grad-CAM, not the PDF's full proposed pipeline or treatment recommendations. No training/validation learning curves are fabricated because per-epoch validation history was not saved. High ROC AUC/AP reflects ranking of continuous scores and can coexist with low argmax recall. Softmax scores and Grad-CAM maps are not calibrated clinical confidence or validated diagnostic annotations.

## Figure captions

### fig01_confusion_matrix: Confusion matrix

Confusion matrix for 200 validation ECG fragments. Cell annotations are recording-fragment counts; color is normalized within each reference class. The diagonal contains 174 correct predictions. No independent patient or recording holdout was used.

### fig02_per_class_scores: Per-class precision, recall and F1

One-versus-rest precision, recall and F1 scores on the validation split. Parentheses show validation support. Fusion and SVTA have zero recall; several categories have only two to four examples. Undefined precision is assigned zero. Macro F1 gives equal class weight; weighted F1 weights each class by validation support.

### fig03_roc_precision_recall: ROC and precision-recall curves

One-versus-rest ROC and precision-recall curves from continuous softmax scores. Pale lines show individual classes; bold lines show micro-averaged curves. Mean class ROC AUC is 0.9907, and mean class average precision (AP) is 0.9151. AP is computed as a recall-weighted sum of precision, not trapezoidal PR area. Ranking metrics do not replace argmax classification scores, particularly for small classes.

### fig04_correct_predictions: Correct ECG prediction examples

One correct NSR, AFIB and PVC example is selected at the median predicted-class score among correct examples of that class (upper median when even). All examples are from validation. Waveforms are filtered, normalized, and sampled at 90 Hz for the model. Color denotes within-example Grad-CAM relevance, not a clinical annotation; each map is independently normalized. Right panels show the three largest uncalibrated softmax scores. Teal bars denote the reference class when it occurs among the top three.

### fig05_prediction_errors: High-confidence prediction errors

The three highest-score misclassifications with distinct reference classes are shown in descending predicted-score order to illustrate failure cases; this is a deliberate error-focused selection. All examples are from validation. Waveforms are filtered, normalized, and sampled at 90 Hz for the model. Color denotes within-example Grad-CAM relevance, not a clinical annotation; each map is independently normalized. Right panels show the three largest uncalibrated softmax scores. Teal bars denote the reference class when it occurs among the top three.

### fig06_adversarial_robustness: FGSM robustness curve

White-box, untargeted FGSM evaluation on the same 200 validation fragments. Perturbations use the gradient of cross-entropy with respect to the normalized 90 Hz model input, with no arbitrary amplitude clipping. Epsilon is an input-amplitude bound. The evaluated checkpoint is the original baseline, not an adversarially trained or complete ResilienceNet model. These scores measure only this specific attack and are not a security certificate.

### fig07_class_distribution: Dataset and validation support

Distribution of 1,000 source ECG fragments across 17 classes, with stacked counts for the reconstructed stratified training and validation partitions. Values above bars are total class counts. The validation sample sizes range from 2 to 57 fragments, limiting precision of class-specific estimates.

## How to use

Use PNG files (400 dpi) in Word; SVG files provide editable vector graphics. Use per_class_metrics.csv and overall_metrics.csv for tables. split_manifest.csv and validation_predictions.csv provide traceable predictions. The PDF combines the figures, tables and captions. All figures were computed from the existing checkpoint without retraining.

Reproduction requires the original project, including its installed Python environment, source package, MLII dataset, and artifacts_final/model.pt. The ZIP is a results bundle, not a standalone model distribution. Copy the files in reproduce/ into the original project's scripts/ directory before running them.

From the original project directory, regenerate figures and tables with `MPLCONFIGDIR=/private/tmp/ecg-paper-mpl .venv/bin/python scripts/make_paper_results.py`.

To regenerate the report and ZIP, use a Python environment with reportlab installed, then run `python scripts/build_paper_pdf.py`. Figure generation uses the original project's dependencies listed in pyproject.toml.

## Sources

- Dataset: Plawiak (2017), ECG signals (1000 fragments), Mendeley Data V3. https://doi.org/10.17632/7dybx7wyfn.3 (CC BY 4.0)
- Grad-CAM: Selvaraju et al. https://arxiv.org/abs/1610.02391
- FGSM: Goodfellow et al. https://arxiv.org/abs/1412.6572
