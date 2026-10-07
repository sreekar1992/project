"""Train a separate, reproducible CWT-MobileNetV2 ECG research experiment.

This command deliberately does not modify ``artifacts_final/model.pt`` and is
not wired into the clinical API.  It trains an isolated image-model artifact
from the MLII fragment directory, using a declared native CWT image transform
and a stratified *fragment-level* validation split.  A fragment-level split
can put fragments from the same source record or person in both partitions;
the resulting metrics are therefore research metrics, **not** a clinical,
patient-level, or deployment performance estimate.

The script uses only repository dependencies: NumPy, SciPy, scikit-learn, and
PyTorch.  It does not use PyWavelets, OpenCV, torchvision, pretrained generic
image weights, a clinical adapter, or a treatment-policy model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import scipy
import torch
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, precision_recall_fscore_support
from sklearn.model_selection import train_test_split
from torch import Tensor, nn
from torch.utils.data import DataLoader, Dataset

from .cwt_image import CWTImageConfig, NativeMorletCWTImageTransform
from .cwt_mobilenetv2 import CWTMobileNetV2
from .data import load_directory_dataset, preprocess


ARTIFACT_FORMAT = "ecg_cvd.cwt_mobilenetv2.experimental.v1"
DEFAULT_VERSION = "cwt-mobilenetv2-experimental-v1"
FRAGMENT_SPLIT_WARNING = (
    "Fragment-level stratified split only. Fragments from the same source record or person may occur "
    "in both partitions; metrics are not a clinical, patient-level, or deployment performance estimate."
)
RESEARCH_SAFETY = (
    "Research-only experimental classifier. Output logits and softmax-derived scores are uncalibrated model "
    "scores, not disease probabilities, diagnoses, triage, treatment recommendations, or prescriptions."
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train a separate CWT-MobileNetV2 ECG research artifact from MLII MAT fragments."
    )
    parser.add_argument(
        "--data",
        type=Path,
        required=True,
        help="MLII directory organized as class label/*.mat (for example, MLII/1 NSR/*.mat).",
    )
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=Path("artifacts/cwt-mobilenetv2-experimental-v1"),
        help="New, empty versioned artifact directory. Existing directories are never overwritten.",
    )
    parser.add_argument("--version", default=DEFAULT_VERSION, help="Version label stored in the manifest.")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--validation-fraction", type=float, default=0.20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sampling-rate", type=float, default=360.0)
    parser.add_argument("--analysis-samples", type=int, default=900)
    parser.add_argument("--number-of-scales", type=int, default=32)
    parser.add_argument("--minimum-scale", type=float, default=1.0)
    parser.add_argument("--maximum-scale", type=float, default=48.0)
    parser.add_argument("--image-size", type=int, default=224)
    parser.add_argument("--width-multiplier", type=float, default=1.0)
    parser.add_argument("--dropout", type=float, default=0.20)
    parser.add_argument(
        "--class-weighting",
        choices=("inverse-frequency", "none"),
        default="inverse-frequency",
        help="Cross-entropy class weighting computed from training fragments only.",
    )
    parser.add_argument(
        "--max-class-weight",
        type=float,
        default=5.0,
        help="Upper cap for inverse-frequency cross-entropy weights; ignored when weighting is none.",
    )
    parser.add_argument(
        "--expected-classes",
        type=int,
        default=17,
        help="Require this number of label directories; default enforces the MLII 17-class experiment.",
    )
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "mps", "cuda"),
        default="auto",
        help="Training device. The artifact always saves CPU tensors for portability.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate data/split/transform and write a preflight manifest without training or a checkpoint.",
    )
    return parser.parse_args(argv)


def _finite_positive(name: str, value: float) -> None:
    if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) <= 0:
        raise ValueError(f"{name} must be a finite positive number.")


def _finite_nonnegative(name: str, value: float) -> None:
    if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError(f"{name} must be a finite non-negative number.")


def validate_args(args: argparse.Namespace) -> None:
    """Reject invalid settings before loading data or writing an artifact directory."""
    if not args.data.is_dir():
        raise ValueError("--data must be an MLII class-directory tree, not an individual MAT file.")
    if not args.version or not args.version.strip():
        raise ValueError("--version must be a non-empty string.")
    for name in ("epochs", "batch_size", "analysis_samples", "number_of_scales", "image_size", "expected_classes"):
        value = getattr(args, name)
        if isinstance(value, bool) or value < 1:
            raise ValueError(f"--{name.replace('_', '-')} must be a positive integer.")
    if args.image_size < 32:
        raise ValueError("--image-size must be at least 32 for MobileNetV2.")
    if args.expected_classes < 2:
        raise ValueError("--expected-classes must be at least 2.")
    for name in ("lr", "sampling_rate", "minimum_scale", "maximum_scale", "width_multiplier"):
        _finite_positive(f"--{name.replace('_', '-')}", getattr(args, name))
    _finite_nonnegative("--weight-decay", args.weight_decay)
    _finite_positive("--max-class-weight", args.max_class_weight)
    if args.maximum_scale < args.minimum_scale:
        raise ValueError("--maximum-scale must be greater than or equal to --minimum-scale.")
    if isinstance(args.dropout, bool) or not math.isfinite(args.dropout) or not 0 <= args.dropout < 1:
        raise ValueError("--dropout must be a finite number in [0, 1).")
    if not 0.0 < args.validation_fraction < 1.0:
        raise ValueError("--validation-fraction must be strictly between 0 and 1.")
    if not 0 <= args.seed < 2**32:
        raise ValueError("--seed must be an integer from 0 through 4294967295.")
    if args.artifacts.exists():
        raise ValueError(
            f"Artifact directory already exists: {args.artifacts}. "
            "Choose a new versioned --artifacts path; this command never overwrites artifacts."
        )


def _json_default(value: object) -> object:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n")


def source_dataset_fingerprint(directory: Path) -> dict[str, Any]:
    """Hash sorted MAT-file bytes and relative paths for reproducible provenance."""
    paths = sorted(directory.rglob("*.mat"))
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(directory).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return {
        "algorithm": "sha256",
        "content_and_relative_path_digest": digest.hexdigest(),
        "n_mat_files": len(paths),
        "root": str(directory.resolve()),
    }


def encode_labels(raw_labels: np.ndarray, expected_classes: int) -> tuple[np.ndarray, dict[str, int]]:
    """Create the persisted lexicographic label map used by the experimental model."""
    labels = np.asarray(raw_labels).astype(str).reshape(-1)
    if labels.size == 0:
        raise ValueError("No labels were loaded from the MLII directory.")
    unique = np.unique(labels)
    if unique.size != expected_classes:
        raise ValueError(
            f"Expected {expected_classes} classes but found {unique.size}: {unique.tolist()}. "
            "Use --expected-classes only for a clearly labelled development experiment."
        )
    class_counts = {label: int(np.count_nonzero(labels == label)) for label in unique}
    too_small = [label for label, count in class_counts.items() if count < 2]
    if too_small:
        raise ValueError(
            "Every class needs at least two fragments for a stratified split; insufficient: "
            + ", ".join(too_small)
        )
    label_map = {str(label): index for index, label in enumerate(unique.tolist())}
    encoded = np.asarray([label_map[label] for label in labels], dtype=np.int64)
    return encoded, label_map


def stratified_fragment_split(
    encoded_labels: np.ndarray, *, validation_fraction: float, seed: int
) -> tuple[np.ndarray, np.ndarray]:
    """Make the declared fragment-level split and validate sklearn's result."""
    indexes = np.arange(len(encoded_labels), dtype=np.int64)
    try:
        train_indexes, validation_indexes = train_test_split(
            indexes,
            test_size=validation_fraction,
            random_state=seed,
            stratify=encoded_labels,
        )
    except ValueError as exc:
        raise ValueError(
            "Unable to create the requested stratified fragment-level split. Increase the number of fragments "
            "per class or adjust --validation-fraction."
        ) from exc
    if np.intersect1d(train_indexes, validation_indexes).size:
        raise RuntimeError("Internal split error: training and validation indexes overlap.")
    return np.sort(train_indexes), np.sort(validation_indexes)


