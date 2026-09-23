from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
import torch
from scipy.io import loadmat
from .data import model_input, preprocess
from .model import RAMNV2
from .explain import grad_cam


def main():
    p=argparse.ArgumentParser(description="Predict one ECG CSV or MATLAB .mat file (research only; no clinical use).")
    p.add_argument("--checkpoint", type=Path, required=True); p.add_argument("--signal", type=Path, required=True)
    p.add_argument("--output", type=Path, default=Path("artifacts/explanation.png")); args=p.parse_args()
    if args.signal.suffix.lower() == ".mat":
        mat = loadmat(args.signal)
        arrays = [np.asarray(value).squeeze() for key, value in mat.items()
                  if not key.startswith("__") and isinstance(value, np.ndarray) and np.issubdtype(value.dtype, np.number)]
        if not arrays:
            raise SystemExit(f"No numeric ECG array found in {args.signal}.")
        raw = (mat["val"].squeeze() if "val" in mat else max(arrays, key=np.size)).reshape(-1)
    else:
        raw=np.loadtxt(args.signal, delimiter=",").reshape(-1)
    if len(raw) != 3600: raise SystemExit(f"Expected 3,600 samples (10 seconds at 360 Hz), got {len(raw)}.")
    signal=model_input(preprocess(raw[None]))[0]; device=torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    checkpoint=torch.load(args.checkpoint, map_location=device, weights_only=False)
    model=RAMNV2(checkpoint["num_classes"]).to(device); model.load_state_dict(checkpoint["state_dict"]); model.eval()
    with torch.no_grad():
        probs=torch.softmax(model(torch.tensor(signal[None,None],dtype=torch.float32,device=device)),1)[0].cpu().numpy()
    idx=int(probs.argmax()); labels=list(checkpoint["label_map"])
    grad_cam(model, signal, idx, args.output)
    print(json.dumps({"predicted_label": labels[idx], "confidence": float(probs[idx]), "explanation": str(args.output),
                      "safety": "Research output only; do not use for diagnosis or treatment."}, indent=2))

if __name__ == "__main__": main()
