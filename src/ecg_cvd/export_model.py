"""Export a verified RAMNV2 research checkpoint as portable model artifacts.

The native project artifact remains the PyTorch ``model.pt`` checkpoint.  This
module packages that checkpoint with the numeric label map, preprocessing
contract, provenance metadata, and checksums.  TorchScript and ONNX are
optional inference exports; neither changes the model's required 900-sample
preprocessed input or makes it clinically validated.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Iterable
import uuid

import torch

from .model import RAMNV2


EXPORT_SCHEMA_VERSION = 1
INPUT_SHAPE = (1, 1, 900)
SUPPORTED_FORMATS = frozenset({"bundle", "torchscript", "onnx", "all"})

_COMPATIBILITY_PREPROCESSING = {
    "sampling_rate_hz": 360,
    "input_samples": 3600,
    "duration_seconds": 10,
    "lead_count": 1,
    "filter": "4th-order zero-phase Butterworth band-pass",
    "band_hz": [0.5, 45.0],
    "normalization": "per-record median and scaled median absolute deviation",
    "downsample_factor": 4,
    "model_sampling_rate_hz": 90,
    "model_input_samples": 900,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_default(value: object) -> object:
    """Make incidental numeric metadata serializable without hiding tensors."""
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Unsupported metadata value: {type(value).__name__}")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=_json_default) + "\n", encoding="utf-8")


def _label_map(checkpoint: dict[str, Any]) -> tuple[int, dict[str, int], list[str]]:
    count = checkpoint.get("num_classes")
    mapping = checkpoint.get("label_map")
    if isinstance(count, bool) or not isinstance(count, int) or count < 2:
        raise ValueError("Checkpoint has an invalid num_classes value.")
    if not isinstance(mapping, dict) or len(mapping) != count:
        raise ValueError("Checkpoint must contain a label_map with one entry per class.")
    normalized: dict[str, int] = {}
    for label, index in mapping.items():
        if not isinstance(label, str) or isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("Checkpoint label_map entries must map string labels to integer indexes.")
        normalized[label] = index
    if sorted(normalized.values()) != list(range(count)):
        raise ValueError("Checkpoint label_map indexes must be contiguous from 0 through num_classes - 1.")
    labels = [label for label, _ in sorted(normalized.items(), key=lambda item: item[1])]
    return count, normalized, labels


def _load_model(checkpoint_path: Path) -> tuple[dict[str, Any], RAMNV2, dict[str, int], list[str]]:
    if not checkpoint_path.is_file():
        raise FileNotFoundError(f"Checkpoint does not exist: {checkpoint_path}")
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("state_dict"), dict):
        raise ValueError("Checkpoint must be a trusted RAMNV2 state-dict artifact.")
    count, mapping, labels = _label_map(checkpoint)
    model = RAMNV2(count)
    model.load_state_dict(checkpoint["state_dict"], strict=True)
    model.eval()
    return checkpoint, model, mapping, labels


def _requested_formats(values: Iterable[str]) -> set[str]:
    requested = {value.lower() for value in values}
    invalid = requested.difference(SUPPORTED_FORMATS)
    if invalid:
        raise ValueError("Unsupported export format: " + ", ".join(sorted(invalid)))
    if "all" in requested:
        requested = {"bundle", "torchscript", "onnx"}
    # A portable bundle is always created, even if the caller asks only for a
    # runtime format.  It preserves labels/provenance needed for safe reuse.
    requested.add("bundle")
    return requested


def _preprocessing(checkpoint: dict[str, Any]) -> tuple[dict[str, Any], str]:
    saved = checkpoint.get("preprocessing")
    if isinstance(saved, dict):
        return dict(saved), "checkpoint"
    return dict(_COMPATIBILITY_PREPROCESSING), "checkpoint-compatible project default"


def _export_torchscript(model: RAMNV2, directory: Path, example: torch.Tensor) -> dict[str, Any]:
    path = directory / "model.ts"
    with torch.no_grad():
        traced = torch.jit.trace(model, example, strict=True)
        traced.save(str(path))
        restored = torch.jit.load(str(path), map_location="cpu").eval()
        maximum_error = float((model(example) - restored(example)).abs().max().item())
    if maximum_error > 1e-5:
        raise RuntimeError(f"TorchScript verification failed; maximum logit difference was {maximum_error}.")
    return {"path": path.name, "input_name": "ecg", "output_name": "logits", "max_logit_error": maximum_error}


def _export_onnx(model: RAMNV2, directory: Path, example: torch.Tensor) -> dict[str, Any]:
    try:
        import onnx
        import onnxscript  # PyTorch's current exporter imports this package.
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ONNX export requires the optional dependency. Install it with: python -m pip install -e '.[export]'"
        ) from exc
    path = directory / "model.onnx"
    try:
        with torch.no_grad():
            torch.onnx.export(
                model,
                example,
                str(path),
                input_names=["ecg"],
                output_names=["logits"],
                opset_version=17,
                do_constant_folding=True,
            )
        onnx.checker.check_model(onnx.load(str(path)))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {
        "path": path.name,
        "input_name": "ecg",
        "input_shape": list(INPUT_SHAPE),
        "output_name": "logits",
        "opset": 17,
        "validation": "onnx.checker.check_model",
        "dynamic_shapes": False,
    }


def export_checkpoint(
    checkpoint_path: str | Path,
    output_directory: str | Path,
    formats: Iterable[str] = ("bundle",),
) -> dict[str, Any]:
    """Create a new verified export directory and return its manifest.

    Existing directories are rejected rather than overwritten, so a successful
    export is immutable by convention and can be associated with its checksum.
    """
    source = Path(checkpoint_path).expanduser().resolve()
    destination = Path(output_directory).expanduser().resolve()
    requested = _requested_formats(formats)
    if destination.exists():
        raise FileExistsError(f"Export directory already exists: {destination}")
    if destination == source.parent:
        raise ValueError("The export directory must differ from the source checkpoint directory.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.exporting-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        model_path = temporary / "model.pt"
        shutil.copy2(source, model_path)
        # Validate the copied payload rather than a separately read source file.
        # This makes the manifest and the exported bytes describe the same
        # checkpoint even if the source is replaced while an export is running.
        checkpoint, model, mapping, labels = _load_model(model_path)
        _write_json(temporary / "label_map.json", mapping)

        copied_files = {
            "model.pt": {"sha256": _sha256(model_path), "bytes": model_path.stat().st_size},
            "label_map.json": {"sha256": _sha256(temporary / "label_map.json"),
                               "bytes": (temporary / "label_map.json").stat().st_size},
        }
        metrics_path = source.parent / "metrics.json"
        if metrics_path.is_file():
            copied_metrics = temporary / "metrics.json"
            shutil.copy2(metrics_path, copied_metrics)
            copied_files[copied_metrics.name] = {"sha256": _sha256(copied_metrics), "bytes": copied_metrics.stat().st_size}

        example = torch.zeros(INPUT_SHAPE, dtype=torch.float32)
        runtime_exports: dict[str, dict[str, Any]] = {}
        if "torchscript" in requested:
            runtime_exports["torchscript"] = _export_torchscript(model, temporary, example)
            file = temporary / runtime_exports["torchscript"]["path"]
            copied_files[file.name] = {"sha256": _sha256(file), "bytes": file.stat().st_size}
        if "onnx" in requested:
            runtime_exports["onnx"] = _export_onnx(model, temporary, example)
            file = temporary / runtime_exports["onnx"]["path"]
            copied_files[file.name] = {"sha256": _sha256(file), "bytes": file.stat().st_size}

        preprocessing, preprocessing_source = _preprocessing(checkpoint)
        manifest: dict[str, Any] = {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_checkpoint": {"filename": source.name, "sha256": copied_files["model.pt"]["sha256"]},
            "architecture": {"name": "RAMNV2", "num_classes": len(labels)},
            "labels": labels,
            "label_map": mapping,
            "input_tensor": {"shape": list(INPUT_SHAPE), "dtype": "float32", "layout": "batch, channel, samples"},
            "preprocessing": preprocessing,
            "preprocessing_source": preprocessing_source,
            "training_config": checkpoint.get("training_config"),
            "split": checkpoint.get("split"),
            "files": copied_files,
            "runtime_exports": runtime_exports,
            "safety": {
                "intended_use": "ECG rhythm-classification research and qualified clinician review only",
                "not_for": "autonomous diagnosis, treatment recommendation, prescription, or clinical probability estimation",
                "score_note": "Softmax scores are uncalibrated model outputs.",
            },
        }
        # A manifest cannot safely contain its own digest: adding it would
        # change the bytes to be hashed. Its file list therefore covers the
        # export payloads; callers can hash this manifest separately.
        _write_json(temporary / "model_manifest.json", manifest)
        temporary.rename(destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a verified ECG RAMNV2 checkpoint as a portable research-model bundle."
    )
    parser.add_argument("--checkpoint", type=Path, required=True, help="Source PyTorch model.pt checkpoint")
    parser.add_argument("--output", type=Path, required=True, help="New export directory; it must not already exist")
    parser.add_argument(
        "--format",
        action="append",
        choices=sorted(SUPPORTED_FORMATS),
        default=[],
        help="Repeat for multiple formats. A PyTorch bundle is always included; default: bundle.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    formats = args.format or ["bundle"]
    manifest = export_checkpoint(args.checkpoint, args.output, formats)
    print(json.dumps({
        "export_directory": str(Path(args.output).resolve()),
        "checkpoint_sha256": manifest["source_checkpoint"]["sha256"],
        "formats": ["bundle", *manifest["runtime_exports"].keys()],
        "manifest": str(Path(args.output).resolve() / "model_manifest.json"),
    }, indent=2))


if __name__ == "__main__":
    main()