def class_weighting_from_training_fragments(
    encoded_labels: np.ndarray,
    train_indexes: np.ndarray,
    *,
    num_classes: int,
    policy: str,
    maximum_weight: float,
) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Return explicit, capped weights fitted on training fragments only.

    The validation labels are deliberately excluded to prevent a small form of
    evaluation leakage.  The weights balance the loss; they do not rebalance
    the validation set, alter the split, or turn fragment metrics into clinical
    performance estimates.
    """
    if policy not in {"inverse-frequency", "none"}:
        raise ValueError("class weighting policy must be inverse-frequency or none.")
    counts = np.bincount(np.asarray(encoded_labels)[train_indexes], minlength=num_classes)
    if counts.shape != (num_classes,) or np.any(counts == 0):
        raise ValueError("Every class must have at least one training fragment for class weighting.")
    metadata: dict[str, Any] = {
        "policy": policy,
        "fitted_on": "training fragments only",
        "training_class_counts": counts.tolist(),
        "maximum_weight": float(maximum_weight),
    }
    if policy == "none":
        metadata["weights"] = None
        return None, metadata
    raw_weights = float(len(train_indexes)) / (float(num_classes) * counts.astype(np.float64))
    weights = np.minimum(raw_weights, float(maximum_weight)).astype(np.float32)
    metadata["formula"] = "min(n_train / (n_classes * class_count), maximum_weight)"
    metadata["weights"] = [float(value) for value in weights]
    return weights, metadata


class CWTImageDataset(Dataset[tuple[Tensor, Tensor]]):
    """Expose cached grayscale CWT images as deterministic RGB float tensors."""

    def __init__(self, images: np.ndarray, labels: np.ndarray, indexes: np.ndarray) -> None:
        if images.ndim != 3:
            raise ValueError("CWT images must have shape (records, height, width).")
        if len(images) != len(labels):
            raise ValueError("CWT image and label counts must match.")
        self.images = images
        self.labels = labels
        self.indexes = np.asarray(indexes, dtype=np.int64)

    def __len__(self) -> int:
        return int(self.indexes.size)

    def __getitem__(self, position: int) -> tuple[Tensor, Tensor]:
        index = int(self.indexes[position])
        gray = torch.from_numpy(np.asarray(self.images[index])).to(dtype=torch.float32).div_(255.0)
        rgb = gray.unsqueeze(0).repeat(3, 1, 1)
        return rgb, torch.tensor(int(self.labels[index]), dtype=torch.long)


def choose_device(requested: str) -> torch.device:
    """Resolve an available training device without silently falling back."""
    if requested == "auto":
        if torch.backends.mps.is_available():
            return torch.device("mps")
        if torch.cuda.is_available():
            return torch.device("cuda")
        return torch.device("cpu")
    if requested == "mps" and not torch.backends.mps.is_available():
        raise ValueError("--device mps was requested, but MPS is not available in this PyTorch runtime.")
    if requested == "cuda" and not torch.cuda.is_available():
        raise ValueError("--device cuda was requested, but CUDA is not available in this PyTorch runtime.")
    return torch.device(requested)


def configure_reproducibility(seed: int) -> dict[str, Any]:
    """Seed all used RNGs and record the exact reproducibility settings."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
    # ``warn_only`` makes unsupported device kernels visible rather than
    # falling back to a silently different algorithm. The manifest preserves
    # this request; hardware/driver parity is still required for bit equality.
    torch.use_deterministic_algorithms(True, warn_only=True)
    return {
        "seed": seed,
        "python_random_seeded": True,
        "numpy_random_seeded": True,
        "torch_random_seeded": True,
        "torch_deterministic_algorithms": "requested_warn_only",
        "cudnn_benchmark": bool(torch.backends.cudnn.benchmark),
        "cudnn_deterministic": bool(torch.backends.cudnn.deterministic),
        "dataloader_workers": 0,
        "caveat": "Exact bitwise repeatability still depends on PyTorch, OS, CPU/GPU, driver, and accelerator kernels.",
    }


