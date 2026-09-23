"""Make IEEE-column figures from saved measurements and one authenticated example.

This script does not retrain the model or rerun dataset-wide evaluation. It reads
the saved validation results, authenticates one existing camouflage container,
and evaluates the original, altered, and restored waveforms on the saved model.
All outputs are confined to output/ieee_paper/figures and figure_evidence.json.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from io import BytesIO
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from matplotlib.ticker import PercentFormatter
import numpy as np
from scipy.io import loadmat
import torch

from ecg_cvd.batch_crypto import DEMO_PASSWORD, decrypt_record
from ecg_cvd.data import model_input, preprocess
from ecg_cvd.model import RAMNV2


WIDTH = 3.45
DPI = 400
BATCH_ID = "f5af518e8dc04ebb9bd4504b31c8f052"
SOURCE = "MLII/1 NSR/100m (2).mat"


def save_figure(fig, output: Path, stem: str) -> None:
    fig.savefig(output / f"{stem}.png", dpi=DPI, facecolor="white")
    fig.savefig(output / f"{stem}.svg", facecolor="white")
    plt.close(fig)


def workflow(output: Path) -> None:
    fig, ax = plt.subplots(figsize=(WIDTH, 3.65))
    fig.subplots_adjust(left=.025, right=.975, bottom=.02, top=.98)
    ax.set(xlim=(0, 1), ylim=(0, 1))
    ax.axis("off")

    def box(x, y, w, h, label, fill="white", bold=False):
        ax.add_patch(FancyBboxPatch((x, y), w, h,
                     boxstyle="round,pad=0.009,rounding_size=0.012",
                     facecolor=fill, edgecolor="0.25", linewidth=.65))
        ax.text(x + w / 2, y + h / 2, label, ha="center", va="center",
                fontsize=8, linespacing=1.25,
                fontweight="bold" if bold else "normal")

    def arrow(start, end, label=None, offset=(0, 0)):
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>",
                     mutation_scale=8, linewidth=.7, color="0.2"))
        if label:
            ax.text((start[0] + end[0]) / 2 + offset[0],
                    (start[1] + end[1]) / 2 + offset[1], label,
                    ha="center", va="center", fontsize=8,
                    bbox={"facecolor": "white", "edgecolor": "none", "pad": .4})

    box(.21, .88, .58, .085, "Original MAT bytes\n3,600-sample ECG", "0.96", True)
    box(.025, .665, .435, .135,
        "PUBLIC BRANCH\nGradient-guided\nwaveform modification")
    box(.54, .665, .435, .135,
        "PROTECTED BRANCH\nAES-256-GCM encrypts\nexact original bytes")
    arrow((.36, .875), (.242, .81))
    arrow((.64, .875), (.757, .81))
    box(.13, .475, .74, .105,
        "Camouflaged MAT container\nPublic val + encrypted recovery payload", "0.94")
    arrow((.242, .66), (.32, .585))
    arrow((.757, .66), (.68, .585))
    box(.025, .255, .435, .13,
        "Read public decoy\nNo password needed\nNot confidential")
    box(.54, .255, .435, .13,
        "Password unwraps key\nAuthenticate + decrypt\nRecover original bytes")
    arrow((.32, .47), (.242, .39))
    arrow((.68, .47), (.757, .39))
    box(.025, .035, .435, .13,
        "ECG classifier\nMeasure changed label\n(model-dependent)", "0.96")
    box(.54, .035, .435, .13,
        "ECG classifier\nOriginal prediction\n+ byte-match check", "0.96")
    arrow((.242, .25), (.242, .17))
    arrow((.757, .25), (.757, .17))
    save_figure(fig, output, "fig01_workflow")


def confusion(root: Path, output: Path) -> dict:
    source = root / "output/paper_results/tables/confusion_matrix_counts.csv"
    with source.open(newline="") as stream:
        rows = list(csv.reader(stream))
    labels = rows[0][1:]
    matrix = np.array([[int(x) for x in row[1:]] for row in rows[1:]])
    if matrix.shape != (17, 17) or matrix.sum() != 200:
        raise ValueError("Unexpected saved confusion matrix dimensions or count.")
    fig, ax = plt.subplots(figsize=(WIDTH, 3.30))
    fig.subplots_adjust(left=.13, right=.99, bottom=.155, top=.99)
    ax.imshow(matrix, cmap="Blues", vmin=0, vmax=int(matrix.max()))
    ax.set(xticks=np.arange(17), yticks=np.arange(17),
           xticklabels=np.arange(1, 18), yticklabels=np.arange(1, 18),
           xlabel="Predicted class ID", ylabel="Reference class ID")
    ax.tick_params(axis="both", labelsize=8, length=0, pad=2)
    for row, col in np.argwhere(matrix != 0):
        ax.text(col, row, str(matrix[row, col]), ha="center", va="center",
                color="white" if matrix[row, col] > 26 else "black", fontsize=8)
    ax.set_xticks(np.arange(-.5, 17, 1), minor=True)
    ax.set_yticks(np.arange(-.5, 17, 1), minor=True)
    ax.grid(which="minor", color="0.85", linewidth=.35)
    ax.tick_params(which="minor", bottom=False, left=False)
    for spine in ax.spines.values():
        spine.set_linewidth(.45)
    save_figure(fig, output, "fig02_confusion_matrix")
    return {"source": str(source.relative_to(root)), "class_order": labels,
            "n": int(matrix.sum()), "correct": int(np.trace(matrix)),
            "zero_cells": "left blank", "cell_values": "fragment counts"}


def fgsm(results: dict, output: Path) -> None:
    curve = results["robustness"]
    eps = np.array([row["epsilon"] for row in curve])
    accuracy = np.array([row["accuracy"] for row in curve])
    macro_f1 = np.array([row["macro_f1"] for row in curve])
    fig, ax = plt.subplots(figsize=(WIDTH, 2.65))
    fig.subplots_adjust(left=.205, right=.98, bottom=.215, top=.965)
    ax.plot(eps, accuracy, "o-", color="#005577", markersize=4,
            linewidth=1.2, label="Accuracy")
    ax.plot(eps, macro_f1, "s--", color="#b34b18", markersize=3.6,
            linewidth=1.1, label="Macro F1")
    ax.set(xlim=(-.005, .205), ylim=(0, 1), xlabel=r"FGSM $\epsilon$ (normalized input units)",
           ylabel="Metric value", xticks=[0, .05, .10, .15, .20])
    ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    ax.grid(color="0.86", linewidth=.5)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="upper right", frameon=False, fontsize=8,
              handlelength=2, borderpad=.25)
    for i, offset in [(0, (5, 5)), (3, (5, 6)), (5, (-27, 8))]:
        ax.annotate(f"{accuracy[i] * 100:.1f}%", (eps[i], accuracy[i]),
                    xytext=offset, textcoords="offset points", fontsize=8,
                    color="#005577")
    save_figure(fig, output, "fig03_fgsm_curve")


def example(root: Path, output: Path) -> dict:
    batch = root / "artifacts_dataset" / BATCH_ID
    index = json.loads((batch / "index.json").read_text())
    matches = [row for row in index["records"]
               if row["name"] == "100m (2).mat" and row["label"] == "1 NSR"]
    if len(matches) != 1:
        raise ValueError("Example must match exactly one batch record.")
    item = decrypt_record(root, BATCH_ID, matches[0]["record_id"], DEMO_PASSWORD)
    original_bytes = (root / SOURCE).read_bytes()
    original = loadmat(BytesIO(original_bytes))["val"].reshape(-1).astype(np.float32)
    restored = loadmat(BytesIO(item["mat_bytes"]))["val"].reshape(-1).astype(np.float32)
    altered = item["camouflage"]["signal"].reshape(-1).astype(np.float32)
    if original_bytes != item["mat_bytes"] or not np.array_equal(original, restored):
        raise ValueError("Recovery was not byte-exact.")
    checkpoint_path = root / "artifacts_final/model.pt"
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    model = RAMNV2(checkpoint["num_classes"])
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    labels = [label for label, _ in sorted(checkpoint["label_map"].items(), key=lambda pair: pair[1])]
    torch.set_num_threads(2)
    signal = model_input(preprocess(np.stack([original, altered, restored])))
    with torch.inference_mode():
        scores = torch.softmax(model(torch.as_tensor(signal[:, None], dtype=torch.float32)), 1).numpy()
    predictions = [{"label": labels[int(row.argmax())], "score": float(row.max())}
                   for row in scores]
    with (root / "output/paper_results/tables/split_manifest.csv").open(newline="") as stream:
        split_rows = [row for row in csv.DictReader(stream) if row["file"] == SOURCE]
    split = split_rows[0]["split"] if len(split_rows) == 1 else "unknown"
    delta = altered.astype(np.float64) - original.astype(np.float64)
    restoration_delta = restored.astype(np.float64) - original.astype(np.float64)
    time = np.arange(original.size) / 360.0
    fig, axes = plt.subplots(4, 1, figsize=(WIDTH, 4.30), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1, 1, .80]})
    fig.subplots_adjust(left=.175, right=.975, bottom=.105, top=.945, hspace=.67)
    ymin, ymax = float(original.min()), float(original.max())
    padding = (ymax - ymin) * .075
    lines = [original, altered, restored, delta]
    colors = ["#005577", "#b34b18", "#005577", "#444444"]
    titles = [f"(a) Original: NSR, {predictions[0]['score'] * 100:.2f}%",
              f"(b) Public decoy: APB, {predictions[1]['score'] * 100:.2f}%",
              f"(c) Restored: NSR, {predictions[2]['score'] * 100:.2f}%",
              "(d) Public decoy minus original"]
    # These expected labels are an assertion, never a replacement for inference.
    if [p["label"] for p in predictions] != ["1 NSR", "2 APB", "1 NSR"]:
        raise ValueError("The saved example's measured labels have changed; update figure titles.")
    for i, (ax, line, color, title) in enumerate(zip(axes, lines, colors, titles)):
        ax.plot(time, line, color=color, linewidth=.55)
        ax.set_title(title, loc="left", fontsize=8, pad=3)
        ax.set_xlim(0, 10)
        ax.set_xticks(np.arange(0, 11, 2))
        ax.tick_params(labelsize=8, width=.5, length=2, pad=1.5)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(color="0.87", linewidth=.4)
        if i < 3:
            ax.set_ylim(ymin - padding, ymax + padding)
            ax.set_yticks([850, 1000, 1150])
        else:
            bound = float(np.abs(delta).max())
            ax.set_ylim(-bound * 1.15, bound * 1.15)
            ax.set_yticks([-1.5, 0, 1.5])
    axes[-1].set_xlabel("Time (s)", fontsize=8)
    fig.text(.025, .62, "Amplitude (dataset units)", ha="center", va="center",
             rotation=90, fontsize=8)
    axes[-1].set_ylabel(r"$\Delta$ (units)", fontsize=8, labelpad=5)
    save_figure(fig, output, "fig04_camouflage_recovery")
    report = item["camouflage"]["report"]
    return {
        "source": SOURCE, "split_membership": split,
        "example_selection": "User's existing illustrative example; a training fragment, not a held-out robustness estimate.",
        "batch_id": BATCH_ID, "record_id": matches[0]["record_id"],
        "checkpoint_sha256": hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
        "predictions_recomputed_cpu": dict(zip(["original", "public_decoy", "restored"], predictions)),
        "authenticated_creation_report": report,
        "original_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "restored_sha256": hashlib.sha256(item["mat_bytes"]).hexdigest(),
        "bytes_identical": original_bytes == item["mat_bytes"],
        "raw_waveform_identical": bool(np.array_equal(original, restored)),
        "restoration_mse": float(np.mean(restoration_delta ** 2)),
        "restoration_max_absolute_error": float(np.max(np.abs(restoration_delta))),
        "camouflage_mse": float(np.mean(delta ** 2)),
        "camouflage_max_absolute_change": float(np.max(np.abs(delta))),
        "units": "raw dataset amplitude units; no millivolt calibration assumed",
        "score_note": "Softmax model scores are uncalibrated; not disease probabilities.",
        "security_note": "The public altered signal is readable and is not confidential encryption or demonstrated attack prevention.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.project.resolve()
    output = root / "output/ieee_paper/figures"
    output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8,
                         "axes.labelsize": 8, "xtick.labelsize": 8, "ytick.labelsize": 8,
                         "axes.linewidth": .6, "svg.fonttype": "none"})
    results = json.loads((root / "output/paper_results/results.json").read_text())
    workflow(output)
    cm_evidence = confusion(root, output)
    fgsm(results, output)
    example_evidence = example(root, output)
    evidence = {"figures": ["fig01_workflow", "fig02_confusion_matrix", "fig03_fgsm_curve", "fig04_camouflage_recovery"],
                "width_inches": WIDTH, "png_dpi": DPI, "font_size_pt": 8,
                "confusion_matrix": cm_evidence, "fgsm_saved_results": results["robustness"],
                "example": example_evidence}
    (output.parent / "figure_evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
