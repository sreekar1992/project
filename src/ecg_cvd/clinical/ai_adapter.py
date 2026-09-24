"""Adapter around the project’s real ECG model; no synthetic predictions."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import hashlib
import json
from pathlib import Path
from threading import RLock
import time
from typing import Any

import numpy as np
import torch
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from ecg_cvd.data import model_input, preprocess
from ecg_cvd.explain import grad_cam
from ecg_cvd.gui import class_name, read_signal
from ecg_cvd.model import RAMNV2


@dataclass(frozen=True)
class ModelDescriptor:
    model_name: str
    version: str
    path: Path
    sha256: str
    metrics: dict[str, Any]
    preprocessing: dict[str, Any]


@dataclass(frozen=True)
class InferenceResult:
    report: dict[str, Any]
    explanation_png: bytes


class ECGModelAdapter:
    """Checkpoint-safe inference service shared by the clinical API and worker.

    The exact waveform validation/preprocessing/RAMNV2/Grad-CAM functions are
    imported from the existing repository.  Checkpoints are loaded with
    `weights_only=True`, and labels are ordered by numeric `label_map` index.
    """

    def __init__(self, model_path: Path, configured_version: str | None = None):
        self.model_path = model_path.resolve()
        self.configured_version = configured_version
        self._lock = RLock()
        self._signature: tuple[str, int, int] | None = None
        self._model: RAMNV2 | None = None
        self._labels: list[str] = []

    def _load(self) -> tuple[RAMNV2, list[str]]:
        if not self.model_path.is_file():
            raise ValueError("The configured ECG model artifact is unavailable.")
        signature = (str(self.model_path), self.model_path.stat().st_mtime_ns, self.model_path.stat().st_size)
        with self._lock:
            if signature != self._signature:
                checkpoint = torch.load(self.model_path, map_location="cpu", weights_only=True)
                mapping = checkpoint.get("label_map")
                count = checkpoint.get("num_classes")
                if not isinstance(mapping, dict) or not isinstance(count, int):
                    raise ValueError("Configured checkpoint lacks the supported model metadata.")
                if sorted(mapping.values()) != list(range(count)):
                    raise ValueError("Configured checkpoint has an invalid contiguous label map.")
                model = RAMNV2(count)
                model.load_state_dict(checkpoint["state_dict"])
                model.eval()
                self._model = model
                self._labels = [label for label, _ in sorted(mapping.items(), key=lambda item: item[1])]
                self._signature = signature
        assert self._model is not None
        return self._model, self._labels

    def descriptor(self) -> ModelDescriptor:
        if not self.model_path.is_file():
            raise ValueError("The configured ECG model artifact is unavailable.")
        checkpoint = torch.load(self.model_path, map_location="cpu", weights_only=True)
        metrics_path = self.model_path.parent / "metrics.json"
        try:
            metrics = json.loads(metrics_path.read_text()) if metrics_path.is_file() else {}
        except (OSError, ValueError):
            metrics = {}
        preprocessing = checkpoint.get("preprocessing")
        if not isinstance(preprocessing, dict):
            preprocessing = {
                "filter": "4th-order zero-phase 0.5-45 Hz Butterworth",
                "normalization": "median/MAD",
                "downsample": "every fourth sample (360 Hz to 90 Hz)",
                "input_samples": 3600,
                "model_samples": 900,
            }
        sha256 = hashlib.sha256(self.model_path.read_bytes()).hexdigest()
        version = self.configured_version or str(checkpoint.get("training_config", {}).get("version")
                                                 or f"sha256-{sha256[:12]}")
        return ModelDescriptor(model_name="RAMNV2 ECG research classifier", version=version,
                               path=self.model_path, sha256=sha256, metrics=metrics,
                               preprocessing=preprocessing)

    @staticmethod
    def validate(payload: bytes, filename: str) -> np.ndarray:
        """Validate through the existing accepted-file interface without inference."""
        return read_signal(payload, filename)

    def predict(self, payload: bytes, filename: str, include_explanation: bool = True) -> InferenceResult:
        started = time.perf_counter()
        raw = read_signal(payload, filename)
        model, labels = self._load()
        signal = model_input(preprocess(raw[None]))[0]
        with self._lock, torch.no_grad():
            probabilities = torch.softmax(model(torch.from_numpy(signal[None, None])), dim=1)[0].numpy()
        order = np.argsort(-probabilities, kind="stable")
        selected = int(order[0])
        explanation = b""
        if include_explanation:
            output = BytesIO()
            with self._lock:
                grad_cam(model, signal, selected, output, fs=90.0)
            explanation = output.getvalue()
        report = {
            "sample_name": Path(filename).name,
            "label": labels[selected],
            "label_name": class_name(labels[selected]),
            "confidence": float(probabilities[selected]),
            "samples": 3600,
            "sampling_rate_hz": 360,
            "duration_seconds": 10,
            "model_sampling_rate_hz": 90,
            "top_predictions": [
                {"label": labels[int(index)], "label_name": class_name(labels[int(index)]),
                 "probability": float(probabilities[index])}
                for index in order[:3]
            ],
            "note": "Research/clinical-decision-support output only. The model score is not a calibrated "
                    "disease probability and requires qualified clinician review.",
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
        return InferenceResult(report=report, explanation_png=explanation)

    def render_waveform(self, raw: np.ndarray) -> bytes:
        """Render the authorized, original 360 Hz waveform without inference.

        A separate Figure instance keeps this clinical rendering path free from
        the legacy encryption/camouflage UI and its public demo password.
        """
        waveform = np.asarray(raw, dtype=np.float32).reshape(-1)
        if waveform.size != 3600 or not np.isfinite(waveform).all():
            raise ValueError("Waveform rendering requires 3,600 finite samples.")
        with self._lock:
            figure = Figure(figsize=(10, 3), dpi=140, facecolor="#ffffff")
            FigureCanvasAgg(figure)
            axis = figure.add_subplot(111)
            axis.plot(np.arange(waveform.size) / 360.0, waveform, color="#075d76", linewidth=0.9)
            axis.set(xlim=(0, 10), xlabel="Time (seconds)", ylabel="Raw amplitude (dataset units)",
                     title="Authorized ECG waveform")
            axis.grid(alpha=0.18, color="#94a3b8")
            axis.spines[["top", "right"]].set_visible(False)
            figure.subplots_adjust(left=0.1, right=0.98, bottom=0.22, top=0.88)
            output = BytesIO()
            figure.savefig(output, format="png", metadata={"Software": "ECG health platform"})
            return output.getvalue()
