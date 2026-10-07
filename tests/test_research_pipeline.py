"""Tests for the optional, non-classifying research feature pipeline."""

from __future__ import annotations

import importlib.util
import unittest

import numpy as np

from ecg_cvd.research_pipeline import (
    ClassificationUnavailableError,
    ECGFrameworkPipeline,
    TreatmentRecommendationUnavailableError,
)


def synthetic_ecg(*, sampling_rate: int = 360, duration_seconds: int = 10) -> np.ndarray:
    """Create a deterministic, ECG-like test trace with sharp candidate peaks."""
    samples = sampling_rate * duration_seconds
    points = np.arange(samples, dtype=np.float64)
    seconds = points / sampling_rate
    trace = 0.15 * np.sin(2 * np.pi * 0.2 * seconds) + 0.02 * np.sin(2 * np.pi * 30 * seconds)
    for location in range(sampling_rate, samples - sampling_rate // 2, sampling_rate):
        trace += 2.5 * np.exp(-0.5 * ((points - location) / 3.0) ** 2)
    return trace


class ECGFrameworkPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipeline = ECGFrameworkPipeline(sampling_rate=360)
        self.trace = synthetic_ecg()

    def test_preprocess_is_finite_and_stably_normalized(self) -> None:
        clean = self.pipeline.preprocess(self.trace)
        self.assertEqual(clean.shape, self.trace.shape)
        self.assertTrue(np.isfinite(clean).all())
        self.assertAlmostEqual(float(clean.mean()), 0.0, places=8)
        self.assertAlmostEqual(float(clean.std()), 1.0, places=8)

        flat = self.pipeline.amplitude_normalization(np.ones(100))
        np.testing.assert_array_equal(flat, np.zeros(100))

    def test_invalid_waveforms_and_filter_configuration_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.pipeline.preprocess([1.0, 2.0])
        with self.assertRaises(ValueError):
            self.pipeline.preprocess([1.0, np.nan] * 20)
        with self.assertRaises(ValueError):
            ECGFrameworkPipeline(sampling_rate=80, noise_cutoff_hz=45)
        with self.assertRaises(ValueError):
            self.pipeline.segment_beats(self.trace, [True, False])
        with self.assertRaises(ValueError):
            self.pipeline.segment_beats(self.trace, ["not-a-sample-index"])

    def test_candidate_segmentation_scalogram_and_features_are_reproducible(self) -> None:
        clean = self.pipeline.preprocess(self.trace)
        peaks = self.pipeline.beat_segmentation_cwt(clean)
        self.assertGreaterEqual(peaks.size, 7)
        self.assertTrue(np.issubdtype(peaks.dtype, np.integer))
        self.assertTrue(np.all(np.diff(peaks) >= round(0.35 * self.pipeline.fs)))

        segmentation = self.pipeline.segment_beats(clean, peaks)
        self.assertGreaterEqual(segmentation.beats.shape[0], 7)
        self.assertEqual(segmentation.beats.shape[1], segmentation.pre_samples + segmentation.post_samples)

        features = self.pipeline.extract_features(segmentation.beats[0])
        self.assertEqual(features.shape, (len(self.pipeline.FEATURE_NAMES),))
        self.assertTrue(np.isfinite(features).all())

        scalogram = self.pipeline.scalogram(
            segmentation.beats[0], widths=[1, 2, 4, 8], wavelet="morlet"
        )
        self.assertEqual(scalogram.values.shape, (4, segmentation.beats.shape[1]))
        self.assertEqual(scalogram.wavelet, "morlet")
        self.assertIn("scipy.signal.convolve", scalogram.backend)
        self.assertTrue(np.isfinite(scalogram.values).all())
        self.assertTrue(np.all(scalogram.values >= 0))

    def test_feature_selection_and_clustering_are_offline_helpers_only(self) -> None:
        rng = np.random.default_rng(9)
        features = rng.normal(size=(24, len(self.pipeline.FEATURE_NAMES)))
        labels = np.repeat([0, 1], 12)
        selected, selector = self.pipeline.feature_selection_mi(features, labels, k=3)
        self.assertEqual(selected.shape, (24, 3))
        self.assertEqual(int(selector.get_support().sum()), 3)

        clusters = self.pipeline.clustering_jpmc(features, n_clusters=2)
        self.assertEqual(clusters.shape, (24,))
        self.assertTrue(set(clusters).issubset({0, 1}))

    def test_no_untrained_classification_or_treatment_output_is_available(self) -> None:
        status = self.pipeline.classify(self.trace)
        self.assertEqual(status.status, "unavailable")
        self.assertIn("no trained", status.reason.lower())
        self.assertIn("No diagnosis", status.safety)

        with self.assertRaises(ClassificationUnavailableError):
            self.pipeline.build_ramnv2_classifier()
        with self.assertRaises(TreatmentRecommendationUnavailableError):
            self.pipeline.dtv_rrl_recommendation(np.ones(3))

    def test_optional_pywavelets_backend_imports_only_when_requested(self) -> None:
        beat = self.trace[:200]
        if importlib.util.find_spec("pywt") is None:
            with self.assertRaisesRegex(ModuleNotFoundError, "PyWavelets"):
                self.pipeline.scalogram(beat, widths=[1, 2], backend="pywavelets")
        else:
            result = self.pipeline.scalogram(beat, widths=[1, 2], backend="pywavelets")
            self.assertEqual(result.values.shape, (2, 200))
            self.assertIn("PyWavelets", result.backend)

    def test_convenience_workflow_returns_artifacts_not_a_prediction(self) -> None:
        result = self.pipeline.extract_research_artifacts(
            self.trace,
            peak_widths=[4, 8, 12],
            scalogram_widths=[1, 2, 4],
        )
        self.assertEqual(result.preprocessed_signal.shape, self.trace.shape)
        self.assertEqual(result.feature_matrix.shape[1], len(self.pipeline.FEATURE_NAMES))
        self.assertEqual(result.classification.status, "unavailable")
        self.assertTrue(result.limitations)
        self.assertIsNotNone(result.first_beat_scalogram)


if __name__ == "__main__":
    unittest.main()
