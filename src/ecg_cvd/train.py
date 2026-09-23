from __future__ import annotations
import argparse, json, math
from pathlib import Path
import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_recall_fscore_support, confusion_matrix
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from .data import MODEL_DOWNSAMPLE, load_directory_dataset, load_mat_dataset, model_input, preprocess, save_label_map
from .model import RAMNV2
from .robustness import fgsm


def parse_args():
    p = argparse.ArgumentParser(description="Train ECG residual-attention research baseline.")
    p.add_argument("--data", type=Path, required=True, help="Extracted Mendeley MLII directory or a source .mat file")
    p.add_argument("--signal-key"); p.add_argument("--label-key")
    p.add_argument("--epochs", type=int, default=30); p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--lr", type=float, default=1e-3); p.add_argument("--seed", type=int, default=42)
    p.add_argument("--adversarial-epsilon", type=float, default=0.0,
                   help="FGSM amplitude used during adversarial training; 0 disables it")
    p.add_argument("--adversarial-weight", type=float, default=0.5,
                   help="Fraction of the loss evaluated on adversarial samples")
    p.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    p.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")
    return p.parse_args()


def validate_args(args):
    """Reject invalid training settings before reading data or creating outputs."""
    if args.epochs < 1:
        raise ValueError("epochs must be a positive integer")
    if args.batch_size < 1:
        raise ValueError("batch-size must be a positive integer")
    if not math.isfinite(args.lr) or args.lr <= 0:
        raise ValueError("lr must be finite and positive")
    if not math.isfinite(args.adversarial_epsilon) or args.adversarial_epsilon < 0:
        raise ValueError("adversarial-epsilon must be finite and non-negative")
    if not math.isfinite(args.adversarial_weight) or not 0 <= args.adversarial_weight <= 1:
        raise ValueError("adversarial-weight must be between 0 and 1")
    if not 0 <= args.seed < 2**32:
        raise ValueError("seed must be an integer from 0 to 4294967295")


def main():
    args = parse_args()
    validate_args(args)
    np.random.seed(args.seed); torch.manual_seed(args.seed)
    if args.data.is_dir():
        signals, raw_labels, info = load_directory_dataset(args.data)
    else:
        signals, raw_labels, info = load_mat_dataset(args.data, args.signal_key, args.label_key)
    signals = model_input(preprocess(signals))
    _, encoded = np.unique(raw_labels, return_inverse=True)
    args.artifacts.mkdir(parents=True, exist_ok=True)
    label_map = save_label_map(raw_labels, args.artifacts / "label_map.json")
    xtr, xva, ytr, yva = train_test_split(signals, encoded, test_size=.2, random_state=args.seed, stratify=encoded)
    train = DataLoader(TensorDataset(torch.from_numpy(xtr[:, None]), torch.from_numpy(ytr).long()), args.batch_size, shuffle=True)
    device = torch.device(("mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu")
                          if args.device == "auto" else args.device)
    model = RAMNV2(len(label_map)).to(device); opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    loss_fn = nn.CrossEntropyLoss()
    for epoch in range(1, args.epochs + 1):
        model.train(); losses=[]
        for x, y in train:
            x,y=x.to(device),y.to(device); opt.zero_grad()
            clean_loss = loss_fn(model(x), y)
            if args.adversarial_epsilon and args.adversarial_weight:
                model.eval()
                try:
                    adversarial_x = fgsm(model, x, y, args.adversarial_epsilon)
                finally:
                    model.train()
                adversarial_loss = loss_fn(model(adversarial_x), y)
                loss = ((1 - args.adversarial_weight) * clean_loss +
                        args.adversarial_weight * adversarial_loss)
            else:
                loss = clean_loss
            loss.backward(); opt.step(); losses.append(loss.item())
        print(f"epoch {epoch:03d}/{args.epochs}: loss={np.mean(losses):.4f}", flush=True)
    model.eval()
    with torch.no_grad():
        pred = np.concatenate([
            model(x.to(device)).argmax(1).cpu().numpy()
            for (x,) in DataLoader(TensorDataset(torch.from_numpy(xva[:, None])), args.batch_size)
        ])
    precision, recall, f1, _ = precision_recall_fscore_support(yva, pred, average="weighted", zero_division=0)
    metrics = {"accuracy": accuracy_score(yva,pred), "precision_weighted": precision, "recall_weighted": recall,
               "f1_weighted": f1, "n_train": len(ytr), "n_validation": len(yva), "data": info,
               "adversarial_training": {"epsilon": args.adversarial_epsilon, "weight": args.adversarial_weight},
               "warning": "Fragment-level split only; not a clinical performance estimate."}
    (args.artifacts / "metrics.json").write_text(json.dumps(metrics, indent=2, default=float))
    training_config = {"seed": args.seed, "epochs": args.epochs, "batch_size": args.batch_size,
                       "lr": args.lr, "adversarial_epsilon": args.adversarial_epsilon,
                       "adversarial_weight": args.adversarial_weight, "device": str(device)}
    torch.save({"state_dict": model.state_dict(), "num_classes": len(label_map), "label_map": label_map,
                "training_config": training_config,
                "split": {"seed": args.seed, "validation_fraction": 0.2, "stratified": True,
                          "unit": "fragment", "n_train": len(ytr), "n_validation": len(yva)},
                "preprocessing": {"sampling_rate_hz": 360, "filter": "butterworth_bandpass",
                                  "filter_order": 4, "band_hz": [0.5, 45.0],
                                  "normalization": "per-record median and scaled median absolute deviation",
                                  "downsample_factor": MODEL_DOWNSAMPLE}}, args.artifacts / "model.pt")
    fig,ax=plt.subplots(figsize=(8,7)); ax.imshow(confusion_matrix(yva,pred), cmap="Blues"); ax.set(title="Validation confusion matrix", xlabel="Predicted", ylabel="True"); fig.tight_layout(); fig.savefig(args.artifacts / "confusion_matrix.png", dpi=160); plt.close(fig)
    print(json.dumps(metrics, indent=2, default=float), flush=True); print(f"Saved model to {args.artifacts / 'model.pt'}", flush=True)

if __name__ == "__main__": main()
