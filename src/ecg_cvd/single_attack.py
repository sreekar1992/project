"""A transparent, single-record FGSM research comparison (not encryption)."""
from __future__ import annotations

import io
import math
from numbers import Real
import threading

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
import numpy as np
import torch

from .robustness import fgsm


_RENDER_LOCK = threading.Lock()
_NOTE = (
    "Research perturbation of normalized ECG model input, not encryption or a clinical "
    "condition. FGSM only; not full RNAF/PPO defense. Scores are uncalibrated model "
    "outputs; a changed label is not a new diagnosis. Tiny differences around the "
    "epsilon bound can arise from floating-point rounding."
)


def _prediction(model: torch.nn.Module, x: torch.Tensor, labels: list[str]) -> dict:
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
    }


def _comparison_png(clean: np.ndarray, attacked: np.ndarray, result: dict) -> bytes:
    """Render the actual model inputs at their model sampling rate, in memory."""
    time = np.arange(clean.size) / 90.0
    low, high = float(min(clean.min(), attacked.min())), float(max(clean.max(), attacked.max()))
    pad = max((high - low) * 0.08, 0.05)
    with _RENDER_LOCK:
        fig = Figure(figsize=(11, 6), dpi=120, constrained_layout=True)
        FigureCanvasAgg(fig)
        clean_ax, attacked_ax, delta_ax = fig.subplots(3, 1, sharex=True)
        for axis, values, color, caption in (
            (clean_ax, clean, "#137f70", "Clean input"),
            (attacked_ax, attacked, "#b55826", f"FGSM input (epsilon={result['epsilon']:g})"),
        ):
            prediction = result["clean_prediction" if axis is clean_ax else "attacked_prediction"]
            axis.plot(time, values, color=color, linewidth=0.8)
            axis.set_ylim(low - pad, high + pad)
            axis.set_ylabel("Normalized amplitude")
            axis.set_title(
                f"{caption}: {prediction['label']} | model score {prediction['confidence']:.2%}",
                loc="left", fontsize=10,
            )
        delta_ax.plot(time, attacked - clean, color="#7556a3", linewidth=0.7)
        delta_ax.set_title("Actual perturbation (FGSM minus clean input)", loc="left", fontsize=10)
        delta_ax.set_ylabel("Amplitude difference")
        delta_ax.set_xlabel("Time (seconds); normalized, downsampled model input at 90 Hz")
        for axis in (clean_ax, attacked_ax, delta_ax):
            axis.grid(alpha=0.2)
            axis.set_xlim(0, clean.size / 90.0)
        fig.suptitle("Research-only adversarial stress test — separate from encryption", fontsize=12)
        output = io.BytesIO()
        fig.savefig(output, format="png", metadata={"Software": "ECG research FGSM comparison"})
        return output.getvalue()


def evaluate_single_attack(
    model: torch.nn.Module,
    labels: list[str],
    signal: np.ndarray,
    reference_label: str | None,
    epsilon: float = 0.05,
) -> tuple[dict, bytes]:
    """Compare clean versus bounded FGSM predictions without saving plaintext.

    ``signal`` must already be the 900-sample, normalized 90 Hz input used by
    this project's model. No label flip is forced. Model training flags,
    parameters, buffers, and existing parameter gradients are preserved.
    A dataset label is required to report correctness or attack success.
    """
    if isinstance(epsilon, (bool, np.bool_)) or not isinstance(epsilon, Real):
        raise ValueError("epsilon must be a finite number from 0 to 1")
    epsilon = float(epsilon)
    if not math.isfinite(epsilon) or not 0 <= epsilon <= 1:
        raise ValueError("epsilon must be a finite number from 0 to 1")
    if (not isinstance(labels, list) or not labels
            or any(not isinstance(label, str) or not label for label in labels)
            or len(set(labels)) != len(labels)):
        raise ValueError("labels must be nonempty, unique class names in model output order")
    values = np.asarray(signal)
    if (values.ndim != 1 or values.size != 900 or not np.issubdtype(values.dtype, np.number)
            or np.iscomplexobj(values) or not np.isfinite(values).all()):
        raise ValueError("signal must contain 900 finite real model-input samples at 90 Hz")
    device_tensor = next(model.parameters(), None)
    if device_tensor is None:
        device_tensor = next(model.buffers(), None)
    device = device_tensor.device if device_tensor is not None else torch.device("cpu")
    dtype = (device_tensor.dtype if device_tensor is not None and device_tensor.is_floating_point()
             else torch.float32)
    x = torch.as_tensor(values.copy(), device=device, dtype=dtype)[None, None, :]
    if not bool(torch.isfinite(x).all()):
        raise ValueError("signal values cannot be represented in the model's floating-point dtype")
    modes = [(module, module.training) for module in model.modules()]
    known_reference = isinstance(reference_label, str) and reference_label in labels
    try:
        model.eval()
        clean = _prediction(model, x, labels)
        attack_reference = reference_label if known_reference else clean["label"]
        y = torch.tensor([labels.index(attack_reference)], dtype=torch.long, device=device)
        attacked_x = fgsm(model, x, y, epsilon)
        attacked = _prediction(model, attacked_x, labels)
    finally:
        # Preserve even intentionally mixed train/eval configurations.
        for module, previous_mode in modes:
            module.training = previous_mode
    clean_values = x[0, 0].detach().cpu().numpy().astype(np.float64)
    attacked_values = attacked_x[0, 0].detach().cpu().numpy().astype(np.float64)
    delta = attacked_values - clean_values
    clean_correct = clean["label"] == reference_label if known_reference else None
    attacked_correct = attacked["label"] == reference_label if known_reference else None
    result = {
        "method": "FGSM",
        "epsilon": epsilon,
        "perturbation_domain": "normalized, downsampled ECG amplitude",
        "objective": "untargeted",
        "attack_reference_label": attack_reference,
        "attack_reference_source": "dataset label" if known_reference else "clean prediction",
        "clean_prediction": clean,
        "attacked_prediction": attacked,
        "label_changed": clean["label"] != attacked["label"],
        "clean_correct": clean_correct,
        "attacked_correct": attacked_correct,
        "attack_success": bool(clean_correct and not attacked_correct) if known_reference else None,
        "max_absolute_perturbation": float(np.abs(delta).max()),
        "mse": float(np.mean(delta ** 2)),
        "note": _NOTE,
    }
    return result, _comparison_png(clean_values, attacked_values, result)
