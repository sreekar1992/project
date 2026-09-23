"""Reproduce validation results and export manuscript figures from the saved model.

Run from the project root with: .venv/bin/python scripts/make_paper_results.py
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import platform
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize
import numpy as np
import scipy
import sklearn
from sklearn.metrics import (accuracy_score, average_precision_score, confusion_matrix,
                             precision_recall_curve, precision_recall_fscore_support,
                             roc_auc_score, roc_curve)
from sklearn.model_selection import train_test_split
import torch
import torch.nn.functional as F

from ecg_cvd.data import load_directory_dataset, model_input, preprocess
from ecg_cvd.gui import CLASS_NAMES
from ecg_cvd.model import RAMNV2
from ecg_cvd.robustness import fgsm

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/paper_results"
FIGURES = OUT / "figures"
TABLES = OUT / "tables"
TEAL, NAVY, GOLD, RED = "#137E7A", "#213B56", "#D39433", "#BE5145"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.titlesize": 12,
                     "axes.labelsize": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "svg.fonttype": "none", "savefig.facecolor": "white"})
torch.set_num_threads(2)


def write_csv(name, rows, columns=None):
    with (TABLES / name).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def predict(model, x):
    chunks = []
    with torch.no_grad():
        for start in range(0, len(x), 32):
            chunks.append(torch.softmax(model(torch.from_numpy(x[start:start+32, None])), 1).numpy())
    return np.concatenate(chunks)


def relevance(model, signal, target):
    captured = []
    handle = model.blocks[-1].register_forward_hook(lambda _m, _i, output: captured.append(output))
    try:
        x = torch.from_numpy(signal[None, None]).requires_grad_(True)
        score = model(x)[0, target]
        grad, = torch.autograd.grad(score, captured[0])
        cam = F.relu((grad.mean(-1, keepdim=True) * captured[0]).sum(1)).detach().numpy()[0]
        cam = np.interp(np.arange(len(signal)), np.linspace(0, len(signal)-1, len(cam)), cam)
        return (cam - cam.min()) / (np.ptp(cam) + 1e-12)
    finally:
        handle.remove()


def main():
    FIGURES.mkdir(parents=True, exist_ok=True)
    TABLES.mkdir(parents=True, exist_ok=True)
    checkpoint = ROOT / "artifacts_final/model.pt"
    saved = torch.load(checkpoint, map_location="cpu", weights_only=True)
    paths = sorted((ROOT / "MLII").rglob("*.mat"))
    raw, labels_raw, _ = load_directory_dataset(ROOT / "MLII")
    assert raw.shape == (1000, 3600) and len(paths) == len(raw)
    assert all(path.parent.name == label for path, label in zip(paths, labels_raw))
    mapping = saved["label_map"]
    labels = [label for label, _ in sorted(mapping.items(), key=lambda item: item[1])]
    y = np.asarray([mapping[str(label)] for label in labels_raw])
    seed = saved.get("split", {}).get("seed", 42)
    train_idx, val_idx = train_test_split(np.arange(len(raw)), test_size=.2, random_state=seed, stratify=y)
    x = model_input(preprocess(raw))
    model = RAMNV2(saved["num_classes"])
    model.load_state_dict(saved["state_dict"])
    model.eval()
    probabilities = predict(model, x[val_idx])
    true = y[val_idx]
    predicted = probabilities.argmax(1)
    n_classes = len(labels)
    onehot = np.eye(n_classes)[true]
    display_order = sorted(range(n_classes), key=lambda i: int(labels[i].split()[0]))
    names = [labels[i].split(" ", 1)[1] for i in display_order]
    cm = confusion_matrix(true, predicted, labels=np.arange(n_classes))
    p, r, f, support = precision_recall_fscore_support(true, predicted, labels=np.arange(n_classes), zero_division=0)
    dataset_counts = np.bincount(y, minlength=n_classes)
    train_counts = np.bincount(y[train_idx], minlength=n_classes)
    class_auc = [roc_auc_score(onehot[:, i], probabilities[:, i]) for i in range(n_classes)]
    class_ap = [average_precision_score(onehot[:, i], probabilities[:, i]) for i in range(n_classes)]
    class_rows = []
    for i in display_order:
        tp = int(cm[i, i]); fn = int(cm[i].sum()-tp); fp = int(cm[:, i].sum()-tp)
        tn = int(cm.sum()-tp-fn-fp)
        abbreviation = labels[i].split(" ", 1)[1]
        class_rows.append({"class": labels[i], "full_name": CLASS_NAMES.get(abbreviation, abbreviation),
                           "dataset_n": int(dataset_counts[i]), "train_n": int(train_counts[i]),
                           "validation_n": int(support[i]), "precision": float(p[i]), "recall": float(r[i]),
                           "f1": float(f[i]), "specificity": tn/(tn+fp), "roc_auc_ovr": class_auc[i],
                           "average_precision_ovr": class_ap[i], "true_positive": tp, "false_positive": fp,
                           "false_negative": fn, "true_negative": tn})
    metrics = {"accuracy": accuracy_score(true, predicted), "correct": int((true == predicted).sum()),
               "n_validation": len(val_idx), "n_training": len(train_idx), "n_classes": n_classes,
               "macro_precision": float(p.mean()), "macro_recall": float(r.mean()), "macro_f1": float(f.mean()),
               "weighted_precision": float(np.average(p, weights=support)),
               "weighted_recall": float(np.average(r, weights=support)),
               "weighted_f1": float(np.average(f, weights=support)), "balanced_accuracy": float(r.mean()),
               "macro_ovr_roc_auc": float(np.mean(class_auc)),
               "micro_ovr_roc_auc": roc_auc_score(onehot.ravel(), probabilities.ravel()),
               "macro_average_precision": float(np.mean(class_ap)),
               "micro_average_precision": average_precision_score(onehot.ravel(), probabilities.ravel()),
               "parameters": sum(parameter.numel() for parameter in model.parameters())}
    original = json.loads((checkpoint.parent / "metrics.json").read_text())
    assert abs(metrics["accuracy"] - original["accuracy"]) < 1e-10
    assert abs(metrics["weighted_f1"] - original["f1_weighted"]) < 1e-10
    record = lambda path: re.match(r"\d+", path.name).group(0)
    train_records = {record(paths[i]) for i in train_idx}
    val_records = {record(paths[i]) for i in val_idx}
    overlap = train_records & val_records
    hashes = [hashlib.sha256(row.tobytes()).hexdigest() for row in raw]
    duplicate_overlap = len({hashes[i] for i in train_idx} & {hashes[i] for i in val_idx})
    duplicate_pairs = [{"validation_file":str(paths[i].relative_to(ROOT)),
                        "training_file":str(paths[j].relative_to(ROOT)), "waveform_sha256":hashes[i]}
                       for i in val_idx for j in train_idx if hashes[i] == hashes[j]]
    write_csv("cross_split_duplicates.csv", duplicate_pairs,
              ["validation_file", "training_file", "waveform_sha256"])
    provenance = {
        "checkpoint": "artifacts_final/model.pt", "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "split_seed": seed, "split_seed_source": "reconstructed original default, matches saved metrics",
        "split_type": "stratified random fragment split", "validation_fraction": .2,
        "validation_recording_ids": len(val_records), "training_recording_ids": len(train_records),
        "overlapping_recording_ids": len(overlap),
        "validation_fragments_from_overlapping_recordings": sum(record(paths[i]) in train_records for i in val_idx),
        "identical_waveform_hashes_in_both_splits": duplicate_overlap,
        "validation_fragments_exactly_duplicated_in_training": len({row["validation_file"] for row in duplicate_pairs}),
        "recording_id_method": "leading digits from original MATLAB filename; proxy, not verified patient identity",
        "preprocessing": "360 Hz input; fourth-order 0.5-45 Hz Butterworth SOS filtfilt; median/MAD scaling; every fourth sample gives 90 Hz model input",
        "software": {"python": platform.python_version(), "torch": torch.__version__, "numpy": np.__version__,
                     "scipy": scipy.__version__, "sklearn": sklearn.__version__, "matplotlib": matplotlib.__version__},
    }
    split_membership = {int(i): "training" for i in train_idx}
    split_membership.update({int(i): "validation" for i in val_idx})
    write_csv("split_manifest.csv", [{"source_index": i, "file": str(path.relative_to(ROOT)),
              "class": str(labels_raw[i]), "split": split_membership[i], "recording_id_proxy": record(path),
              "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for i, path in enumerate(paths)])
    write_csv("per_class_metrics.csv", class_rows)
    write_csv("overall_metrics.csv", [{"metric": k, "value": v} for k, v in metrics.items()])
    write_csv("confusion_matrix_counts.csv", [{"true_class": labels[i], **{labels[j]: int(cm[i,j]) for j in display_order}} for i in display_order])
    prediction_rows=[]
    for j, source_idx in enumerate(val_idx):
        prediction_rows.append({"file": str(paths[source_idx].relative_to(ROOT)), "true_class": labels[true[j]],
                                "predicted_class": labels[predicted[j]], "correct": bool(true[j] == predicted[j]),
                                "model_score": float(probabilities[j].max()),
                                **{f"score_{labels[i]}": float(probabilities[j,i]) for i in display_order}})
    write_csv("validation_predictions.csv", prediction_rows)
    captions=[]

    def save_figure(fig, name, title, caption):
        fig.savefig(FIGURES / f"{name}.png", dpi=400, bbox_inches="tight")
        fig.savefig(FIGURES / f"{name}.svg", bbox_inches="tight")
        plt.close(fig)
        captions.append({"file": name, "title": title, "caption": caption})
        print(f"Saved {name}", flush=True)

    matrix = cm[np.ix_(display_order, display_order)]
    norm = matrix / matrix.sum(axis=1, keepdims=True)
    fig, ax = plt.subplots(figsize=(9.4, 8.1), layout="constrained")
    im = ax.imshow(norm, cmap="Blues", vmin=0, vmax=1)
    ax.set(xticks=np.arange(17), yticks=np.arange(17), xticklabels=names, yticklabels=names,
           xlabel="Predicted class", ylabel="Reference class", title="Validation confusion matrix (n = 200)")
    plt.setp(ax.get_xticklabels(), rotation=50, ha="right", rotation_mode="anchor")
    for i in range(17):
        for j in range(17):
            if matrix[i,j]:
                ax.text(j,i,str(matrix[i,j]),ha="center",va="center",fontsize=9,
                        color="white" if norm[i,j] > .55 else NAVY)
    fig.colorbar(im, ax=ax, shrink=.75, label="Fraction of reference class")
    save_figure(fig,"fig01_confusion_matrix","Confusion matrix",
                "Confusion matrix for 200 validation ECG fragments. Cell annotations are recording-fragment counts; color is normalized within each reference class. The diagonal contains 174 correct predictions. No independent patient or recording holdout was used.")

    fig, ax = plt.subplots(figsize=(9.5, 7.7), layout="constrained")
    yy = np.arange(17)
    for offset, values, label, color in [(-.24,p,"Precision",NAVY),(0,r,"Recall",TEAL),(.24,f,"F1",GOLD)]:
        ax.barh(yy+offset,values[display_order]*100,height=.22,label=label,color=color)
    ax.set(yticks=yy, yticklabels=[f"{name} (n={support[i]})" for name,i in zip(names,display_order)],
           xlim=(0,105),xlabel="Score (%)",title="Performance by rhythm class")
    ax.invert_yaxis(); ax.set_axisbelow(True); ax.grid(axis="x",alpha=.18)
    ax.legend(loc="lower center",bbox_to_anchor=(.5,1.04),ncol=3,frameon=False)
    save_figure(fig,"fig02_per_class_scores","Per-class precision, recall and F1",
                "One-versus-rest precision, recall and F1 scores on the validation split. Parentheses show validation support. Fusion and SVTA have zero recall; several categories have only two to four examples. Undefined precision is assigned zero. Macro F1 gives equal class weight; weighted F1 weights each class by validation support.")

    fig, axes = plt.subplots(1,2,figsize=(10.6,4.7),layout="constrained")
    roc_rows=[]; pr_rows=[]
    for i in range(n_classes):
        fpr,tpr,_=roc_curve(onehot[:,i], probabilities[:,i]); precision,recall,_=precision_recall_curve(onehot[:,i],probabilities[:,i])
        axes[0].plot(fpr,tpr,color="#AAB7C2",alpha=.5,lw=.8)
        axes[1].plot(recall,precision,color="#AAB7C2",alpha=.5,lw=.8)
        roc_rows.extend({"class":labels[i],"fpr":float(a),"tpr":float(b)} for a,b in zip(fpr,tpr))
        pr_rows.extend({"class":labels[i],"recall":float(a),"precision":float(b)} for a,b in zip(recall,precision))
    fpr,tpr,_=roc_curve(onehot.ravel(), probabilities.ravel())
    axes[0].plot(fpr,tpr,color=TEAL,lw=2.3,label=f"Micro ROC AUC = {metrics['micro_ovr_roc_auc']:.4f}")
    axes[0].plot([0,1],[0,1],ls="--",color="#7D8B95",lw=1,label="Chance")
    precision,recall,_=precision_recall_curve(onehot.ravel(),probabilities.ravel())
    axes[1].plot(recall,precision,color=NAVY,lw=2.3,label=f"Micro AP = {metrics['micro_average_precision']:.4f}")
    axes[1].axhline(1/17,ls="--",color="#7D8B95",lw=1,label="Micro prevalence = 1/17")
    axes[0].set(xlabel="False positive rate",ylabel="True positive rate",title="(a) One-versus-rest ROC")
    axes[1].set(xlabel="Recall",ylabel="Precision",title="(b) Precision-recall")
    for ax in axes:
        ax.set(xlim=(0,1),ylim=(0,1.02)); ax.grid(alpha=.15); ax.legend(loc="lower left",fontsize=8.5)
    save_figure(fig,"fig03_roc_precision_recall","ROC and precision-recall curves",
                f"One-versus-rest ROC and precision-recall curves from continuous softmax scores. Pale lines show individual classes; bold lines show micro-averaged curves. Mean class ROC AUC is {metrics['macro_ovr_roc_auc']:.4f}, and mean class average precision (AP) is {metrics['macro_average_precision']:.4f}. AP is computed as a recall-weighted sum of precision, not trapezoidal PR area. Ranking metrics do not replace argmax classification scores, particularly for small classes.")
    write_csv("roc_coordinates.csv",roc_rows); write_csv("precision_recall_coordinates.csv",pr_rows)

    good=[]
    for label in ["1 NSR","4 AFIB","7 PVC"]:
        choices=np.flatnonzero((true==mapping[label]) & (predicted==true))
        ordered=choices[np.argsort(probabilities[choices].max(axis=1),kind="stable")]
        good.append(int(ordered[len(ordered)//2]))
    wrong=[]; used=set()
    for j in np.argsort(-probabilities.max(axis=1),kind="stable"):
        if true[j]!=predicted[j] and true[j] not in used:
            wrong.append(int(j)); used.add(true[j])
        if len(wrong)==3: break
    examples=[]
    def plot_examples(chosen, name, title, selection):
        fig, axes=plt.subplots(3,2,figsize=(11.2,8.1),gridspec_kw={"width_ratios":[3.2,1]},layout="constrained")
        for row,j in enumerate(chosen):
            signal=x[val_idx[j]]; cam=relevance(model,signal,int(predicted[j])); t=np.arange(len(signal))/90
            points=np.column_stack([t,signal]); segments=np.stack([points[:-1],points[1:]],axis=1)
            lc=LineCollection(segments,cmap="viridis",norm=Normalize(0,1),linewidth=1.15)
            lc.set_array((cam[:-1]+cam[1:])/2)
            ax=axes[row,0]; ax.add_collection(lc); ax.autoscale_view(); ax.set_xlim(0,10)
            ax.set_ylabel("Normalized amplitude"); ax.set_xlabel("Time (s)"); ax.grid(alpha=.15)
            ref=labels[true[j]].split(" ",1)[1]; pred=labels[predicted[j]].split(" ",1)[1]
            ax.set_title(f"({chr(97+row)}) Reference: {ref} | Prediction: {pred} | {paths[val_idx[j]].name}",loc="left",fontsize=10.5)
            order=np.argsort(probabilities[j])[::-1][:3]; bar=axes[row,1]
            bar.barh(np.arange(3),probabilities[j,order]*100,color=[TEAL if i==true[j] else NAVY for i in order])
            bar.set(yticks=np.arange(3),yticklabels=[labels[i].split(" ",1)[1] for i in order],xlim=(0,119),xlabel="Model score (%)")
            bar.invert_yaxis(); bar.set_xticks([0,50,100]); bar.set_axisbelow(True); bar.grid(axis="x",alpha=.15)
            for k,i in enumerate(order): bar.text(probabilities[j,i]*100+2,k,f"{probabilities[j,i]*100:.1f}",va="center",fontsize=9)
            examples.append({"figure":name,"panel":chr(97+row),"file":str(paths[val_idx[j]].relative_to(ROOT)),
                             "reference":labels[true[j]],"prediction":labels[predicted[j]],"score":float(probabilities[j].max()),"selection":selection})
        fig.colorbar(lc,ax=axes[:,0],orientation="horizontal",shrink=.65,aspect=45,pad=.04,label="Relative Grad-CAM relevance for the predicted class (0-1)")
        save_figure(fig,name,title,selection+" All examples are from validation. Waveforms are filtered, normalized, and sampled at 90 Hz for the model. Color denotes within-example Grad-CAM relevance, not a clinical annotation; each map is independently normalized. Right panels show the three largest uncalibrated softmax scores. Teal bars denote the reference class when it occurs among the top three.")
    plot_examples(good,"fig04_correct_predictions","Correct ECG prediction examples",
                  "One correct NSR, AFIB and PVC example is selected at the median predicted-class score among correct examples of that class (upper median when even).")
    plot_examples(wrong,"fig05_prediction_errors","High-confidence prediction errors",
                  "The three highest-score misclassifications with distinct reference classes are shown in descending predicted-score order to illustrate failure cases; this is a deliberate error-focused selection.")
    write_csv("example_selection.csv",examples)

    robust_rows=[]
    for epsilon in [0,.01,.02,.05,.10,.20]:
        attack_pred=[]; max_delta=0
        for start in range(0,len(val_idx),32):
            xx=torch.from_numpy(x[val_idx[start:start+32],None]); yy=torch.from_numpy(true[start:start+32])
            attacked=fgsm(model,xx,yy,epsilon)
            max_delta=max(max_delta,float((attacked-xx).abs().max()))
            with torch.no_grad(): attack_pred.extend(model(attacked).argmax(1).numpy().tolist())
        attack_pred=np.asarray(attack_pred)
        _,_,ff,_=precision_recall_fscore_support(true,attack_pred,labels=np.arange(n_classes),zero_division=0)
        robust_rows.append({"epsilon":epsilon,"accuracy":float(np.mean(attack_pred==true)),"macro_f1":float(ff.mean()),
                            "correct":int((attack_pred==true).sum()),"n":len(true),"max_absolute_perturbation":max_delta})
        assert max_delta <= epsilon+2e-6
        print(f"FGSM epsilon={epsilon}: {robust_rows[-1]['accuracy']:.3f}",flush=True)
    fig,ax=plt.subplots(figsize=(8.3,4.8),layout="constrained")
    eps=[row["epsilon"] for row in robust_rows]
    for key,color,marker,label in [("accuracy",TEAL,"o","Accuracy"),("macro_f1",NAVY,"s","Macro F1")]:
        values=[100*row[key] for row in robust_rows]
        ax.plot(eps,values,color=color,marker=marker,lw=2,label=label)
        if key=="accuracy":
            for a,b in zip(eps,values): ax.annotate(f"{b:.1f}",(a,b),xytext=(0,9),textcoords="offset points",ha="center",fontsize=9)
    ax.set(xlabel="FGSM epsilon (normalized input-amplitude units)",ylabel="Score (%)",ylim=(0,102),
           title="Baseline model under bounded FGSM perturbations (n = 200)")
    ax.set_xticks(eps); ax.grid(alpha=.18); ax.legend()
    save_figure(fig,"fig06_adversarial_robustness","FGSM robustness curve",
                "White-box, untargeted FGSM evaluation on the same 200 validation fragments. Perturbations use the gradient of cross-entropy with respect to the normalized 90 Hz model input, with no arbitrary amplitude clipping. Epsilon is an input-amplitude bound. The evaluated checkpoint is the original baseline, not an adversarially trained or complete ResilienceNet model. These scores measure only this specific attack and are not a security certificate.")
    write_csv("robustness_curve.csv",robust_rows)

    fig,ax=plt.subplots(figsize=(9.5,5),layout="constrained")
    indices=np.arange(17)
    ax.bar(indices,train_counts[display_order],color=NAVY,label="Training (n = 800)")
    ax.bar(indices,support[display_order],bottom=train_counts[display_order],color=TEAL,label="Validation (n = 200)")
    for i,idx in enumerate(display_order):
        ax.text(i,dataset_counts[idx]+3,str(dataset_counts[idx]),ha="center",fontsize=8.5)
    ax.set(xticks=indices,xticklabels=names,ylabel="ECG fragments",ylim=(0,320),title="Class distribution and split support")
    plt.setp(ax.get_xticklabels(),rotation=50,ha="right",rotation_mode="anchor")
    ax.grid(axis="y",alpha=.15); ax.set_axisbelow(True); ax.legend()
    save_figure(fig,"fig07_class_distribution","Dataset and validation support",
                "Distribution of 1,000 source ECG fragments across 17 classes, with stacked counts for the reconstructed stratified training and validation partitions. Values above bars are total class counts. The validation sample sizes range from 2 to 57 fragments, limiting precision of class-specific estimates.")

    package={"metrics":metrics,"provenance":provenance,"classes":class_rows,"figures":captions,"robustness":robust_rows,"examples":examples}
    (OUT/"results.json").write_text(json.dumps(package,indent=2)+"\n")
    md=["# ECG manuscript results and figure captions", "", "## Reproducible results", "",
        f"On a reconstructed stratified validation split of 200 ECG fragments, the 1-D residual-attention baseline correctly classified {metrics['correct']} fragments (accuracy {metrics['accuracy']:.2%}). Weighted precision, recall and F1 were {metrics['weighted_precision']:.2%}, {metrics['weighted_recall']:.2%} and {metrics['weighted_f1']:.2%}, respectively. Macro F1 was {metrics['macro_f1']:.2%}, and balanced accuracy was {metrics['balanced_accuracy']:.2%}. Macro one-versus-rest ROC AUC was {metrics['macro_ovr_roc_auc']:.4f}. The model contains {metrics['parameters']:,} trainable parameters.", "",
        "## Evaluation limitations (retain in the paper)", "",
        f"The validation split is fragment-based. All {len(val_idx)} validation fragments have a filename-derived recording ID also present in training ({len(overlap)} overlapping recording IDs). These IDs are recording proxies rather than independently verified patient identities. Three validation waveforms also exactly match training waveforms; their file pairs are listed in cross_split_duplicates.csv. Therefore, these results contain recording overlap and duplicate leakage and do not estimate performance on unseen recordings or unseen patients. Several classes have only 2-4 validation samples. Fusion and SVTA have zero recall. The legacy checkpoint does not store its seed; seed 42 is reconstructed from the original training default and reproduces the saved accuracy and weighted metrics exactly.", "",
        "The results evaluate the implemented residual-attention CNN baseline and Grad-CAM, not the PDF's full proposed pipeline or treatment recommendations. No training/validation learning curves are fabricated because per-epoch validation history was not saved. High ROC AUC/AP reflects ranking of continuous scores and can coexist with low argmax recall. Softmax scores and Grad-CAM maps are not calibrated clinical confidence or validated diagnostic annotations.", "",
        "## Figure captions", ""]
    for item in captions: md += [f"### {item['file']}: {item['title']}","",item["caption"],""]
    md += ["## How to use", "", "Use PNG files (400 dpi) in Word; SVG files provide editable vector graphics. Use per_class_metrics.csv and overall_metrics.csv for tables. split_manifest.csv and validation_predictions.csv provide traceable predictions. The PDF combines the figures, tables and captions. All figures were computed from the existing checkpoint without retraining.","",
           "Reproduction requires the original project, including its installed Python environment, source package, MLII dataset, and artifacts_final/model.pt. The ZIP is a results bundle, not a standalone model distribution. Copy the files in reproduce/ into the original project's scripts/ directory before running them.", "",
           "From the original project directory, regenerate figures and tables with `MPLCONFIGDIR=/private/tmp/ecg-paper-mpl .venv/bin/python scripts/make_paper_results.py`.", "",
           "To regenerate the report and ZIP, use a Python environment with reportlab installed, then run `python scripts/build_paper_pdf.py`. Figure generation uses the original project's dependencies listed in pyproject.toml.", "",
           "## Sources", "", "- Dataset: Plawiak (2017), ECG signals (1000 fragments), Mendeley Data V3. https://doi.org/10.17632/7dybx7wyfn.3 (CC BY 4.0)",
           "- Grad-CAM: Selvaraju et al. https://arxiv.org/abs/1610.02391", "- FGSM: Goodfellow et al. https://arxiv.org/abs/1412.6572", ""]
    (OUT/"RESULTS_AND_CAPTIONS.md").write_text("\n".join(md))
    latex=[r"\begin{tabular}{lrrrr}",r"\hline",r"Class & n & Precision (\%) & Recall (\%) & F1 (\%) \\",r"\hline"]
    for row in class_rows:
        latex.append(f"{row['class']} & {row['validation_n']} & {row['precision']*100:.2f} & {row['recall']*100:.2f} & {row['f1']*100:.2f} "+r"\\")
    latex.extend([r"\hline",r"\end{tabular}"])
    (TABLES/"per_class_table.tex").write_text("\n".join(latex)+"\n")
    print(json.dumps(metrics,indent=2),flush=True)
    print(json.dumps(provenance,indent=2),flush=True)


if __name__ == "__main__":
    main()
