from __future__ import annotations

import unittest

import torch

from ecg_cvd.cwt_mobilenetv2 import CWTMobileNetV2, CWTMobileNetV2Config


class CWTMobileNetV2Tests(unittest.TestCase):
    def _model(self, **overrides: object) -> CWTMobileNetV2:
        options: dict[str, object] = {
            "num_classes": 17,
            "image_height": 32,
            "image_width": 48,
            "width_multiplier": 0.1,
            "dropout": 0.0,
        }
        options.update(overrides)
        return CWTMobileNetV2(**options).eval()

    def test_rgb_cwt_batch_returns_configured_17_class_logits(self) -> None:
        torch.manual_seed(7)
        model = self._model()
        image_batch = torch.randn(2, 3, 32, 48)
        with torch.no_grad():
            logits = model(image_batch)
            features = model.forward_features(image_batch)
        self.assertEqual(tuple(logits.shape), (2, 17))
        self.assertEqual(tuple(features.shape), (2, model.feature_channels))
        self.assertTrue(torch.isfinite(logits).all())

    def test_geometry_is_part_of_the_default_input_contract(self) -> None:
        model = self._model()
        with self.assertRaisesRegex(ValueError, "geometry does not match"):
            model(torch.zeros(1, 3, 32, 32))
        with self.assertRaisesRegex(ValueError, "3-channel RGB"):
            model(torch.zeros(1, 1, 32, 48))
        with self.assertRaisesRegex(TypeError, "floating-point"):
            model(torch.zeros(1, 3, 32, 48, dtype=torch.int64))
        with self.assertRaisesRegex(ValueError, "finite"):
            model(torch.full((1, 3, 32, 48), float("nan")))

    def test_width_multiplier_changes_capacity_without_changing_logits_shape(self) -> None:
        narrow = self._model(width_multiplier=0.1)
        wide = self._model(width_multiplier=0.5)
        narrow_parameters = sum(parameter.numel() for parameter in narrow.parameters())
        wide_parameters = sum(parameter.numel() for parameter in wide.parameters())
        self.assertLess(narrow_parameters, wide_parameters)
        self.assertEqual(narrow.classifier[-1].out_features, 17)
        self.assertEqual(wide.classifier[-1].out_features, 17)

    def test_config_is_validated_and_round_trips_through_model_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "image_height"):
            CWTMobileNetV2Config(image_height=16)
        with self.assertRaisesRegex(ValueError, "width_multiplier"):
            CWTMobileNetV2Config(width_multiplier=0.0)
        with self.assertRaisesRegex(ValueError, "dropout"):
            CWTMobileNetV2Config(dropout=1.0)

        model = self._model()
        metadata = model.metadata()
        self.assertEqual(metadata["architecture"], "CWTMobileNetV2")
        self.assertEqual(metadata["input_tensor"]["shape"], [1, 3, 32, 48])
        self.assertEqual(metadata["output"]["shape"], [1, 17])
        self.assertTrue(metadata["research_only"])

        reconstructed = CWTMobileNetV2.from_config(metadata["config"])
        self.assertEqual(reconstructed.config, model.config)

    def test_non_strict_geometry_is_explicit_for_controlled_experiments(self) -> None:
        model = self._model(strict_input_geometry=False)
        with torch.no_grad():
            logits = model(torch.zeros(1, 3, 40, 56))
        self.assertEqual(tuple(logits.shape), (1, 17))


if __name__ == "__main__":
    unittest.main()
