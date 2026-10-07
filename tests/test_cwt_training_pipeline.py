from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy.io import savemat

from ecg_cvd.cwt_image import CWTImageConfig, NativeMorletCWTImageTransform
from ecg_cvd.train_cwt_mobilenetv2 import (
    CWTImageDataset,
    class_weighting_from_training_fragments,
    encode_labels,
    main,
    stratified_fragment_split,
)


def _synthetic_trace(class_offset: float, record_offset: float, samples: int = 360) -> np.ndarray:
    time = np.arange(samples, dtype=np.float64) / 360.0
    return (
        0.10 * np.sin(2 * np.pi * (1.0 + class_offset) * time)
        + 0.03 * np.sin(2 * np.pi * 25.0 * time)
        + class_offset * 0.1
        + record_offset * 0.01
    ).astype(np.float32)


class CWTImageTransformTests(unittest.TestCase):
    def test_native_cwt_image_is_fixed_shape_uint8_and_deterministic(self) -> None:
        config = CWTImageConfig(
            analysis_samples=96,
            number_of_scales=6,
            maximum_scale=12,
            image_size=32,
        )
        transform = NativeMorletCWTImageTransform(config)
        trace = _synthetic_trace(1.0, 0.0)
        first = transform.transform(trace)
        second = transform.transform(trace)
        self.assertEqual(first.shape, (32, 32))
        self.assertEqual(first.dtype, np.uint8)
        self.assertTrue(np.array_equal(first, second))
        self.assertGreater(int(first.max()), int(first.min()))
        provenance = config.manifest()
        self.assertEqual(provenance["output"]["model_channels"], 3)
        self.assertIn("torchvision", provenance["dependencies_not_used"])

    def test_invalid_cwt_contract_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "analysis_samples"):
            CWTImageConfig(analysis_samples=31)
        with self.assertRaisesRegex(ValueError, "number_of_scales"):
            CWTImageConfig(number_of_scales=1)
        with self.assertRaisesRegex(ValueError, "maximum_scale"):
            CWTImageConfig(minimum_scale=4, maximum_scale=2)


class CWTTrainingHelpersTests(unittest.TestCase):
    def test_label_encoding_split_and_training_only_class_weighting(self) -> None:
        labels = np.asarray(["A"] * 6 + ["B"] * 4 + ["C"] * 2)
        encoded, label_map = encode_labels(labels, expected_classes=3)
        train_indexes, validation_indexes = stratified_fragment_split(
            encoded, validation_fraction=0.5, seed=19
        )
        repeated_train, repeated_validation = stratified_fragment_split(
            encoded, validation_fraction=0.5, seed=19
        )
        self.assertEqual(label_map, {"A": 0, "B": 1, "C": 2})
        np.testing.assert_array_equal(train_indexes, repeated_train)
        np.testing.assert_array_equal(validation_indexes, repeated_validation)
        self.assertFalse(np.intersect1d(train_indexes, validation_indexes).size)

        weights, metadata = class_weighting_from_training_fragments(
            encoded,
            train_indexes,
            num_classes=3,
            policy="inverse-frequency",
            maximum_weight=2.0,
        )
        self.assertIsNotNone(weights)
        assert weights is not None
        self.assertEqual(weights.shape, (3,))
        self.assertTrue(np.all(weights <= 2.0))
        self.assertEqual(metadata["fitted_on"], "training fragments only")
        self.assertEqual(metadata["policy"], "inverse-frequency")

    def test_dataset_repeats_grayscale_cwt_image_into_rgb(self) -> None:
        images = np.asarray([np.full((32, 32), 64, dtype=np.uint8)])
        labels = np.asarray([2], dtype=np.int64)
        inputs, target = CWTImageDataset(images, labels, np.asarray([0]))[0]
        self.assertEqual(tuple(inputs.shape), (3, 32, 32))
        self.assertEqual(int(target), 2)
        self.assertTrue(np.array_equal(inputs[0].numpy(), inputs[1].numpy()))
        self.assertAlmostEqual(float(inputs[0, 0, 0]), 64 / 255.0)

    def test_dry_run_writes_preflight_contract_without_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dataset = root / "MLII"
            for class_index, class_name in enumerate(("A", "B")):
                directory = dataset / class_name
                directory.mkdir(parents=True)
                for record_index in range(3):
                    savemat(
                        directory / f"record-{record_index}.mat",
                        {"val": _synthetic_trace(float(class_index + 1), float(record_index))},
                    )
            artifacts = root / "artifacts" / "preflight"
            main(
                [
                    "--data",
                    str(dataset),
                    "--artifacts",
                    str(artifacts),
                    "--version",
                    "test-preflight-v1",
                    "--expected-classes",
                    "2",
                    "--validation-fraction",
                    "0.5",
                    "--analysis-samples",
                    "64",
                    "--number-of-scales",
                    "4",
                    "--maximum-scale",
                    "8",
                    "--image-size",
                    "32",
                    "--width-multiplier",
                    "0.1",
                    "--dry-run",
                ]
            )
            manifest = json.loads((artifacts / "manifest.json").read_text())
            self.assertEqual(manifest["artifact_status"], "preflight_only_no_checkpoint")
            self.assertEqual(manifest["data"]["n_classes"], 2)
            self.assertEqual(manifest["preflight"]["validated_first_record_cwt_image_shape"], [32, 32])
            self.assertEqual(manifest["training"]["class_weighting"]["fitted_on"], "training fragments only")
            self.assertTrue((artifacts / "label_map.json").exists())
            self.assertTrue((artifacts / "split_indices.npz").exists())
            self.assertFalse((artifacts / "cwt_mobilenetv2_experimental.pt").exists())

    def test_tiny_full_run_writes_versioned_checkpoint_metrics_and_manifest(self) -> None:
        """Exercise serialization without running the real 1,000-fragment training job."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dataset = root / "MLII"
            for class_index, class_name in enumerate(("A", "B")):
                directory = dataset / class_name
                directory.mkdir(parents=True)
                for record_index in range(4):
                    savemat(
                        directory / f"record-{record_index}.mat",
                        {"val": _synthetic_trace(float(class_index + 1), float(record_index))},
                    )
            artifacts = root / "artifacts" / "tiny-train"
            main(
                [
                    "--data",
                    str(dataset),
                    "--artifacts",
                    str(artifacts),
                    "--version",
                    "test-tiny-train-v1",
                    "--expected-classes",
                    "2",
                    "--validation-fraction",
                    "0.5",
                    "--epochs",
                    "1",
                    "--batch-size",
                    "2",
                    "--analysis-samples",
                    "64",
                    "--number-of-scales",
                    "4",
                    "--maximum-scale",
                    "8",
                    "--image-size",
                    "32",
                    "--width-multiplier",
                    "0.1",
                    "--dropout",
                    "0",
                    "--device",
                    "cpu",
                ]
            )
            metrics = json.loads((artifacts / "metrics.json").read_text())
            manifest = json.loads((artifacts / "manifest.json").read_text())
            self.assertEqual(manifest["artifact_status"], "trained_experimental")
            self.assertEqual(manifest["checkpoint"]["file"], "cwt_mobilenetv2_experimental.pt")
            self.assertEqual(metrics["n_train"], 4)
            self.assertEqual(metrics["n_validation"], 4)
            self.assertEqual(len(metrics["history"]), 1)
            self.assertTrue((artifacts / "cwt_mobilenetv2_experimental.pt").exists())


if __name__ == "__main__":
    unittest.main()
