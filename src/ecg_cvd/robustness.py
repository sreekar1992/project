"""Adversarial robustness evaluation for the 1-D ECG classifier.

This is a ResilienceNet/RNAF-inspired *evaluation and training* layer. It is
not encryption and should not be described as cryptographic protection.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Callable

import numpy as np
import torch
import torch.nn.functional as F
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader, TensorDataset

from .data import load_directory_dataset, load_mat_dataset, model_input, preprocess
from .model import RAMNV2


def fgsm(model: torch.nn.Module, x: torch.Tensor, y: torch.Tensor, epsilon: float) -> torch.Tensor:
    """Perturb normalized ECG inputs without changing model parameter gradients.

    Robust normalization has no fixed amplitude range. Clipping the signal to
    an arbitrary range would violate the epsilon bound for inputs outside it.
    The caller controls model mode (evaluation mode is normally appropriate).
    """
    if not math.isfinite(epsilon) or epsilon < 0:
        raise ValueError("epsilon must be finite and non-negative")
    if epsilon == 0:
        return x.detach().clone()
    with torch.enable_grad():
        x_attack = x.detach().clone().requires_grad_(True)
        loss = F.cross_entropy(model(x_attack), y)
        gradient, = torch.autograd.grad(loss, x_attack)
    return (x_attack.detach() + epsilon * gradient.sign()).detach()


def evaluate_robustness(
    data: str | Path,
    checkpoint: str | Path,
    epsilon: float,
    seed: int | None = None,
    batch_size: int = 32,
    progress: Callable[[int, int], None] | None = None,
    *,
    signal_key: str | None = None,
    label_key: str | None = None,
) -> dict:
    """Evaluate bounded FGSM in batches on the checkpoint's validation split.

    ``progress(completed_batches, total_batches)`` runs after each batch. A
    supplied seed overrides checkpoint metadata. Legacy checkpoints use 42,
    matching the original training CLI default, and report that assumption.
    """
    if not math.isfinite(epsilon) or epsilon < 0:
        raise ValueError("epsilon must be finite and non-negative")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    saved = torch.load(checkpoint, map_location="cpu", weights_only=True)
    saved_seed = saved.get("split", {}).get("seed", saved.get("training_config", {}).get("seed"))
    if seed is not None:
        split_seed, seed_source = seed, "explicit override"
    elif saved_seed is not None:
        split_seed, seed_source = saved_seed, "checkpoint metadata"
    else:
        split_seed, seed_source = 42, "legacy assumption (original training default)"
    if isinstance(split_seed, bool) or not isinstance(split_seed, int) or not 0 <= split_seed < 2**32:
        raise ValueError("seed must be an integer from 0 to 4294967295")

    data = Path(data)
    if data.is_dir():
        signals, raw_labels, _ = load_directory_dataset(data)
    else:
        signals, raw_labels, _ = load_mat_dataset(data, signal_key, label_key)
    signals = model_input(preprocess(signals))
    label_to_index = saved["label_map"]
    num_classes = saved["num_classes"]
    if (not isinstance(label_to_index, dict) or len(label_to_index) != num_classes
            or any(type(value) is not int for value in label_to_index.values())
            or set(label_to_index.values()) != set(range(num_classes))):
        raise ValueError("Checkpoint label_map must map labels to unique contiguous class indices")
    try:
        labels = np.asarray([label_to_index[str(label)] for label in raw_labels], dtype=np.int64)
    except KeyError as exc:
        raise ValueError(f"Dataset label is absent from checkpoint map: {exc}") from exc
    validation_fraction = saved.get("split", {}).get("validation_fraction", 0.2)
    _, x_validation, _, y_validation = train_test_split(
        signals, labels, test_size=validation_fraction, random_state=split_seed, stratify=labels
    )
    model = RAMNV2(num_classes)
    model.load_state_dict(saved["state_dict"])
    model.eval()
    batches = DataLoader(TensorDataset(
        torch.from_numpy(x_validation[:, None]), torch.from_numpy(y_validation)
    ), batch_size=batch_size)
    clean_correct = adversarial_correct = 0
    max_perturbation = 0.0
    for completed, (x, y) in enumerate(batches, 1):
        with torch.no_grad():
            clean_correct += int((model(x).argmax(1) == y).sum())
        attacked = fgsm(model, x, y, epsilon)
        max_perturbation = max(max_perturbation, float((attacked - x).abs().max()))
        with torch.no_grad():
            adversarial_correct += int((model(attacked).argmax(1) == y).sum())
        if progress is not None:
            progress(completed, len(batches))
    clean_accuracy = clean_correct / len(y_validation)
    adversarial_accuracy = adversarial_correct / len(y_validation)
    return {
        "attack": "FGSM", "epsilon": epsilon, "n_validation": len(y_validation),
        "clean_accuracy": clean_accuracy, "adversarial_accuracy": adversarial_accuracy,
        "accuracy_drop": clean_accuracy - adversarial_accuracy,
        "max_absolute_perturbation": max_perturbation,
        "perturbation_domain": "normalized, downsampled ECG amplitude",
        "split_seed": split_seed, "split_seed_source": seed_source,
        "validation_fraction": validation_fraction,
        "warning": "Fragment-level research stress test only; not a clinical or cryptographic security guarantee.",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate ECG classifier resilience using FGSM attacks.")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--signal-key"); parser.add_argument("--label-key")
    parser.add_argument("--epsilon", type=float, default=0.05, help="Maximum normalized-amplitude perturbation")
    parser.add_argument("--seed", type=int, default=None,
                        help="Override checkpoint split seed (legacy checkpoints default to 42)")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--output", type=Path, default=Path("artifacts/robustness.json"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = evaluate_robustness(
        args.data, args.checkpoint, args.epsilon, seed=args.seed,
        batch_size=args.batch_size, signal_key=args.signal_key, label_key=args.label_key,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
