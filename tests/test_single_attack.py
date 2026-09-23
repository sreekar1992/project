"""Single-record FGSM results must be measured, bounded, and non-destructive."""
import unittest

import numpy as np
import torch
from torch import nn

from ecg_cvd.single_attack import evaluate_single_attack


class MeanClassifier(nn.Module):
    def __init__(self):
        super().__init__()
        self.scale = nn.Parameter(torch.tensor(10.0))
        self.dropout = nn.Dropout(0.5)

    def forward(self, x):
        mean = self.dropout(x).mean(dim=(1, 2)) * self.scale
        return torch.stack((mean, -mean), dim=1)


class SingleAttackTests(unittest.TestCase):
    def setUp(self):
        self.model = MeanClassifier().eval()
        self.labels = ["A", "B"]
        self.signal = np.full(900, 0.025, dtype=np.float32)

    def evaluate(self, **kwargs):
        return evaluate_single_attack(self.model, self.labels, self.signal, **kwargs)

    def test_zero_epsilon_exact_and_png(self):
        result, png = self.evaluate(reference_label="A", epsilon=0)
        self.assertEqual(result["clean_prediction"], result["attacked_prediction"])
        self.assertEqual(result["mse"], 0)
        self.assertEqual(result["max_absolute_perturbation"], 0)
        self.assertFalse(result["label_changed"])
        self.assertFalse(result["attack_success"])
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(png), 1000)

    def test_measured_flip_and_epsilon_bound(self):
        before = self.signal.copy()
        result, _ = self.evaluate(reference_label="A", epsilon=0.05)
        self.assertEqual(result["method"], "FGSM")
        self.assertEqual(result["clean_prediction"]["label"], "A")
        self.assertEqual(result["attacked_prediction"]["label"], "B")
        self.assertTrue(result["label_changed"])
        self.assertTrue(result["clean_correct"])
        self.assertFalse(result["attacked_correct"])
        self.assertTrue(result["attack_success"])
        self.assertLessEqual(result["max_absolute_perturbation"], 0.05 + 1e-7)
        self.assertAlmostEqual(result["mse"], 0.05 ** 2)
        np.testing.assert_array_equal(self.signal, before)

    def test_unknown_reference_reports_no_correctness(self):
        for reference in (None, "unknown"):
            with self.subTest(reference=reference):
                result, _ = self.evaluate(reference_label=reference, epsilon=0.05)
                self.assertEqual(result["attack_reference_label"], "A")
                self.assertEqual(result["attack_reference_source"], "clean prediction")
                for key in ("clean_correct", "attacked_correct", "attack_success"):
                    self.assertIsNone(result[key])

    def test_incorrect_baseline_is_not_attack_success(self):
        result, _ = self.evaluate(reference_label="B", epsilon=0.05)
        self.assertFalse(result["clean_correct"])
        self.assertFalse(result["attack_success"])

    def test_preserves_parameters_gradients_and_mixed_mode(self):
        self.model.train()
        self.model.dropout.eval()
        self.model.scale.grad = torch.tensor(0.75)
        state = {key: value.clone() for key, value in self.model.state_dict().items()}
        result, _ = self.evaluate(reference_label="A", epsilon=0.05)
        self.assertTrue(result["attack_success"])
        self.assertTrue(self.model.training)
        self.assertFalse(self.model.dropout.training)
        self.assertEqual(self.model.scale.grad.item(), 0.75)
        for key, value in state.items():
            self.assertTrue(torch.equal(self.model.state_dict()[key], value))
        self.model.scale.grad = None
        self.evaluate(reference_label="A", epsilon=0.05)
        self.assertIsNone(self.model.scale.grad)

    def test_invalid_epsilon(self):
        for epsilon in (True, np.bool_(False), None, "0.1", -0.01, 1.01, float("nan"), float("inf")):
            with self.subTest(epsilon=epsilon), self.assertRaises(ValueError):
                self.evaluate(reference_label="A", epsilon=epsilon)

    def test_invalid_signal(self):
        for signal in (np.zeros((1, 900)), np.zeros(899), np.full(900, np.nan),
                       np.full(900, np.inf), np.ones(900, dtype=complex), np.full(900, "x")):
            with self.subTest(shape=signal.shape), self.assertRaises(ValueError):
                evaluate_single_attack(self.model, self.labels, signal, "A")

    def test_output_label_mismatch_and_exception_restore_mode(self):
        self.model.train()
        with self.assertRaises(ValueError):
            evaluate_single_attack(self.model, ["A"], self.signal, "A")
        self.assertTrue(self.model.training)
        self.assertTrue(self.model.dropout.training)

    def test_can_run_under_no_grad(self):
        with torch.no_grad():
            result, _ = self.evaluate(reference_label="A", epsilon=0.05)
        self.assertTrue(result["attack_success"])


if __name__ == "__main__":
    unittest.main()
