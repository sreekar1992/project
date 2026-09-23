"""Measured adversarial camouflage of raw ECG waveforms (not encryption).

The gradient is computed in model-input space and lifted back to the original
360 Hz sampling grid. Every candidate is then evaluated through the project's
real SciPy preprocessing. This is a bounded heuristic, not exact raw-space PGD,
an implementation of RNAF/PPO, or a guarantee of privacy or attack prevention.
"""
from __future__ import annotations

import math
from numbers import Integral, Real

import numpy as np
from scipy.signal import butter, sosfiltfilt
import torch
from torch.nn import functional as F

from .data import MODEL_DOWNSAMPLE, model_input, preprocess


METHOD = "iterative gradient-guided raw waveform camouflage"
NOTE = (
    "Research-only adversarial camouflage, not encryption or a new diagnosis. "
    "The raw waveform is changed within a measured amplitude budget and every "
    "prediction is recomputed through the real ECG preprocessing pipeline. "
    "A label change is not guaranteed and does not establish privacy or protection "
    "against attacks or other models. This heuristic is not exact raw-space PGD "
    "or the full RNAF/PPO framework. Scores are uncalibrated model outputs. "
    "Original recovery must use an authenticated encrypted copy of the original; "
    "the camouflage transform itself is not reversibly decryptable."
)


def _validate(raw: np.ndarray, labels: list[str], epsilon: float, steps: int) -> np.ndarray:
    if (isinstance(epsilon, (bool, np.bool_)) or not isinstance(epsilon, Real)
            or not 0 < epsilon <= 1 or not math.isfinite(float(epsilon))):
        raise ValueError("epsilon must be a finite number greater than 0 and at most 1")
    if (isinstance(steps, (bool, np.bool_)) or not isinstance(steps, Integral)
            or not 1 <= int(steps) <= 40):
        raise ValueError("steps must be an integer from 1 to 40")
    if (not isinstance(labels, list) or not labels
            or any(not isinstance(label, str) or not label for label in labels)
            or len(set(labels)) != len(labels)):
        raise ValueError("labels must be nonempty, unique class names in model output order")
    values = np.asarray(raw)
    if (values.ndim != 1 or values.size != 3600 or not np.issubdtype(values.dtype, np.number)
            or np.iscomplexobj(values) or not np.isfinite(values).all()):
        raise ValueError("raw must contain 3600 finite real ECG samples at 360 Hz")
    with np.errstate(over="ignore", invalid="ignore"):
        values = values.astype(np.float32, copy=True)
    if not np.isfinite(values).all():
        raise ValueError("raw values cannot be represented as finite float32 samples")
    return values


def _model_environment(model: torch.nn.Module) -> tuple[torch.device, torch.dtype]:
    tensor = next(model.parameters(), None)
    if tensor is None:
        tensor = next(model.buffers(), None)
    device = tensor.device if tensor is not None else torch.device("cpu")
    dtype = tensor.dtype if tensor is not None and tensor.is_floating_point() else torch.float32
    return device, dtype


def _scores(model: torch.nn.Module, x: torch.Tensor, labels: list[str]) -> tuple[dict, np.ndarray]:
    with torch.no_grad():
        logits = model(x)
        if logits.shape != (1, len(labels)) or not bool(torch.isfinite(logits).all()):
            raise ValueError("Model must return one finite logit for each class label")
        scores = torch.softmax(logits, dim=1)[0].detach().cpu().numpy()
    order = np.argsort(-scores, kind="stable")
    return {
        "label": labels[int(order[0])],
        "confidence": float(scores[order[0]]),
        "top_predictions": [
            {"label": labels[int(index)], "probability": float(scores[index])}
            for index in order[:3]
        ],
    }, scores


