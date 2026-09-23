"""Raw-waveform camouflage is real, measured, bounded, and non-destructive."""
import unittest

import numpy as np
import torch
from torch import nn

from ecg_cvd.data import model_input, preprocess
from ecg_cvd.raw_camouflage import generate_camouflage


class SampleClassifier(nn.Module):
    def __init__(self):
        super().__init__()
        self.scale = nn.Parameter(torch.tensor(2.0))
        self.dropout = nn.Dropout(0.5)

    def forward(self, x):
        value = self.dropout(x)[:, 0, 225] * self.scale
        return torch.stack((value, -value), dim=1)


class ConstantClassifier(nn.Module):
    def forward(self, x):
        return torch.tensor([[2.0, 1.0]], dtype=x.dtype, device=x.device).repeat(x.shape[0], 1)


class RawCamouflageTests(unittest.TestCase):
    def setUp(self):
        self.model = SampleClassifier().eval()
        self.labels = ["A", "B"]
        t = np.arange(3600, dtype=np.float64) / 360.0
        self.raw = (900 + 20 * np.sin(2 * np.pi * 2.0 * t + 0.03)).astype(np.float32)

    def generate(self, **kwargs):
        return generate_camouflage(self.model, self.labels, self.raw, "A", **kwargs)

    def test_real_pipeline_flip_and_raw_budget(self):
        original = self.raw.copy()
        transformed, report = self.generate(epsilon=0.5, steps=20)
        self.assertEqual(transformed.shape, (3600,))
        self.assertEqual(transformed.dtype, np.float32)
        self.assertTrue(report["label_changed"])
        self.assertTrue(report["attack_success"])
        self.assertGreater(report["mse"], 0)
        self.assertLessEqual(report["max_absolute_raw_change"], report["raw_budget"])
        self.assertLessEqual(report["steps_used"], 20)
        measured = model_input(preprocess(transformed[None]))
        with torch.no_grad():
            predicted = self.labels[int(self.model(torch.from_numpy(measured[:, None])).argmax())]
        self.assertEqual(predicted, report["camouflaged_prediction"]["label"])
        np.testing.assert_array_equal(original, self.raw)

    def test_constant_model_cannot_be_forced_to_flip(self):
        transformed, report = generate_camouflage(ConstantClassifier(), self.labels, self.raw, "A")
        self.assertFalse(report["label_changed"])
        self.assertFalse(report["attack_success"])
        self.assertEqual(report["steps_used"], 0)
        self.assertEqual(report["mse"], 0)
        np.testing.assert_array_equal(transformed, self.raw)

    def test_zero_and_constant_signals_are_not_fabricated(self):
        for value in (0, 900):
            raw = np.full(3600, value, dtype=np.float32)
            transformed, report = generate_camouflage(self.model, self.labels, raw, None)
            np.testing.assert_array_equal(raw, transformed)
            self.assertEqual(report["raw_budget"], 0)
            self.assertEqual(report["mse"], 0)
            self.assertFalse(report["label_changed"])

    def test_unknown_reference_reports_no_correctness(self):
        for reference in (None, "not a class"):
            _, report = generate_camouflage(self.model, self.labels, self.raw, reference)
            for key in ("clean_correct", "camouflaged_correct", "attack_success"):
                self.assertIsNone(report[key])

    def test_wrong_reference_does_not_make_success(self):
        _, report = generate_camouflage(self.model, self.labels, self.raw, "B")
        self.assertFalse(report["clean_correct"])
        self.assertFalse(report["attack_success"])
        self.assertEqual(report["attack_reference_label"], "A")

    def test_preserves_parameters_gradients_and_mixed_modes(self):
        self.model.train()
        self.model.dropout.eval()
        self.model.scale.grad = torch.tensor(0.75)
        state = {key: value.clone() for key, value in self.model.state_dict().items()}
        self.generate()
        self.assertTrue(self.model.training)
        self.assertFalse(self.model.dropout.training)
        self.assertEqual(self.model.scale.grad.item(), 0.75)
        for key, value in state.items():
            self.assertTrue(torch.equal(self.model.state_dict()[key], value))
        self.model.scale.grad = None
        self.generate()
        self.assertIsNone(self.model.scale.grad)

    def test_deterministic_and_no_grad_context(self):
        first, report = self.generate()
        with torch.no_grad():
            second, other = self.generate()
        np.testing.assert_array_equal(first, second)
        self.assertEqual(report, other)

    def test_invalid_epsilon_and_steps(self):
        for epsilon in (True, np.bool_(True), 0, -0.1, 1.1, 10 ** 400, -(10 ** 400),
                        float("inf"), float("nan"), "0.5", None):
            with self.subTest(epsilon=epsilon), self.assertRaises(ValueError):
                self.generate(epsilon=epsilon)
        for steps in (True, np.bool_(True), 0, 41, 1.5, "20", None):
            with self.subTest(steps=steps), self.assertRaises(ValueError):
                self.generate(steps=steps)

    def test_invalid_raw_and_labels(self):
        for raw in (np.zeros(900), np.zeros((1, 3600)), np.full(3600, np.nan), np.full(3600, np.inf),
                    np.ones(3600, dtype=complex), np.full(3600, "x"), np.full(3600, 1e100)):
            with self.subTest(shape=raw.shape), self.assertRaises(ValueError):
                generate_camouflage(self.model, self.labels, raw, "A")
        for labels in ([], ["A", "A"], ["A", None], ("A", "B")):
            with self.subTest(labels=labels), self.assertRaises(ValueError):
                generate_camouflage(self.model, labels, self.raw, "A")

    def test_bad_output_restores_mixed_modes(self):
        self.model.train()
        self.model.dropout.eval()
        with self.assertRaises(ValueError):
            generate_camouflage(self.model, ["A"], self.raw, "A")
        self.assertTrue(self.model.training)
        self.assertFalse(self.model.dropout.training)


if __name__ == "__main__":
    unittest.main()