def build_cwt_images(
    processed_signals: np.ndarray, transform: NativeMorletCWTImageTransform
) -> np.ndarray:
    """Create a compact in-memory uint8 CWT cache once before model training."""
    images: list[np.ndarray] = []
    total = len(processed_signals)
    for index, waveform in enumerate(processed_signals, start=1):
        images.append(transform.transform(waveform))
        if index == total or index % 50 == 0:
            print(f"CWT transform {index}/{total}", flush=True)
    return np.stack(images).astype(np.uint8)


def _cpu_state_dict(model: nn.Module) -> dict[str, Tensor]:
    """Make a portable checkpoint even when training on MPS/CUDA."""
    return {name: value.detach().cpu() for name, value in model.state_dict().items()}


def _inverse_label_map(label_map: dict[str, int]) -> list[str]:
    return [label for label, _ in sorted(label_map.items(), key=lambda item: item[1])]


def evaluate(
    model: nn.Module,
    loader: DataLoader[tuple[Tensor, Tensor]],
    device: torch.device,
    class_names: list[str],
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Run validation once and return reproducible aggregate/per-class metrics."""
    model.eval()
    true_labels: list[np.ndarray] = []
    predictions: list[np.ndarray] = []
    with torch.no_grad():
        for inputs, targets in loader:
            logits = model(inputs.to(device))
            true_labels.append(targets.numpy())
            predictions.append(logits.argmax(dim=1).cpu().numpy())
    y_true = np.concatenate(true_labels)
    y_pred = np.concatenate(predictions)
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_true, y_pred, average="weighted", zero_division=0
    )
    macro_precision, macro_recall, macro_f1, _ = precision_recall_fscore_support(
        y_true, y_pred, average="macro", zero_division=0
    )
    class_indexes = list(range(len(class_names)))
    metrics: dict[str, Any] = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision_weighted": float(precision),
        "recall_weighted": float(recall),
        "f1_weighted": float(f1),
        "precision_macro": float(macro_precision),
        "recall_macro": float(macro_recall),
        "f1_macro": float(macro_f1),
        "per_class": classification_report(
            y_true,
            y_pred,
            labels=class_indexes,
            target_names=class_names,
            output_dict=True,
            zero_division=0,
        ),
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=class_indexes).tolist(),
        "warning": FRAGMENT_SPLIT_WARNING,
        "research_safety": RESEARCH_SAFETY,
    }
    return y_true, y_pred, metrics


def _module_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(
    *,
    args: argparse.Namespace,
    data_info: dict[str, Any],
    dataset_fingerprint: dict[str, Any],
    label_map: dict[str, int],
    train_indexes: np.ndarray,
    validation_indexes: np.ndarray,
    transform: NativeMorletCWTImageTransform,
    model: CWTMobileNetV2 | None,
    reproducibility: dict[str, Any],
    class_weighting: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    """Construct a self-contained manifest for either preflight or training."""
    module_root = Path(__file__).resolve().parent
    return {
        "artifact_format": ARTIFACT_FORMAT,
        "artifact_status": status,
        "version": args.version,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "intended_use": "offline ECG rhythm-classification research experiment only",
        "not_used_by": ["clinical API", "RAMNV2 adapter", "prescription workflow", "treatment recommendation"],
        "research_safety": RESEARCH_SAFETY,
        "data": {
            "loader": "ecg_cvd.data.load_directory_dataset",
            "loader_info": data_info,
            "dataset_fingerprint": dataset_fingerprint,
            "n_records": int(len(train_indexes) + len(validation_indexes)),
            "n_classes": len(label_map),
        },
        "label_map_file": "label_map.json",
        "split": {
            "method": "sklearn.model_selection.train_test_split(stratify=encoded_labels)",
            "unit": "fragment",
            "seed": int(args.seed),
            "validation_fraction_requested": float(args.validation_fraction),
            "n_train": int(len(train_indexes)),
            "n_validation": int(len(validation_indexes)),
            "indices_file": "split_indices.npz",
            "warning": FRAGMENT_SPLIT_WARNING,
        },
        "numerical_preprocessing": {
            "function": "ecg_cvd.data.preprocess",
            "sampling_rate_hz": float(args.sampling_rate),
            "filter": "fourth-order zero-phase Butterworth band-pass",
            "band_hz": [0.5, min(45.0, float(args.sampling_rate) / 2.0 - 1.0)],
            "normalization": "per-record median centering and scaled median absolute deviation",
        },
        "cwt_image_transform": transform.config.manifest(),
        "architecture": model.metadata() if model is not None else None,
        "training": {
            "epochs": int(args.epochs),
            "batch_size": int(args.batch_size),
            "optimizer": "AdamW",
            "learning_rate": float(args.lr),
            "weight_decay": float(args.weight_decay),
            "class_weighting": class_weighting,
            "device": str(choose_device(args.device)),
            "no_pretrained_generic_image_weights": True,
            "no_data_augmentation": True,
        },
        "reproducibility": reproducibility,
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "torch": torch.__version__,
            "training_module_sha256": _module_digest(Path(__file__)),
            "cwt_transform_module_sha256": _module_digest(module_root / "cwt_image.py"),
            "model_module_sha256": _module_digest(module_root / "cwt_mobilenetv2.py"),
        },
    }


def _new_artifact_directory(path: Path) -> None:
    """Create one output directory only after all input validation succeeds."""
    path.mkdir(parents=True, exist_ok=False)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    validate_args(args)
    reproducibility = configure_reproducibility(args.seed)

    signals, raw_labels, data_info = load_directory_dataset(args.data)
    encoded_labels, label_map = encode_labels(raw_labels, args.expected_classes)
    train_indexes, validation_indexes = stratified_fragment_split(
        encoded_labels, validation_fraction=args.validation_fraction, seed=args.seed
    )
    dataset_fingerprint = source_dataset_fingerprint(args.data)
    class_weights, class_weighting = class_weighting_from_training_fragments(
        encoded_labels,
        train_indexes,
        num_classes=len(label_map),
        policy=args.class_weighting,
        maximum_weight=args.max_class_weight,
    )
    transform = NativeMorletCWTImageTransform(
        CWTImageConfig(
            source_sampling_rate_hz=args.sampling_rate,
            analysis_samples=args.analysis_samples,
            minimum_scale=args.minimum_scale,
            maximum_scale=args.maximum_scale,
            number_of_scales=args.number_of_scales,
            image_size=args.image_size,
        )
    )

    # The preflight model validates image geometry and serializes exactly the
    # architecture that a real run will construct. It has random initialized
    # weights and is never saved by --dry-run.
    model = CWTMobileNetV2(
        num_classes=len(label_map),
        image_height=args.image_size,
        image_width=args.image_size,
        width_multiplier=args.width_multiplier,
        dropout=args.dropout,
    )
    _new_artifact_directory(args.artifacts)
    (args.artifacts / "label_map.json").write_text(
        json.dumps(label_map, indent=2, sort_keys=True) + "\n"
    )
    np.savez_compressed(
        args.artifacts / "split_indices.npz",
        train_indexes=train_indexes,
        validation_indexes=validation_indexes,
    )

    if args.dry_run:
        manifest = build_manifest(
            args=args,
            data_info=data_info,
            dataset_fingerprint=dataset_fingerprint,
            label_map=label_map,
            train_indexes=train_indexes,
            validation_indexes=validation_indexes,
            transform=transform,
            model=model,
            reproducibility=reproducibility,
            class_weighting=class_weighting,
            status="preflight_only_no_checkpoint",
        )
        manifest["preflight"] = {
            "validated_first_record_cwt_image_shape": list(transform.transform(preprocess(signals[:1], fs=args.sampling_rate)[0]).shape),
            "checkpoint_written": False,
            "reason": "--dry-run was selected; no training, model weights, or metrics were generated.",
        }
        write_json(args.artifacts / "manifest.json", manifest)
        print(json.dumps({"artifact": str(args.artifacts), "status": manifest["artifact_status"], "warning": FRAGMENT_SPLIT_WARNING}, indent=2), flush=True)
        return

    print("Preprocessing numerical ECG records for the declared CWT input contract…", flush=True)
    processed_signals = preprocess(signals, fs=args.sampling_rate)
    images = build_cwt_images(processed_signals, transform)
    if images.shape != (len(signals), args.image_size, args.image_size):
        raise RuntimeError(f"Unexpected CWT image cache shape: {images.shape}")

    device = choose_device(args.device)
    model = model.to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    loss_weight_tensor = (
        torch.from_numpy(class_weights).to(device) if class_weights is not None else None
    )
    loss_function = nn.CrossEntropyLoss(weight=loss_weight_tensor)
    train_dataset = CWTImageDataset(images, encoded_labels, train_indexes)
    validation_dataset = CWTImageDataset(images, encoded_labels, validation_indexes)
    generator = torch.Generator().manual_seed(args.seed)
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, generator=generator, num_workers=0)
    validation_loader = DataLoader(validation_dataset, batch_size=args.batch_size, shuffle=False, num_workers=0)

    history: list[dict[str, float | int]] = []
    for epoch in range(1, args.epochs + 1):
        model.train()
        losses: list[float] = []
        for inputs, targets in train_loader:
            optimizer.zero_grad(set_to_none=True)
            logits = model(inputs.to(device))
            loss = loss_function(logits, targets.to(device))
            loss.backward()
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
        mean_loss = float(np.mean(losses))
        history.append({"epoch": epoch, "training_loss": mean_loss})
        print(f"epoch {epoch:03d}/{args.epochs}: loss={mean_loss:.4f}", flush=True)

    class_names = _inverse_label_map(label_map)
    _y_true, _y_pred, metrics = evaluate(model, validation_loader, device, class_names)
    metrics.update(
        {
            "n_train": int(len(train_indexes)),
            "n_validation": int(len(validation_indexes)),
            "history": history,
            "artifact_format": ARTIFACT_FORMAT,
            "version": args.version,
        }
    )
    manifest = build_manifest(
        args=args,
        data_info=data_info,
        dataset_fingerprint=dataset_fingerprint,
        label_map=label_map,
        train_indexes=train_indexes,
        validation_indexes=validation_indexes,
        transform=transform,
        model=model,
        reproducibility=reproducibility,
        class_weighting=class_weighting,
        status="trained_experimental",
    )
    manifest["checkpoint"] = {
        "file": "cwt_mobilenetv2_experimental.pt",
        "format": ARTIFACT_FORMAT,
        "state_dict_key": "state_dict",
        "portable_tensors": "CPU",
        "metrics_file": "metrics.json",
    }
    checkpoint = {
        "artifact_format": ARTIFACT_FORMAT,
        "artifact_status": "trained_experimental",
        "version": args.version,
        "state_dict": _cpu_state_dict(model),
        "architecture": model.metadata(),
        "label_map": label_map,
        "cwt_image_transform": transform.config.manifest(),
        "numerical_preprocessing": manifest["numerical_preprocessing"],
        "split": manifest["split"],
        "training": manifest["training"],
        "research_safety": RESEARCH_SAFETY,
        "metrics_file": "metrics.json",
        "manifest_file": "manifest.json",
    }
    torch.save(checkpoint, args.artifacts / "cwt_mobilenetv2_experimental.pt")
    write_json(args.artifacts / "metrics.json", metrics)
    write_json(args.artifacts / "manifest.json", manifest)
    print(json.dumps(metrics, indent=2, default=_json_default), flush=True)
    print(f"Saved separate experimental artifact to {args.artifacts / 'cwt_mobilenetv2_experimental.pt'}", flush=True)


if __name__ == "__main__":
    main()
