from __future__ import annotations

import json
import hashlib
from pathlib import Path
import tempfile
import unittest

import torch

from ecg_cvd.export_model import export_checkpoint
from ecg_cvd.model import RAMNV2


class ExportModelTests(unittest.TestCase):
    def _checkpoint(self, path: Path) -> None:
        model = RAMNV2(2).eval()
        torch.save(
            {
                "state_dict": model.state_dict(),
                "num_classes": 2,
                "label_map": {"normal": 0, "other": 1},
                "preprocessing": {"sampling_rate_hz": 360, "downsample_factor": 4},
                "training_config": {"seed": 42},
            },
            path,
        )

    def test_bundle_and_torchscript_are_verified_and_preserve_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "source.pt"
            self._checkpoint(checkpoint)
            (root / "metrics.json").write_text('{"accuracy": 0.5}\n', encoding="utf-8")

            manifest = export_checkpoint(checkpoint, root / "portable", ["torchscript"])

            exported = root / "portable"
            self.assertTrue((exported / "model.pt").is_file())
            self.assertTrue((exported / "label_map.json").is_file())
            self.assertTrue((exported / "metrics.json").is_file())
            self.assertTrue((exported / "model.ts").is_file())
            self.assertTrue((exported / "model_manifest.json").is_file())
            self.assertEqual(manifest["labels"], ["normal", "other"])
            self.assertEqual(manifest["input_tensor"]["shape"], [1, 1, 900])
            self.assertIn("torchscript", manifest["runtime_exports"])
            self.assertNotIn("model_manifest.json", manifest["files"])
            self.assertEqual(manifest["source_checkpoint"]["sha256"], manifest["files"]["model.pt"]["sha256"])
            self.assertEqual(manifest["preprocessing_source"], "checkpoint")

            for filename, details in manifest["files"].items():
                payload = exported / filename
                self.assertEqual(payload.stat().st_size, details["bytes"])
                self.assertEqual(hashlib.sha256(payload.read_bytes()).hexdigest(), details["sha256"])

            saved = json.loads((exported / "model_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["source_checkpoint"]["sha256"], manifest["source_checkpoint"]["sha256"])
            runtime = torch.jit.load(str(exported / "model.ts"), map_location="cpu")
            self.assertEqual(tuple(runtime(torch.zeros(1, 1, 900)).shape), (1, 2))

    def test_existing_export_directory_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "source.pt"
            self._checkpoint(checkpoint)
            existing = root / "portable"
            existing.mkdir()
            with self.assertRaises(FileExistsError):
                export_checkpoint(checkpoint, existing)

    def test_invalid_label_map_is_rejected_without_creating_an_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "source.pt"
            model = RAMNV2(2).eval()
            torch.save(
                {
                    "state_dict": model.state_dict(),
                    "num_classes": 2,
                    "label_map": {"normal": False, "other": 1},
                },
                checkpoint,
            )
            destination = root / "portable"
            with self.assertRaisesRegex(ValueError, "integer indexes"):
                export_checkpoint(checkpoint, destination)
            self.assertFalse(destination.exists())

    def test_legacy_checkpoint_uses_documented_compatibility_preprocessing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "source.pt"
            model = RAMNV2(2).eval()
            torch.save(
                {
                    "state_dict": model.state_dict(),
                    "num_classes": 2,
                    "label_map": {"normal": 0, "other": 1},
                },
                checkpoint,
            )
            manifest = export_checkpoint(checkpoint, root / "portable")
            self.assertEqual(manifest["preprocessing_source"], "checkpoint-compatible project default")
            self.assertEqual(manifest["preprocessing"]["model_input_samples"], 900)


if __name__ == "__main__":
    unittest.main()
