from __future__ import annotations
from pathlib import Path
from io import BytesIO
import numpy as np
import torch
import torch.nn.functional as F
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def grad_cam(model: torch.nn.Module, signal: np.ndarray, class_index: int,
             output: Path | BytesIO, fs: float = 90.0) -> None:
    """Save a Grad-CAM overlay for one normalized ECG recording."""
    model.eval()
    activations, gradients = [], []
    target = model.blocks[-1]
    handles = [target.register_forward_hook(lambda _m, _i, o: activations.append(o)),
               target.register_full_backward_hook(lambda _m, _gi, go: gradients.append(go[0]))]
    try:
        device = next(model.parameters()).device
        x = torch.tensor(signal[None, None], dtype=torch.float32, device=device, requires_grad=True)
        score = model(x)[0, class_index]
        model.zero_grad(set_to_none=True); score.backward()
        weights = gradients[0].mean(dim=-1, keepdim=True)
        cam = F.relu((weights * activations[0]).sum(dim=1)).detach().cpu().numpy()[0]
        cam = np.interp(np.linspace(0, len(cam) - 1, len(signal)), np.arange(len(cam)), cam)
        cam = (cam - cam.min()) / (np.ptp(cam) + 1e-8)
        t = np.arange(len(signal)) / fs
        fig, ax = plt.subplots(figsize=(14, 3))
        ax.plot(t, signal, color="black", linewidth=.8)
        ax.scatter(t, signal, c=cam, cmap="turbo", s=3, label="Grad-CAM relevance")
        ax.set(xlabel="Time (s)", ylabel="Normalized amplitude", title="ECG prediction relevance (research only)")
        fig.colorbar(ax.collections[0], ax=ax, label="relative relevance")
        fig.tight_layout()
        if isinstance(output, Path):
            output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, format="png", dpi=160)
        plt.close(fig)
    finally:
        for handle in handles: handle.remove()
