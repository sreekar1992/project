"""Deterministic, dependency-light CWT image construction for offline ECG research.

The CWT image contract in this module is intentionally explicit.  It is used
only by the separately versioned CWT-MobileNetV2 experiment and is not a
replacement for the deployed one-dimensional RAMNV2 model.  In particular it
does not import PyWavelets, OpenCV, or torchvision: the transform is built
from explicit analytic Morlet kernels plus :mod:`scipy.signal` operations.

The output is a *single-channel uint8 image*.  The experimental training
dataset repeats that grayscale image into three channels immediately before
the RGB image model.  Keeping the stored representation single channel keeps
the cache compact and makes the RGB conversion unambiguous in the manifest.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import signal


FloatArray = NDArray[np.float64]
Uint8Array = NDArray[np.uint8]


@dataclass(frozen=True)
class CWTImageConfig:
    """Versioned input contract for a CWT image experiment.

    ``analysis_samples`` is an explicit deterministic resampling step before
    the transform.  With this repository's 3,600-sample, 10-second MLII
    records, the default produces a 900-sample analysis trace.  It is not a
    sampling-rate conversion suitable for clinical interpretation; the source
    sampling rate and all parameters are written to the training manifest.
    """

    source_sampling_rate_hz: float = 360.0
    analysis_samples: int = 900
    minimum_scale: float = 1.0
    maximum_scale: float = 48.0
    number_of_scales: int = 32
    image_size: int = 224
    morlet_omega0: float = 6.0
    lower_intensity_quantile: float = 0.01
    upper_intensity_quantile: float = 0.99

    def __post_init__(self) -> None:
        finite_positive = {
            "source_sampling_rate_hz": self.source_sampling_rate_hz,
            "minimum_scale": self.minimum_scale,
            "maximum_scale": self.maximum_scale,
            "morlet_omega0": self.morlet_omega0,
        }
        for name, value in finite_positive.items():
            if isinstance(value, bool) or not np.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{name} must be a finite positive number.")
        if self.maximum_scale < self.minimum_scale:
            raise ValueError("maximum_scale must be greater than or equal to minimum_scale.")
        if not isinstance(self.analysis_samples, int) or isinstance(self.analysis_samples, bool) or self.analysis_samples < 32:
            raise ValueError("analysis_samples must be an integer of at least 32.")
        if not isinstance(self.number_of_scales, int) or isinstance(self.number_of_scales, bool) or self.number_of_scales < 2:
            raise ValueError("number_of_scales must be an integer of at least 2.")
        if not isinstance(self.image_size, int) or isinstance(self.image_size, bool) or self.image_size < 32:
            raise ValueError("image_size must be an integer of at least 32.")
        if not (
            0.0 <= self.lower_intensity_quantile < self.upper_intensity_quantile <= 1.0
        ):
            raise ValueError("Intensity quantiles must satisfy 0 <= lower < upper <= 1.")

    @property
    def scales(self) -> FloatArray:
        """Return fixed logarithmically spaced scales for every record."""
        return np.geomspace(
            float(self.minimum_scale), float(self.maximum_scale), int(self.number_of_scales)
        ).astype(np.float64)

    def manifest(self) -> dict[str, Any]:
        """Return JSON-safe provenance for a training artifact."""
        return {
            "name": "native_morlet_cwt_uint8_grayscale_v1",
            "implementation": "numpy + scipy.signal.convolve + scipy.signal.resample",
            "input": "one preprocessed numerical ECG waveform per record",
            "output": {
                "shape": [int(self.image_size), int(self.image_size)],
                "dtype": "uint8",
                "channels_before_model": 1,
                "model_channels": 3,
                "rgb_conversion": "repeat grayscale CWT image identically across RGB channels",
            },
            "preprocessing_contract": asdict(self),
            "resampling": {
                "method": "scipy.signal.resample (FFT)",
                "input_samples": "variable source record length",
                "analysis_samples": int(self.analysis_samples),
            },
            "wavelet": {
                "family": "complex Morlet",
                "omega0": float(self.morlet_omega0),
                "kernel_support": "[-5*scale, +5*scale] samples",
                "scales": [float(value) for value in self.scales],
            },
            "intensity": {
                "magnitude": "log1p(abs(CWT coefficient))",
                "normalization": "per-record quantile clipping then linear mapping to [0, 255]",
                "lower_quantile": float(self.lower_intensity_quantile),
                "upper_quantile": float(self.upper_intensity_quantile),
            },
            "dependencies_not_used": ["PyWavelets", "OpenCV", "torchvision"],
        }


def _one_dimensional_finite(values: ArrayLike) -> FloatArray:
    waveform = np.asarray(values, dtype=np.float64)
    if waveform.ndim == 2 and 1 in waveform.shape:
        waveform = waveform.reshape(-1)
    if waveform.ndim != 1:
        raise ValueError("Expected one ECG waveform with shape (samples,).")
    if waveform.size < 32:
        raise ValueError("An ECG waveform must contain at least 32 samples.")
    if not np.isfinite(waveform).all():
        raise ValueError("An ECG waveform must contain only finite samples.")
    return np.ascontiguousarray(waveform, dtype=np.float64)


def _morlet_kernel(scale: float, omega0: float) -> NDArray[np.complex128]:
    """Create one mean-centred, L2-normalized analytic Morlet kernel."""
    half_width = max(3, int(np.ceil(5.0 * scale)))
    positions = np.arange(-half_width, half_width + 1, dtype=np.float64) / scale
    kernel = np.pi ** (-0.25) * np.exp(1j * omega0 * positions) * np.exp(-0.5 * positions**2)
    kernel -= np.mean(kernel)
    norm = float(np.linalg.norm(kernel))
    return np.asarray(kernel / max(norm, np.finfo(np.float64).eps), dtype=np.complex128)


class NativeMorletCWTImageTransform:
    """Turn preprocessed numerical ECG signals into fixed-size CWT images.

    All work is CPU-side and deterministic for identical numeric inputs and
    package versions.  This object has no labels and no learned state; fitting
    it on the train/validation split is therefore neither required nor done.
    """

    def __init__(self, config: CWTImageConfig | None = None) -> None:
        self.config = config or CWTImageConfig()

    def _resample_trace(self, waveform: FloatArray) -> FloatArray:
        if waveform.size == self.config.analysis_samples:
            return waveform.copy()
        return np.asarray(signal.resample(waveform, self.config.analysis_samples), dtype=np.float64)

    def scalogram(self, waveform: ArrayLike) -> FloatArray:
        """Return unquantized log-magnitude CWT values before image resizing."""
        trace = self._resample_trace(_one_dimensional_finite(waveform))
        coefficients = [
            signal.convolve(
                trace,
                _morlet_kernel(float(scale), self.config.morlet_omega0),
                mode="same",
                method="auto",
            )
            for scale in self.config.scales
        ]
        return np.log1p(np.abs(np.vstack(coefficients))).astype(np.float64)

    def transform(self, waveform: ArrayLike) -> Uint8Array:
        """Create one deterministic ``(image_size, image_size)`` uint8 image."""
        values = self.scalogram(waveform)
        # Resize in two explicitly recorded FFT-resampling stages.  The input
        # is real, but ``resample`` may use complex intermediate arithmetic.
        values = np.real(signal.resample(values, self.config.image_size, axis=0))
        values = np.real(signal.resample(values, self.config.image_size, axis=1))
        lower, upper = np.quantile(
            values,
            [self.config.lower_intensity_quantile, self.config.upper_intensity_quantile],
        )
        if not np.isfinite(lower) or not np.isfinite(upper) or upper - lower <= np.finfo(np.float64).eps:
            return np.zeros((self.config.image_size, self.config.image_size), dtype=np.uint8)
        normalized = np.clip((values - lower) / (upper - lower), 0.0, 1.0)
        return np.rint(normalized * 255.0).astype(np.uint8)

    def transform_batch(self, waveforms: ArrayLike) -> Uint8Array:
        """Transform a two-dimensional ``(records, samples)`` numerical batch."""
        records = np.asarray(waveforms, dtype=np.float64)
        if records.ndim != 2:
            raise ValueError("Expected waveform batch with shape (records, samples).")
        if records.shape[0] < 1:
            raise ValueError("Expected at least one waveform record.")
        return np.stack([self.transform(record) for record in records]).astype(np.uint8)
