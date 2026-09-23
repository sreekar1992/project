"""Exercise the local GUI's inference, file integrity, and request boundaries."""
import base64
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from scipy.io import savemat
import torch

from ecg_cvd.data import model_input, preprocess
from ecg_cvd.gui import create_app, read_signal
from ecg_cvd.model import RAMNV2


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def mat_bytes(values):
    stream = BytesIO()
    savemat(stream, {"val": values})
    return stream.getvalue()


class GUIAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_threads = torch.get_num_threads()
        torch.set_num_threads(2)
        cls.temp = TemporaryDirectory(prefix="ecg-gui-tests-")
        cls.root = Path(cls.temp.name)
        cls.model_id = "artifacts_final/model.pt"
        cls.sample_id = "MLII/1 NSR/sample.mat"
        cls.signal = np.sin(np.arange(3600, dtype=np.float32) / 17)
        cls.payload = mat_bytes(cls.signal)
        (cls.root / cls.sample_id).parent.mkdir(parents=True)
        (cls.root / cls.sample_id).write_bytes(cls.payload)
        (cls.root / cls.model_id).parent.mkdir(parents=True)
        model = RAMNV2(2)
        with torch.no_grad():
            model.classifier.weight.zero_()
            model.classifier.bias.copy_(torch.tensor([-2.0, 2.0]))
        # Deliberately put label order opposite to numeric class order.
        torch.save({"state_dict": model.state_dict(), "num_classes": 2,
                    "label_map": {"1 NSR": 1, "4 AFIB": 0}}, cls.root / cls.model_id)
        (cls.root / "artifacts_final/metrics.json").write_text(json.dumps({"accuracy": .87}))
        cls.app = create_app(cls.root)
        cls.app.config.update(TESTING=True)

    @classmethod
    def tearDownClass(cls):
        cls.app.extensions["ecg_executor"].shutdown(wait=True)
        cls.temp.cleanup()
        torch.set_num_threads(cls.previous_threads)

    def setUp(self):
        self.client = self.app.test_client()

    def post_file(self, endpoint, payload, name, key=None):
        data = {"file": (BytesIO(payload), name)}
        if key is not None:
            data["key"] = (BytesIO(key), "ecg.key")
        return self.client.post(endpoint, data=data)

    def assert_error(self, response, status=400):
        self.assertEqual(response.status_code, status, response.get_json())
        self.assertEqual(set(response.get_json()), {"error"})

    def test_state_lists_only_available_relative_identifiers(self):
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        state = response.get_json()
        self.assertEqual(set(state), {"models", "samples", "default_model", "dataset"})
        self.assertEqual(state["dataset"], {"classes": 1, "records": 1})
        self.assertEqual(state["default_model"], self.model_id)
        self.assertEqual([item["id"] for item in state["models"]], [self.model_id])
        self.assertEqual([item["id"] for item in state["samples"]], [self.sample_id])
        self.assertNotIn(str(self.root), response.get_data(as_text=True))

    def test_analyze_preserves_checkpoint_label_mapping_and_duration(self):
        response = self.client.post("/api/analyze", data={
            "model_id": self.model_id, "sample_id": self.sample_id})
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()
        self.assertEqual(result["label"], "1 NSR")
        self.assertEqual(result["label_name"], "Normal sinus rhythm")
        self.assertEqual(result["reference_label"], "1 NSR")
        self.assertEqual(result["top_predictions"][0]["label"], "1 NSR")
        self.assertAlmostEqual(result["confidence"], torch.sigmoid(torch.tensor(4.0)).item())
        self.assertEqual(result["duration_seconds"], 10)
        self.assertEqual(result["samples"], 3600)
        self.assertEqual(result["sampling_rate_hz"], 360)
        self.assertEqual(result["model_sampling_rate_hz"], 90)
        self.assertTrue(base64.b64decode(result["explanation"]).startswith(PNG_MAGIC))
        self.assertEqual(result["report"]["label"], result["label"])

    def test_uploaded_mat_is_analyzed_without_persisting_upload(self):
        response = self.client.post("/api/analyze", data={
            "model_id": self.model_id, "file": (BytesIO(self.payload), "uploaded.mat")})
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()
        self.assertIsNone(result["reference_label"])
        self.assertEqual(result["sample_name"], "uploaded.mat")
        self.assertFalse((self.root / "uploaded.mat").exists())

    def test_malformed_and_nonfinite_signals_return_actionable_errors(self):
        cases = [(b"", "empty.mat"), (b"not a MAT file", "bad.mat"),
                 (b"x,y,z", "bad.csv"), (b"1,2,3", "short.csv"),
                 (mat_bytes(np.full(3600, np.nan)), "nan.mat"),
                 (mat_bytes(np.ones(3600)), "flat.mat"),
                 (mat_bytes(np.ones((2, 3600))), "two-leads.mat"),
                 (PNG_MAGIC, "image.png")]
        for payload, name in cases:
            with self.subTest(name=name):
                response = self.client.post("/api/analyze", data={
                    "model_id": self.model_id, "file": (BytesIO(payload), name)})
                self.assert_error(response)

    def test_arbitrary_model_and_sample_paths_are_rejected(self):
        for invalid in ("../../outside/model.pt", "/etc/passwd", str(self.root / self.model_id)):
            with self.subTest(model=invalid):
                self.assert_error(self.client.post("/api/analyze", data={
                    "model_id": invalid, "sample_id": self.sample_id}))
        for invalid in ("../../outside.mat", "/etc/passwd", str(self.root / self.sample_id)):
            with self.subTest(sample=invalid):
                self.assert_error(self.client.post("/api/analyze", data={
                    "model_id": self.model_id, "sample_id": invalid}))

    def test_symlinks_to_files_outside_project_are_not_listed(self):
        with TemporaryDirectory(prefix="ecg-gui-outside-") as outside:
            target = Path(outside) / "outside.bin"
            target.write_bytes(b"not an ECG")
            sample_link = self.root / "MLII/1 NSR/outside.mat"
            model_link = self.root / "artifacts_external/model.pt"
            model_link.parent.mkdir()
            sample_link.symlink_to(target)
            model_link.symlink_to(target)
            try:
                state = self.client.get("/api/state").get_json()
                self.assertNotIn("MLII/1 NSR/outside.mat", [x["id"] for x in state["samples"]])
                self.assertNotIn("artifacts_external/model.pt", [x["id"] for x in state["models"]])
            finally:
                sample_link.unlink()
                model_link.unlink()
                model_link.parent.rmdir()

    def test_encrypt_new_key_and_reuse_roundtrip_with_fresh_nonces(self):
        plaintext = PNG_MAGIC + bytes(range(256))
        first = self.post_file("/api/encrypt", plaintext, "ecg.png")
        self.assertEqual(first.status_code, 200)
        encrypted = first.get_json()
        self.assertEqual(encrypted["filename"], "ecg.png.ecgenc")
        key = encrypted["key"].encode("ascii")
        self.assertEqual(len(bytes.fromhex(key.decode())), 32)
        restored = self.post_file("/api/decrypt", base64.b64decode(encrypted["ciphertext"]),
                                  encrypted["filename"], key)
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(base64.b64decode(restored.get_json()["plaintext"]), plaintext)
        self.assertEqual(restored.get_json()["mime"], "image/png")
        self.assertEqual(restored.get_json()["filename"], "ecg.png")
        second = self.post_file("/api/encrypt", plaintext, "ecg.png", key).get_json()
        self.assertIsNone(second["key"])
        self.assertNotEqual(second["ciphertext"], encrypted["ciphertext"])
        again = self.post_file("/api/decrypt", base64.b64decode(second["ciphertext"]),
                              second["filename"], key)
        self.assertEqual(base64.b64decode(again.get_json()["plaintext"]), plaintext)

    def test_empty_file_encrypts_and_decrypts(self):
        encrypted = self.post_file("/api/encrypt", b"", "empty.bin").get_json()
        restored = self.post_file("/api/decrypt", base64.b64decode(encrypted["ciphertext"]),
                                  encrypted["filename"], encrypted["key"].encode("ascii"))
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(base64.b64decode(restored.get_json()["plaintext"]), b"")

    def test_tampered_ciphertext_and_wrong_key_never_return_plaintext(self):
        key = b"01" * 32
        encrypted = self.post_file("/api/encrypt", b"private ECG image", "ecg.png", key).get_json()
        ciphertext = base64.b64decode(encrypted["ciphertext"])
        tampered = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
        for payload, candidate in ((tampered, key), (ciphertext, b"02" * 32)):
            with self.subTest(tampered=payload == tampered):
                response = self.post_file("/api/decrypt", payload, "ecg.png.ecgenc", candidate)
                self.assert_error(response)
                self.assertNotIn("private ECG image", response.get_data(as_text=True))

    def test_missing_and_malformed_keys_are_rejected(self):
        self.assert_error(self.post_file("/api/decrypt", b"ciphertext", "test.ecgenc"))
        for key in (b"", b"not-hex", b"01" * 31, b"\xff" * 64):
            with self.subTest(length=len(key)):
                self.assert_error(self.post_file("/api/encrypt", b"ecg", "test.bin", key))

    def test_cross_origin_posts_and_untrusted_hosts_are_rejected(self):
        for headers in ({"Origin": "https://example.com"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                response = self.client.post("/api/encrypt", headers=headers,
                                            data={"file": (BytesIO(b"ecg"), "ecg.png")})
                self.assert_error(response, 403)
        self.assert_error(self.client.get("/api/state", headers={"Host": "example.com"}))
        same_origin = self.client.post("/api/encrypt", headers={"Origin": "http://localhost"},
                                       data={"file": (BytesIO(b"ecg"), "ecg.png")})
        self.assertEqual(same_origin.status_code, 200)

    def test_api_responses_prevent_caching_and_framing(self):
        response = self.client.get("/api/state")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")


@unittest.skipUnless((PROJECT_ROOT / "artifacts_final/model.pt").exists()
                     and (PROJECT_ROOT / "MLII").exists(), "Local trained model and MLII data unavailable")
class RealDatasetGUITest(unittest.TestCase):
    def test_real_mat_prediction_matches_checkpoint_and_explanation_is_png(self):
        previous_threads = torch.get_num_threads()
        torch.set_num_threads(2)
        app = create_app(PROJECT_ROOT)
        try:
            path = next((PROJECT_ROOT / "MLII/1 NSR").glob("*.mat"))
            checkpoint = torch.load(PROJECT_ROOT / "artifacts_final/model.pt", map_location="cpu", weights_only=True)
            model = RAMNV2(checkpoint["num_classes"]).eval()
            model.load_state_dict(checkpoint["state_dict"])
            signal = model_input(preprocess(read_signal(path.read_bytes(), path.name)[None]))
            with torch.no_grad():
                probabilities = model(torch.from_numpy(signal[:, None])).softmax(1)[0]
            expected_index = int(probabilities.argmax())
            expected_label = next(label for label, index in checkpoint["label_map"].items()
                                  if index == expected_index)
            response = app.test_client().post("/api/analyze", data={
                "model_id": "artifacts_final/model.pt", "sample_id": path.relative_to(PROJECT_ROOT).as_posix()})
            self.assertEqual(response.status_code, 200, response.get_json())
            result = response.get_json()
            self.assertEqual(result["label"], expected_label)
            self.assertAlmostEqual(result["confidence"], float(probabilities[expected_index]), places=6)
            self.assertEqual(result["reference_label"], "1 NSR")
            self.assertEqual(result["duration_seconds"], 10)
            self.assertTrue(base64.b64decode(result["explanation"]).startswith(PNG_MAGIC))
        finally:
            app.extensions["ecg_executor"].shutdown(wait=True)
            torch.set_num_threads(previous_threads)


if __name__ == "__main__":
    unittest.main()
