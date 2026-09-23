from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import numpy as np
from scipy.io import loadmat
from scipy.signal import butter, sosfiltfilt

MODEL_DOWNSAMPLE = 4

def _numeric_arrays(mat: dict[str, Any]) -> dict[str, np.ndarray]:
    return {k: np.asarray(v).squeeze() for k, v in mat.items()
            if not k.startswith("__") and isinstance(v, np.ndarray) and np.issubdtype(v.dtype, np.number)}


def load_mat_dataset(path: str | Path, signal_key: str | None = None,
                     label_key: str | None = None) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Load a conventional MATLAB ECG matrix and labels.

    Auto-detection chooses the largest 2-D numeric array and a 1-D array whose
    length matches its record count. Use explicit keys when the source layout differs.
    """
    path = Path(path)
    mat = _numeric_arrays(loadmat(path))
    if not mat:
        raise ValueError(f"No numeric arrays found in {path}; MATLAB v7.3 files require h5py support.")
    if signal_key:
        if signal_key not in mat:
            raise KeyError(f"{signal_key!r} not found. Available keys: {list(mat)}")
        signals = mat[signal_key]
    else:
        candidates = [a for a in mat.values() if a.ndim == 2 and min(a.shape) > 100]
        if not candidates:
            raise ValueError(f"Cannot identify waveform matrix. Available keys: {list(mat)}")
        signals = max(candidates, key=lambda a: a.size)
    if signals.ndim != 2:
        raise ValueError("Waveform matrix must be 2-D.")
    # Dataset convention is samples x recordings; normalize to recordings x samples.
    if signals.shape[0] > signals.shape[1]:
        signals = signals.T
    n = signals.shape[0]
    if label_key:
        labels = mat[label_key]
    else:
        candidates = [a for a in mat.values() if a.ndim == 1 and len(a) == n]
        if not candidates:
            raise ValueError("Cannot identify labels. Supply --label-key. Available keys: " + str(list(mat)))
        labels = candidates[0]
    labels = np.asarray(labels).reshape(-1)
    if len(labels) != n:
        raise ValueError(f"Expected {n} labels, found {len(labels)}.")
    return signals.astype(np.float32), labels, {"keys": list(mat), "source": str(path)}


def load_directory_dataset(directory: str | Path) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Load the supplied dataset's nested `class name/*.mat` structure.

    Each Mendeley file stores the waveform in `val`; its parent directory name
    is the class label (for example, `1 NSR`).
    """
    directory = Path(directory)
    paths = sorted(directory.rglob("*.mat"))
    if not paths:
        raise ValueError(f"No .mat files found under {directory}")
    waveforms, labels = [], []
    for path in paths:
        arrays = _numeric_arrays(loadmat(path))
        if "val" in arrays:
            signal = arrays["val"]
        else:
            candidates = [a for a in arrays.values() if a.size >= 100]
            if not candidates:
                continue
            signal = max(candidates, key=lambda a: a.size)
        signal = np.asarray(signal).squeeze()
        if signal.ndim != 1:
            continue
        waveforms.append(signal)
        labels.append(path.parent.name)
    if not waveforms:
        raise ValueError("No one-dimensional ECG arrays found in MATLAB files.")
    lengths = {len(x) for x in waveforms}
    if len(lengths) != 1:
        raise ValueError(f"Signals have inconsistent sample counts: {sorted(lengths)}")
    return np.stack(waveforms).astype(np.float32), np.asarray(labels), {"source": str(directory), "n_files": len(waveforms)}


def preprocess(signals: np.ndarray, fs: float = 360.0) -> np.ndarray:
    """Zero-phase ECG band-pass filtering and per-record robust normalization."""
    if signals.ndim != 2:
        raise ValueError("signals must have shape (records, samples)")
    sos = butter(4, [0.5, min(45.0, fs / 2 - 1)], btype="bandpass", fs=fs, output="sos")
    clean = sosfiltfilt(sos, signals, axis=1)
    clean -= np.median(clean, axis=1, keepdims=True)
    scale = np.median(np.abs(clean), axis=1, keepdims=True) * 1.4826
    return (clean / np.maximum(scale, 1e-6)).astype(np.float32)


def model_input(signals: np.ndarray) -> np.ndarray:
    """Reduce 360 Hz recordings to 90 Hz for efficient 1-D CNN training."""
    return signals[:, ::MODEL_DOWNSAMPLE].copy()


def save_label_map(labels: np.ndarray, path: Path) -> dict[str, int]:
    unique = np.unique(labels)
    mapping = {str(x): int(i) for i, x in enumerate(unique)}
    path.write_text(json.dumps(mapping, indent=2))
    return mapping
