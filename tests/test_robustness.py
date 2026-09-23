"""Regression checks for bounded attacks and independent training gradients."""
import argparse
import unittest

import torch
from torch import nn
from torch.nn import functional as F

from ecg_cvd.robustness import fgsm
from ecg_cvd.train import validate_args


class FGSMTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(7)
        self.model = nn.Sequential(nn.Flatten(), nn.Linear(4, 3)).eval()
        self.x = torch.tensor([[[-20.0, -9.0, 9.0, 20.0]], [[30.0, 0.0, -30.0, 5.0]]])
        self.y = torch.tensor([0, 2])

    def test_bound_outside_previous_clipping_range(self):
        attacked = fgsm(self.model, self.x, self.y, 0.125)
        self.assertLessEqual(float((attacked - self.x).abs().max()), 0.125)
        self.assertFalse(torch.equal(attacked, self.x))
        self.assertFalse(attacked.requires_grad)

    def test_zero_epsilon_is_identity_and_does_not_alias(self):
        attacked = fgsm(self.model, self.x, self.y, 0.0)
        self.assertTrue(torch.equal(attacked, self.x))
        self.assertNotEqual(attacked.data_ptr(), self.x.data_ptr())

    def test_existing_parameter_gradients_are_unchanged(self):
        for parameter in self.model.parameters():
            parameter.grad = torch.randn_like(parameter)
        previous = [parameter.grad.clone() for parameter in self.model.parameters()]
        fgsm(self.model, self.x, self.y, 0.05)
        for parameter, expected in zip(self.model.parameters(), previous):
            self.assertTrue(torch.equal(parameter.grad, expected))
        self.model.zero_grad(set_to_none=True)
        fgsm(self.model, self.x, self.y, 0.05)
        self.assertTrue(all(parameter.grad is None for parameter in self.model.parameters()))

    def test_mixed_training_backward_contains_only_intended_losses(self):
        clean_loss = F.cross_entropy(self.model(self.x), self.y)
        attacked = fgsm(self.model, self.x, self.y, 0.05)
        loss = 0.5 * clean_loss + 0.5 * F.cross_entropy(self.model(attacked), self.y)
        loss.backward()
        actual = [parameter.grad.clone() for parameter in self.model.parameters()]
        self.model.zero_grad(set_to_none=True)
        reference = (0.5 * F.cross_entropy(self.model(self.x), self.y)
                     + 0.5 * F.cross_entropy(self.model(attacked), self.y))
        reference.backward()
        for parameter, observed in zip(self.model.parameters(), actual):
            torch.testing.assert_close(parameter.grad, observed)

    def test_invalid_epsilon_is_rejected(self):
        for epsilon in (-0.01, float("nan"), float("inf")):
            with self.subTest(epsilon=epsilon), self.assertRaises(ValueError):
                fgsm(self.model, self.x, self.y, epsilon)

    def test_input_attack_can_run_under_no_grad(self):
        with torch.no_grad():
            attacked = fgsm(self.model, self.x, self.y, 0.125)
        self.assertLessEqual(float((attacked - self.x).abs().max()), 0.125)


class TrainingValidationTests(unittest.TestCase):
    def test_invalid_settings_are_rejected(self):
        defaults = dict(epochs=1, batch_size=32, lr=0.001, adversarial_epsilon=0.05,
                        adversarial_weight=0.5, seed=42)
        invalid = (("epochs", 0), ("batch_size", 0), ("lr", 0), ("lr", float("nan")),
                   ("adversarial_epsilon", -1), ("adversarial_epsilon", float("inf")),
                   ("adversarial_weight", -0.1), ("adversarial_weight", 1.1),
                   ("adversarial_weight", float("nan")), ("seed", -1))
        validate_args(argparse.Namespace(**defaults))
        for key, value in invalid:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_args(argparse.Namespace(**{**defaults, key: value}))


if __name__ == "__main__":
    unittest.main()