def generate_camouflage(
    model: torch.nn.Module,
    labels: list[str],
    raw: np.ndarray,
    reference_label: str | None,
    epsilon: float = 0.5,
    steps: int = 20,
) -> tuple[np.ndarray, dict]:
    """Return a changed 3600-sample raw waveform and truthful measured results.

    ``epsilon`` multiplies the original band-passed signal's robust MAD scale
    (1.4826 * median absolute deviation), defining an L-infinity raw-amplitude
    budget. A zero-scale recording is not modified. Intermediate candidates are
    projected to the budget; the strongest measured candidate is returned, with
    an early stop when the model label changes. The attack objective always
    moves away from the original *predicted* label, never fabricates a label or
    substitutes another patient's signal. Reference labels only assess results.

    No files are written. Input arrays, model parameters, parameter gradients,
    and mixed training/evaluation flags are preserved. Raw calculations use
    float32, the same representation used by the project's dataset loader.
    """
    original = _validate(raw, labels, epsilon, steps)
    epsilon, steps = float(epsilon), int(steps)
    device, dtype = _model_environment(model)

    def make_input(values: np.ndarray) -> tuple[np.ndarray, torch.Tensor]:
        normalized = model_input(preprocess(values[None]))[0]
        x = torch.as_tensor(normalized.copy(), dtype=dtype, device=device)[None, None, :]
        if not bool(torch.isfinite(x).all()):
            raise ValueError("Preprocessed waveform is not finite in the model's floating-point dtype")
        return normalized, x

    sos = butter(4, [0.5, 45.0], btype="bandpass", fs=360.0, output="sos")
    filtered = sosfiltfilt(sos, original.astype(np.float64))
    scale = float(np.median(np.abs(filtered - np.median(filtered))) * 1.4826)
    # Constant inputs can leave filter roundoff at 1e-13; do not turn that into
    # an artificial adversarial signal via the normalization floor.
    if float(np.ptp(original.astype(np.float64))) == 0 or scale < 1e-6:
        scale = 0.0
    budget = epsilon * scale
    modes = [(module, module.training) for module in model.modules()]
    best = original.copy()
    used = 0
    try:
        model.eval()
        clean_input, x = make_input(original)
        clean, clean_scores = _scores(model, x, labels)
        reference_index = labels.index(clean["label"])
        y = torch.tensor([reference_index], dtype=torch.long, device=device)
        best_prediction = clean
        best_score = float(clean_scores[reference_index])
        best_input = clean_input.copy()
        current = original.copy()
        raw_positions = np.arange(original.size)
        model_positions = np.arange(clean_input.size) * MODEL_DOWNSAMPLE
        if budget > 0:
            # Float32-representable bounds point inward, so rounding cannot
            # silently exceed the requested raw-amplitude perturbation budget.
            lower_exact = original.astype(np.float64) - budget
            upper_exact = original.astype(np.float64) + budget
            lower = lower_exact.astype(np.float32)
            upper = upper_exact.astype(np.float32)
            lower = np.where(lower < lower_exact, np.nextafter(lower, np.float32(np.inf)), lower)
            upper = np.where(upper > upper_exact, np.nextafter(upper, np.float32(-np.inf)), upper)
            step_size = 2.0 * budget / steps
            for iteration in range(steps):
                with torch.enable_grad():
                    gradient_input = x.detach().clone().requires_grad_(True)
                    logits = model(gradient_input)
                    if logits.shape != (1, len(labels)) or not bool(torch.isfinite(logits).all()):
                        raise ValueError("Model must return one finite logit for each class label")
                    loss = F.cross_entropy(logits, y)
                    gradient = (torch.autograd.grad(loss, gradient_input, allow_unused=True)[0]
                                if loss.requires_grad else None)
                if gradient is None or not bool(torch.isfinite(gradient).all()):
                    break
                gradient_values = gradient[0, 0].detach().cpu().numpy().astype(np.float64)
                if not np.any(gradient_values):
                    break
                lifted = np.interp(raw_positions, model_positions, gradient_values)
                current = np.clip(
                    current.astype(np.float64) + step_size * np.sign(lifted), lower, upper,
                ).astype(np.float32)
                candidate_input, x = make_input(current)
                candidate, scores = _scores(model, x, labels)
                used = iteration + 1
                if candidate["label"] != clean["label"] or float(scores[reference_index]) < best_score:
                    best = current.copy()
                    best_prediction = candidate
                    best_score = float(scores[reference_index])
                    best_input = candidate_input.copy()
                if candidate["label"] != clean["label"]:
                    break
    finally:
        for module, previous_mode in modes:
            module.training = previous_mode
    delta = best.astype(np.float64) - original.astype(np.float64)
    known_reference = isinstance(reference_label, str) and reference_label in labels
    clean_correct = clean["label"] == reference_label if known_reference else None
    camouflaged_correct = best_prediction["label"] == reference_label if known_reference else None
    result = {
        "method": METHOD,
        "epsilon": epsilon,
        "raw_budget": budget,
        "raw_budget_unit": "original MAT amplitude units",
        "reference_filtered_mad_scale": scale,
        "steps_requested": steps,
        "steps_used": used,
        "clean_prediction": clean,
        "camouflaged_prediction": best_prediction,
        "label_changed": clean["label"] != best_prediction["label"],
        "clean_correct": clean_correct,
        "camouflaged_correct": camouflaged_correct,
        "attack_success": bool(clean_correct and not camouflaged_correct) if known_reference else None,
        "attack_reference_label": clean["label"],
        "attack_reference_source": "clean prediction",
        "max_absolute_raw_change": float(np.abs(delta).max()),
        "mse": float(np.mean(delta ** 2)),
        "model_input_max_change": float(np.abs(best_input.astype(np.float64) - clean_input).max()),
        "note": NOTE,
    }
    return best, result
