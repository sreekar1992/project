"""Read supported external MAT waveforms without making them model inputs."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import warnings

import numpy as np
from scipy.io import loadmat


@dataclass(frozen=True)
class SourceECG:
    waveform: np.ndarray
    duration_seconds: float


def read_source_ecg(payload: bytes) -> SourceECG:
    """Validate the COVID-19 dataset's sinalf1/TEMPO1 viewing fields."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            data = loadmat(BytesIO(payload), variable_names=("sinalf1", "TEMPO1"),
                           verify_compressed_data_integrity=True)
    except (ValueError, TypeError, OSError, Warning) as exc:
        raise ValueError("The MAT file could not be read as a supported source ECG.") from exc
    if "sinalf1" not in data or "TEMPO1" not in data:
        raise ValueError("Source-only MAT files require 'sinalf1' and 'TEMPO1' fields.")
    raw = np.asarray(data["sinalf1"])
    duration = np.asarray(data["TEMPO1"])
    if raw.ndim not in (1, 2) or (raw.ndim == 2 and min(raw.shape) != 1):
        raise ValueError("'sinalf1' must contain one waveform vector.")
    if not np.issubdtype(raw.dtype, np.number) or np.iscomplexobj(raw):
        raise ValueError("'sinalf1' must contain real numeric samples.")
    waveform = raw.reshape(-1)
    if not 2 <= waveform.size <= 500_000 or not np.isfinite(waveform).all():
        raise ValueError("'sinalf1' must contain 2–500,000 finite samples.")
    if duration.size != 1 or not np.issubdtype(duration.dtype, np.number) or np.iscomplexobj(duration):
        raise ValueError("'TEMPO1' must contain one numeric duration in seconds.")
    duration_seconds = float(duration.item())
    if not np.isfinite(duration_seconds) or not 0 < duration_seconds <= 86_400:
        raise ValueError("'TEMPO1' must be a positive duration no greater than 24 hours.")
    return SourceECG(waveform=waveform, duration_seconds=duration_seconds)
