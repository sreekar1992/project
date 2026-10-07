"""Optional, offline ECG signal-processing utilities for research.

This module is a careful, dependency-light interpretation of the signal
processing portions of a supplied exploratory ``ECGFrameworkPipeline``
snippet.  It is deliberately *not* connected to the clinical API or to the
deployed PyTorch :class:`ecg_cvd.model.RAMNV2` checkpoint.

The utilities can make preprocessing, candidate beat locations, CWT-like
scalograms, and simple feature vectors reproducible for a research notebook.
They do not validate an ECG, establish R peaks, group patients, diagnose a
rhythm, or recommend treatment.  In particular, the supplied snippet's
untrained Keras/MobileNet classifier and random treatment-policy placeholder
are intentionally unavailable here.

``pywt``, Keras, and Keras Hub are not project dependencies.  The default CWT
helpers therefore use small, explicit Morlet/Mexican-hat convolution kernels
built on SciPy.  An explicitly requested optional ``backend="pywavelets"``
performs a lazy import and gives an actionable dependency error when absent.
This keeps importing the module safe in the supported runtime and makes the
chosen transform visible in the returned provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any, Iterable, Literal, Sequence

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import signal
from sklearn.cluster import KMeans
from sklearn.feature_selection import SelectKBest, mutual_info_classif


FloatArray = NDArray[np.float64]
WaveletName = Literal["morlet", "mexican_hat"]
CwtBackend = Literal["native", "pywavelets"]


class ClassificationUnavailableError(RuntimeError):
    """Raised when a caller tries to classify with this feature-only module."""


class TreatmentRecommendationUnavailableError(RuntimeError):
    """Raised when a caller tries to obtain an intentionally disabled treatment output."""


@dataclass(frozen=True)
class BeatSegmentation:
    """Candidate beat windows extracted around waveform peaks.

    ``r_peaks`` are algorithmic candidates only.  The detector has not been
    clinically validated and should not be used as a device-level R-peak
    detector.  ``beats`` has shape ``(n_beats, pre_samples + post_samples)``;
    it is empty, with that second dimension preserved, when no complete window
    can be extracted.
    """

    r_peaks: NDArray[np.int64]
    beats: FloatArray
    pre_samples: int
    post_samples: int


@dataclass(frozen=True)
class Scalogram:
    """Magnitude CWT-like representation and the metadata needed to reproduce it."""

    values: FloatArray
    scales: FloatArray
    wavelet: WaveletName
    sampling_rate_hz: float
    backend: str = "scipy.signal.convolve (explicit analytic kernels)"


@dataclass(frozen=True)
class ClassificationAvailability:
    """Explicitly documents why no prediction is produced by this module."""

    status: Literal["unavailable"]
    reason: str
    required_artifact: str
    safety: str


@dataclass(frozen=True)
class ResearchArtifacts:
    """Output of :meth:`ECGFrameworkPipeline.extract_research_artifacts`.

    This object is intended for offline inspection or a notebook.  It contains
    no disease label, confidence score, treatment, or patient grouping.
    """

    preprocessed_signal: FloatArray
    segmentation: BeatSegmentation
    feature_matrix: FloatArray
    feature_names: tuple[str, ...]
    first_beat_scalogram: Scalogram | None
    classification: ClassificationAvailability
    limitations: tuple[str, ...]


class ECGFrameworkPipeline:
    """Research-only preprocessing and feature-extraction pipeline.

    Parameters are intentionally explicit so a notebook records the sampling
    rate and filtering assumptions used.  The default (360 Hz) matches this
    repository's supplied 10-second numerical ECG records, but this pipeline
    is not the production RAMNV2 preprocessing contract.  Pass the actual
    source sampling rate when it differs.
    """

    FEATURE_NAMES = (
        "maximum",
        "minimum",
        "peak_to_peak",
        "variance",
        "standard_deviation",
        "mean_log_absolute_amplitude",
        "successive_difference_mean_square",
    )
    _CLASSIFICATION_REASON = (
        "Classification is unavailable: this research utility has no trained, "
        "versioned classifier artifact or verified label map."
    )
    _TREATMENT_REASON = (
        "Treatment recommendation is intentionally disabled. The supplied ECG "
        "dataset contains no treatment or outcome targets, and this module has "
        "no treatment-policy artifact."
    )

    def __init__(
        self,
        sampling_rate: float = 360.0,
        *,
        baseline_cutoff_hz: float = 0.5,
        noise_cutoff_hz: float = 45.0,
        median_kernel_size: int = 3,
        random_state: int = 42,
    ) -> None:
        self.fs = self._positive_finite("sampling_rate", sampling_rate)
        self.baseline_cutoff_hz = self._positive_finite("baseline_cutoff_hz", baseline_cutoff_hz)
        self.noise_cutoff_hz = self._positive_finite("noise_cutoff_hz", noise_cutoff_hz)
        nyquist = self.fs / 2.0
        if not self.baseline_cutoff_hz < self.noise_cutoff_hz < nyquist:
            raise ValueError(
                "Expected 0 < baseline_cutoff_hz < noise_cutoff_hz < sampling_rate / 2. "
                f"Received {self.baseline_cutoff_hz}, {self.noise_cutoff_hz}, and {self.fs}."
            )
        if isinstance(median_kernel_size, bool) or not isinstance(median_kernel_size, int):
            raise TypeError("median_kernel_size must be an odd positive integer.")
        if median_kernel_size < 1 or median_kernel_size % 2 == 0:
            raise ValueError("median_kernel_size must be an odd positive integer.")
        if isinstance(random_state, bool) or not isinstance(random_state, int):
            raise TypeError("random_state must be an integer.")
        self.median_kernel_size = median_kernel_size
        self.random_state = random_state

    # ------------------------------------------------------------------
    # Input validation and preprocessing
    # ------------------------------------------------------------------
    @staticmethod
    def _positive_finite(name: str, value: float) -> float:
        if isinstance(value, bool):
            raise TypeError(f"{name} must be a finite positive number.")
        numeric = float(value)
        if not np.isfinite(numeric) or numeric <= 0:
            raise ValueError(f"{name} must be a finite positive number.")
        return numeric

    @staticmethod
    def _waveform(ecg_signal: ArrayLike, *, minimum_samples: int = 1) -> FloatArray:
        """Convert one finite, one-dimensional signal to a stable float array."""
        values = np.asarray(ecg_signal, dtype=np.float64)
        if values.ndim == 2 and 1 in values.shape:
            values = values.reshape(-1)
        if values.ndim != 1:
            raise ValueError("ECG signal must be one-dimensional (or a single row/column).")
        if values.size < minimum_samples:
            raise ValueError(f"ECG signal must contain at least {minimum_samples} samples.")
        if not np.isfinite(values).all():
            raise ValueError("ECG signal must contain only finite numeric samples.")
        return np.ascontiguousarray(values, dtype=np.float64)

    def _sos_filter(self, values: FloatArray, cutoff_hz: float, btype: Literal["highpass", "lowpass"], order: int) -> FloatArray:
        sos = signal.butter(order, cutoff_hz, btype=btype, fs=self.fs, output="sos")
        # ``sosfiltfilt`` gives zero-phase output but requires a modest amount
        # of data.  Rejecting short traces is safer than silently returning a
        # causal, phase-shifted substitute.
        try:
            filtered = signal.sosfiltfilt(sos, values)
        except ValueError as exc:
            raise ValueError(
                "ECG signal is too short for zero-phase filtering; provide at least 32 samples."
            ) from exc
        return np.asarray(filtered, dtype=np.float64)

    def baseline_wander_removal(self, ecg_signal: ArrayLike) -> FloatArray:
        """Apply a first-order, zero-phase high-pass filter.

        This is an exploratory baseline-drift reduction step, not a validated
        clinical signal-conditioning protocol.
        """
        values = self._waveform(ecg_signal, minimum_samples=32)
        return self._sos_filter(values, self.baseline_cutoff_hz, "highpass", order=1)

    def butterworth_filter(self, ecg_signal: ArrayLike) -> FloatArray:
        """Apply a fourth-order, zero-phase low-pass noise filter."""
        values = self._waveform(ecg_signal, minimum_samples=32)
        return self._sos_filter(values, self.noise_cutoff_hz, "lowpass", order=4)

    def amplitude_normalization(self, ecg_signal: ArrayLike) -> FloatArray:
        """Return z-scored amplitudes, or stable zeros for a flat trace."""
        values = self._waveform(ecg_signal)
        centered = values - float(np.mean(values))
        scale = float(np.std(centered))
        if scale <= np.finfo(np.float64).eps:
            return np.zeros_like(centered)
        return centered / scale

    def artifact_correction_issmca(self, ecg_signal: ArrayLike) -> FloatArray:
        """Apply a small median despiker.

        The historic method name is retained only to make the snippet's API
        recognizable.  This is **not** an implementation of ISSMCA and is not
        described as one in research outputs.
        """
        values = self._waveform(ecg_signal, minimum_samples=self.median_kernel_size)
        return np.asarray(signal.medfilt(values, kernel_size=self.median_kernel_size), dtype=np.float64)

    def preprocess(self, ecg_signal: ArrayLike) -> FloatArray:
        """Run high-pass, low-pass, median-despike, and z-score steps.

        It intentionally remains separate from :func:`ecg_cvd.data.preprocess`,
        which is the persisted contract for the deployed PyTorch model.
        """
        values = self._waveform(ecg_signal, minimum_samples=32)
        cleaned = self.baseline_wander_removal(values)
        cleaned = self.butterworth_filter(cleaned)
        cleaned = self.artifact_correction_issmca(cleaned)
        return self.amplitude_normalization(cleaned)

    def preprocessing_provenance(self) -> dict[str, Any]:
        """Return serializable parameters without claiming model compatibility."""
        return {
            "research_only": True,
            "sampling_rate_hz": self.fs,
            "baseline_filter": f"first-order zero-phase high-pass at {self.baseline_cutoff_hz:g} Hz",
            "noise_filter": f"fourth-order zero-phase low-pass at {self.noise_cutoff_hz:g} Hz",
            "artifact_step": f"median filter (kernel={self.median_kernel_size}); not ISSMCA",
            "normalization": "z-score with zero output for a flat trace",
            "not_ramnv2_preprocessing": True,
        }

    # ------------------------------------------------------------------
    # Candidate beat segmentation and multi-scale transforms
    # ------------------------------------------------------------------
    @staticmethod
    def _mexican_hat_kernel(scale: float) -> FloatArray:
        half_width = max(3, int(np.ceil(5.0 * scale)))
        positions = np.arange(-half_width, half_width + 1, dtype=np.float64) / scale
        kernel = (1.0 - positions**2) * np.exp(-0.5 * positions**2)
        kernel -= np.mean(kernel)
        norm = float(np.linalg.norm(kernel))
        return kernel / max(norm, np.finfo(np.float64).eps)

    @staticmethod
    def _morlet_kernel(scale: float, *, omega0: float = 6.0) -> NDArray[np.complex128]:
        half_width = max(3, int(np.ceil(5.0 * scale)))
        positions = np.arange(-half_width, half_width + 1, dtype=np.float64) / scale
        kernel = np.pi ** (-0.25) * np.exp(1j * omega0 * positions) * np.exp(-0.5 * positions**2)
        kernel -= np.mean(kernel)
        norm = float(np.linalg.norm(kernel))
        return np.asarray(kernel / max(norm, np.finfo(np.float64).eps), dtype=np.complex128)

    def _default_peak_scales(self) -> FloatArray:
        lower = max(1, int(round(self.fs * 0.010)))
        upper = max(lower + 1, int(round(self.fs * 0.080)))
        return np.arange(lower, upper + 1, dtype=np.float64)

    @staticmethod
    def _validate_scales(widths: Iterable[float]) -> FloatArray:
        values = np.asarray(list(widths), dtype=np.float64).reshape(-1)
        if values.size == 0:
            raise ValueError("At least one positive CWT scale is required.")
        if not np.isfinite(values).all() or np.any(values <= 0):
            raise ValueError("CWT scales must be finite positive numbers.")
        return values

    def beat_segmentation_cwt(
        self,
        ecg_signal: ArrayLike,
        *,
        widths: Sequence[float] | None = None,
        minimum_rr_seconds: float = 0.35,
        prominence: float | None = None,
    ) -> NDArray[np.int64]:
        """Return candidate waveform peaks from a Mexican-hat multiscale response.

        This lightweight detector replaces the unavailable ``pywt`` dependency.
        It is intentionally labelled *candidate* segmentation: peak locations
        are sensitive to lead polarity, noise, rhythm, filtering, and the
        selected sampling rate.
        """
        values = self._waveform(ecg_signal, minimum_samples=32)
        minimum_rr_seconds = self._positive_finite("minimum_rr_seconds", minimum_rr_seconds)
        if prominence is not None:
            prominence = self._positive_finite("prominence", prominence)
        if np.ptp(values) <= np.finfo(np.float64).eps:
            return np.empty(0, dtype=np.int64)

        scales = self._validate_scales(widths if widths is not None else self._default_peak_scales())
        centered = values - np.median(values)
        responses = [
            np.abs(signal.convolve(centered, self._mexican_hat_kernel(float(scale)), mode="same", method="auto"))
            for scale in scales
        ]
        response = np.max(np.vstack(responses), axis=0)
        response_median = float(np.median(response))
        response_mad = float(np.median(np.abs(response - response_median))) * 1.4826
        adaptive_prominence = max(response_mad * 1.5, np.finfo(np.float64).eps)
        distance = max(1, int(round(self.fs * minimum_rr_seconds)))
        peaks, _ = signal.find_peaks(
            response,
            distance=distance,
            prominence=adaptive_prominence if prominence is None else prominence,
        )
        return np.asarray(peaks, dtype=np.int64)

    def segment_beats(
        self,
        ecg_signal: ArrayLike,
        r_peaks: Sequence[int] | NDArray[np.integer[Any]] | None = None,
        *,
        pre_seconds: float = 0.30,
        post_seconds: float = 0.45,
    ) -> BeatSegmentation:
        """Extract complete, fixed-width windows around candidate peaks."""
        values = self._waveform(ecg_signal, minimum_samples=32)
        pre_seconds = self._positive_finite("pre_seconds", pre_seconds)
        post_seconds = self._positive_finite("post_seconds", post_seconds)
        pre_samples = max(1, int(round(pre_seconds * self.fs)))
        post_samples = max(1, int(round(post_seconds * self.fs)))
        window = pre_samples + post_samples

        if r_peaks is None:
            candidates = self.beat_segmentation_cwt(values)
        else:
            raw_peaks = np.asarray(r_peaks)
            if raw_peaks.ndim != 1:
                raise ValueError("r_peaks must be a one-dimensional sequence of sample indexes.")
            if np.issubdtype(raw_peaks.dtype, np.bool_):
                raise ValueError("r_peaks must contain integer sample indexes, not booleans.")
            if not np.issubdtype(raw_peaks.dtype, np.integer):
                try:
                    numeric_peaks = np.asarray(raw_peaks, dtype=np.float64)
                except (TypeError, ValueError) as exc:
                    raise ValueError("r_peaks must contain finite integer sample indexes.") from exc
                if not np.all(np.isfinite(numeric_peaks)) or not np.all(np.equal(numeric_peaks, np.floor(numeric_peaks))):
                    raise ValueError("r_peaks must contain finite integer sample indexes.")
                raw_peaks = numeric_peaks
            candidates = np.asarray(raw_peaks, dtype=np.int64)

        valid = candidates[(candidates >= pre_samples) & (candidates + post_samples <= values.size)]
        valid = np.unique(valid)
        if valid.size == 0:
            beats = np.empty((0, window), dtype=np.float64)
        else:
            beats = np.stack([values[peak - pre_samples:peak + post_samples] for peak in valid]).astype(np.float64)
        return BeatSegmentation(r_peaks=valid, beats=beats, pre_samples=pre_samples, post_samples=post_samples)

    @staticmethod
    def _normalise_wavelet_name(wavelet: str) -> WaveletName:
        normalised = wavelet.strip().lower().replace("-", "_")
        aliases: dict[str, WaveletName] = {
            "morlet": "morlet",
            "morl": "morlet",
            "mexican_hat": "mexican_hat",
            "mexh": "mexican_hat",
            "ricker": "mexican_hat",
        }
        try:
            return aliases[normalised]
        except KeyError as exc:
            raise ValueError("wavelet must be one of: morlet, mexican_hat.") from exc

    @staticmethod
    def _normalise_cwt_backend(backend: str) -> CwtBackend:
        normalised = backend.strip().lower().replace("-", "_")
        aliases: dict[str, CwtBackend] = {
            "native": "native",
            "scipy": "native",
            "pywavelets": "pywavelets",
            "pywt": "pywavelets",
        }
        try:
            return aliases[normalised]
        except KeyError as exc:
            raise ValueError("backend must be one of: native, pywavelets.") from exc

    @staticmethod
    def _require_pywavelets() -> Any:
        """Import the optional reference implementation only on explicit request."""
        try:
            import pywt  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "The optional PyWavelets CWT backend is not installed. Install it only in a "
                "research environment with `pip install 'PyWavelets>=1.6,<2'`, or use the "
                "default native backend instead."
            ) from exc
        return pywt

    def scalogram(
        self,
        segmented_beat: ArrayLike,
        *,
        widths: Sequence[float] | None = None,
        wavelet: str = "morlet",
        backend: str = "native",
    ) -> Scalogram:
        """Create a CWT-like magnitude scalogram from an individual beat.

        The output is a feature visualization only.  It is not automatically
        resized, RGB-converted, or compatible with an image model.  A separate
        trained artifact must define those transformations if classification is
        ever evaluated in a research setting.
        """
        beat = self._waveform(segmented_beat, minimum_samples=8)
        selected_wavelet = self._normalise_wavelet_name(wavelet)
        selected_backend = self._normalise_cwt_backend(backend)
        if widths is None:
            # Limit the largest default scale to half the beat length so a
            # nearly full-window convolution does not dominate short beats.
            upper = min(127, max(2, beat.size // 2))
            selected_scales = np.arange(1, upper + 1, dtype=np.float64)
        else:
            selected_scales = self._validate_scales(widths)

        centered = beat - np.mean(beat)
        if selected_backend == "pywavelets":
            pywt = self._require_pywavelets()
            pywt_name = "morl" if selected_wavelet == "morlet" else "mexh"
            coefficients, _ = pywt.cwt(
                centered,
                selected_scales,
                pywt_name,
                sampling_period=1.0 / self.fs,
            )
            values = np.abs(np.asarray(coefficients)).astype(np.float64)
            backend_name = "PyWavelets cwt (optional dependency)"
        elif selected_wavelet == "morlet":
            coefficients = [
                signal.convolve(centered, self._morlet_kernel(float(scale)), mode="same", method="auto")
                for scale in selected_scales
            ]
            values = np.abs(np.vstack(coefficients)).astype(np.float64)
            backend_name = "scipy.signal.convolve (explicit analytic kernels)"
        else:
            coefficients = [
                signal.convolve(centered, self._mexican_hat_kernel(float(scale)), mode="same", method="auto")
                for scale in selected_scales
            ]
            values = np.abs(np.vstack(coefficients)).astype(np.float64)
            backend_name = "scipy.signal.convolve (explicit analytic kernels)"
        return Scalogram(
            values=values,
            scales=selected_scales,
            wavelet=selected_wavelet,
            sampling_rate_hz=self.fs,
            backend=backend_name,
        )

    def multi_scale_decomposition(
        self,
        segmented_beat: ArrayLike,
        widths: Sequence[float] | None = None,
        *,
        backend: str = "native",
    ) -> FloatArray:
        """Compatibility wrapper returning only a Morlet scalogram magnitude."""
        return self.scalogram(segmented_beat, widths=widths, wavelet="morlet", backend=backend).values

    # ------------------------------------------------------------------
    # Research features, selection, and exploratory clustering
    # ------------------------------------------------------------------
    def morphological_feature_extraction(self, beat: ArrayLike) -> FloatArray:
        """Extract simple waveform-shape summary features from one beat."""
        values = self._waveform(beat)
        return np.asarray(
            [np.max(values), np.min(values), np.ptp(values), np.var(values)], dtype=np.float64
        )

    def extract_hybrid_features(self, beat: ArrayLike) -> FloatArray:
        """Extract basic dispersion, log-amplitude, and difference-energy features."""
        values = self._waveform(beat, minimum_samples=2)
        return np.asarray(
            [
                np.std(values),
                np.mean(np.log(np.abs(values) + 1e-5)),
                np.mean(np.diff(values) ** 2),
            ],
            dtype=np.float64,
        )

    def extract_features(self, beat: ArrayLike) -> FloatArray:
        """Return the seven named research features in :attr:`FEATURE_NAMES`."""
        return np.concatenate((self.morphological_feature_extraction(beat), self.extract_hybrid_features(beat)))

    @staticmethod
    def _feature_matrix(features: ArrayLike, *, minimum_rows: int = 1) -> FloatArray:
        values = np.asarray(features, dtype=np.float64)
        if values.ndim != 2 or values.shape[0] < minimum_rows or values.shape[1] < 1:
            raise ValueError("features must have shape (n_samples, n_features).")
        if not np.isfinite(values).all():
            raise ValueError("features must contain only finite numeric values.")
        return values

    def feature_selection_mi(
        self,
        X: ArrayLike,
        y: ArrayLike,
        k: int = 5,
    ) -> tuple[FloatArray, SelectKBest]:
        """Fit supervised mutual-information feature selection for offline research.

        The caller supplies labels and owns the split/evaluation protocol.  This
        helper does not fit a classifier or make a clinical prediction.
        """
        features = self._feature_matrix(X, minimum_rows=2)
        labels = np.asarray(y).reshape(-1)
        if labels.size != features.shape[0]:
            raise ValueError("y must contain exactly one label for each feature row.")
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= features.shape[1]:
            raise ValueError("k must be an integer between 1 and the number of feature columns.")
        score = partial(mutual_info_classif, random_state=self.random_state)
        selector = SelectKBest(score_func=score, k=k)
        return np.asarray(selector.fit_transform(features, labels), dtype=np.float64), selector

    def clustering_jpmc(self, features: ArrayLike, n_clusters: int = 3) -> NDArray[np.int64]:
        """Cluster feature rows for an exploratory, non-patient-grouping analysis.

        The retained name mirrors the supplied snippet only.  It is ordinary
        K-means, not a JPMC implementation, does not identify patient groups,
        and must not be interpreted as a clinical subgrouping result.
        """
        matrix = self._feature_matrix(features, minimum_rows=2)
        if isinstance(n_clusters, bool) or not isinstance(n_clusters, int):
            raise TypeError("n_clusters must be an integer.")
        if not 2 <= n_clusters <= matrix.shape[0]:
            raise ValueError("n_clusters must be at least 2 and no greater than the number of feature rows.")
        estimator = KMeans(n_clusters=n_clusters, random_state=self.random_state, n_init=10)
        return np.asarray(estimator.fit_predict(matrix), dtype=np.int64)

    # ------------------------------------------------------------------
    # Explicitly unavailable classifier and treatment interfaces
    # ------------------------------------------------------------------
    def classification_status(self) -> ClassificationAvailability:
        """Describe the intentionally absent classifier instead of fabricating a score."""
        return ClassificationAvailability(
            status="unavailable",
            reason=self._CLASSIFICATION_REASON,
            required_artifact=(
                "A separately trained, versioned research artifact with a declared input transform, "
                "label map, evaluation protocol, and provenance."
            ),
            safety=(
                "No diagnosis, calibrated probability, triage, or treatment output is produced by this module."
            ),
        )

    def classify(self, _: ArrayLike | None = None) -> ClassificationAvailability:
        """Return an explicit unavailable status; no untrained inference is attempted."""
        return self.classification_status()

    def build_ramnv2_classifier(self, *_: Any, **__: Any) -> None:
        """Reject the pasted untrained Keras/MobileNet classifier path explicitly."""
        raise ClassificationUnavailableError(self._CLASSIFICATION_REASON)

    def dtv_rrl_recommendation(self, *_: Any, **__: Any) -> None:
        """Reject the pasted random treatment-policy placeholder explicitly."""
        raise TreatmentRecommendationUnavailableError(self._TREATMENT_REASON)

    # ------------------------------------------------------------------
    # Convenience workflow
    # ------------------------------------------------------------------
    def extract_research_artifacts(
        self,
        ecg_signal: ArrayLike,
        *,
        peak_widths: Sequence[float] | None = None,
        scalogram_widths: Sequence[float] | None = None,
    ) -> ResearchArtifacts:
        """Run the non-classifying portions of the research workflow.

        The first complete candidate beat is decomposed only when one is
        available.  Empty candidate results are valid research output and do
        not imply a normal or abnormal recording.
        """
        cleaned = self.preprocess(ecg_signal)
        peaks = self.beat_segmentation_cwt(cleaned, widths=peak_widths)
        segmentation = self.segment_beats(cleaned, peaks)
        if segmentation.beats.size:
            feature_matrix = np.vstack([self.extract_features(beat) for beat in segmentation.beats])
            first_scalogram: Scalogram | None = self.scalogram(
                segmentation.beats[0], widths=scalogram_widths
            )
        else:
            feature_matrix = np.empty((0, len(self.FEATURE_NAMES)), dtype=np.float64)
            first_scalogram = None
        return ResearchArtifacts(
            preprocessed_signal=cleaned,
            segmentation=segmentation,
            feature_matrix=feature_matrix,
            feature_names=self.FEATURE_NAMES,
            first_beat_scalogram=first_scalogram,
            classification=self.classification_status(),
            limitations=(
                "Candidate beat locations are not clinically validated R-peak annotations.",
                "Scalograms and features are offline research artifacts, not a classifier input contract.",
                "No diagnosis, treatment recommendation, or clinical decision is produced.",
            ),
        )
