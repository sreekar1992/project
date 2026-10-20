"""Read external MAT waveforms and prepare explicit research derivatives."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import warnings

import numpy as np
from scipy.io import loadmat
from scipy.signal import resample


@dataclass(frozen=True)
class SourceECG:
    waveform: np.ndarray
    duration_seconds: float


@dataclass(frozen=True)
class SourceResearchSegment:
    waveform: np.ndarray
    inferred_sampling_rate_hz: float
    source_samples_used: int
    start_seconds: float


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


def derive_research_segment(source: SourceECG) -> SourceResearchSegment:
    """Resample a deterministic ten-second window without asserting lead identity.

    TEMPO1 provides duration, not a verified acquisition sampling rate. This
    derivative is only for a separately labelled research experiment; it must
    never replace the original source or be described as a validated ECG.
    """
    inferred_rate = source.waveform.size / source.duration_seconds
    if source.duration_seconds < 10 or not 50 <= inferred_rate <= 2_000:
        raise ValueError("The source must cover at least 10 seconds with an inferred 50–2,000 samples/second.")
    sample_count = round(inferred_rate * 10)
    window = np.asarray(source.waveform[:sample_count], dtype=np.float64)
    if window.size < 500 or np.ptp(window) <= 1e-8:
        raise ValueError("The selected source window has insufficient finite, non-flat samples.")
    derived = resample(window, 3_600).astype(np.float32)
    if not np.isfinite(derived).all() or np.ptp(derived) <= 1e-8:
        raise ValueError("The resampled research waveform did not pass numerical validation.")
    return SourceResearchSegment(waveform=derived, inferred_sampling_rate_hz=inferred_rate,
                                 source_samples_used=window.size, start_seconds=0.0)
