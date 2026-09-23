"""Create an honest, model-specific camouflage + AES recovery batch.

Run with the project virtual environment; original MLII files are never edited.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import torch

from ecg_cvd.batch_crypto import encrypt_dataset
from ecg_cvd.gui import read_signal
from ecg_cvd.model import RAMNV2
from ecg_cvd.raw_camouflage import generate_camouflage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--checkpoint", default="artifacts_final/model.pt")
    parser.add_argument("--epsilon", type=float, default=0.5)
    parser.add_argument("--steps", type=int, default=20)
    args = parser.parse_args()
    root = args.project.resolve(strict=True)
    checkpoint = (root / args.checkpoint).resolve(strict=True)
    if not checkpoint.is_relative_to(root):
        parser.error("Checkpoint must be inside the project.")
    if not 0 < args.epsilon <= 1 or not 1 <= args.steps <= 40:
        parser.error("Use 0 < epsilon <= 1 and 1 <= steps <= 40.")
    torch.set_num_threads(2)
    saved = torch.load(checkpoint, map_location="cpu", weights_only=True)
    mapping = saved["label_map"]
    if sorted(mapping.values()) != list(range(saved["num_classes"])):
        raise ValueError("Invalid checkpoint label mapping.")
    labels = [label for label, _ in sorted(mapping.items(), key=lambda item: item[1])]
    model = RAMNV2(saved["num_classes"])
    model.load_state_dict(saved["state_dict"])
    model.eval()
    configuration = {"model": checkpoint.relative_to(root).as_posix(),
                     "model_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                     "epsilon": args.epsilon, "steps": args.steps}

    def generator(mat_bytes, reference):
        transformed, report = generate_camouflage(model, labels, read_signal(mat_bytes, "recording.mat"),
                                                  reference, args.epsilon, args.steps)
        report.update(model=configuration["model"], model_sha256=configuration["model_sha256"])
        return transformed, report

    def progress(done, total, message):
        if done % 25 == 0 or done == total:
            print(message, flush=True)

    print("Public altered signals are decoys, not encryption or proven attack prevention.", flush=True)
    print(json.dumps(encrypt_dataset(root, progress, camouflage_generator=generator,
                                     camouflage_config=configuration), indent=2), flush=True)


if __name__ == "__main__":
    main()
