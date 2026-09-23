"""Password-gated ECG inference must persist ciphertext, not intermediate images."""
import base64
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from scipy.io import savemat
import torch

from ecg_cvd.gui import create_app
from ecg_cvd.model import RAMNV2


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PASSWORD = "Long test-only passphrase 739!"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SecureGUIAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_threads = torch.get_num_threads()
        torch.set_num_threads(2)
        model = RAMNV2(2)
        with torch.no_grad():
            model.classifier.weight.zero_()
            model.classifier.bias.copy_(torch.tensor([-2.0, 2.0]))
        checkpoint = BytesIO()
        torch.save({"state_dict": model.state_dict(), "num_classes": 2,
                    "label_map": {"1 NSR": 1, "4 AFIB": 0}}, checkpoint)
        cls.checkpoint = checkpoint.getvalue()
        signal = np.sin(np.arange(3600, dtype=np.float32) / 17)
        mat = BytesIO()
        savemat(mat, {"val": signal})
        cls.mat = mat.getvalue()
        cls.csv = ",".join(str(value) for value in signal).encode("ascii")

    @classmethod
    def tearDownClass(cls):
        torch.set_num_threads(cls.previous_threads)

    def setUp(self):
        self.temp = TemporaryDirectory(prefix="ecg-secure-gui-tests-")
        self.root = Path(self.temp.name)
        self.model_id = "artifacts_final/model.pt"
        self.sample_id = "MLII/1 NSR/sample.mat"
        (self.root / self.model_id).parent.mkdir(parents=True)
        (self.root / self.model_id).write_bytes(self.checkpoint)
        (self.root / self.sample_id).parent.mkdir(parents=True)
        (self.root / self.sample_id).write_bytes(self.mat)
        self.app = create_app(self.root)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["ecg_executor"].shutdown(wait=True)
        self.temp.cleanup()

    def assert_error(self, response, status=400):
        self.assertEqual(response.status_code, status, response.get_json())
        self.assertEqual(set(response.get_json()), {"error"})
        self.assertNotIn(PASSWORD, response.get_data(as_text=True))

    def encrypt_sample(self):
        response = self.client.post("/api/secure/encrypt-signal", data={
            "sample_id": self.sample_id, "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def analyze(self, encrypted, password=PASSWORD):
        return self.client.post("/api/secure/analyze", data={
            "artifact_id": encrypted["artifact_id"], "password": password,
            "model_id": self.model_id})

    def decrypt(self, encrypted, password=PASSWORD):
        return self.client.post("/api/secure/decrypt", data={
            "file": (BytesIO(base64.b64decode(encrypted["ciphertext"])), encrypted["filename"]),
            "password": password})

    def secure_files(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in (self.root / "artifacts_secure").rglob("*") if path.is_file()}

    def assert_no_plaintext_intermediates(self):
        originals = {self.model_id, self.sample_id}
        new_files = [path for path in self.root.rglob("*")
                     if path.is_file() and path.relative_to(self.root).as_posix() not in originals]
        self.assertTrue(new_files)
        for path in new_files:
            self.assertTrue(path.name.endswith(".ecgenc"), path)
            self.assertNotIn(PASSWORD.encode(), path.read_bytes())

    def assert_encrypted_artifact(self, value, folder):
        for field in ("artifact_id", "filename", "ciphertext", "saved_path"):
            self.assertIn(field, value)
        self.assertTrue(value["filename"].endswith(".ecgenc"))
        path = Path(value["saved_path"])
        if not path.is_absolute():
            path = self.root / path
        self.assertTrue(path.resolve().is_relative_to((self.root / "artifacts_secure" / folder).resolve()))
        ciphertext = base64.b64decode(value["ciphertext"], validate=True)
        self.assertEqual(path.read_bytes(), ciphertext)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(ciphertext.startswith(PNG_MAGIC))
        self.assertNotIn(PASSWORD.encode(), ciphertext)
        self.assertNotIn(PASSWORD, json.dumps(value))

    def test_dataset_encryption_preserves_source_and_produces_fresh_ciphertext(self):
        first = self.encrypt_sample()
        second = self.encrypt_sample()
        self.assert_encrypted_artifact(first, "inputs")
        self.assert_encrypted_artifact(second, "inputs")
        self.assertIn("AES", first["algorithm"])
        self.assertNotEqual(first["artifact_id"], second["artifact_id"])
        self.assertNotEqual(first["ciphertext"], second["ciphertext"])
        self.assertEqual((self.root / self.sample_id).read_bytes(), self.mat)
        self.assertTrue(all(name.endswith(".ecgenc") for name in self.secure_files()))
        restored = self.decrypt(first)
        self.assertEqual(restored.status_code, 200, restored.get_json())
        self.assertEqual(restored.get_json()["filename"], "sample.mat")
        self.assertEqual(base64.b64decode(restored.get_json()["plaintext"]), self.mat)

    def test_secure_prediction_matches_plain_inference_and_encrypts_image_and_report(self):
        plain = self.client.post("/api/analyze", data={
            "sample_id": self.sample_id, "model_id": self.model_id}).get_json()
        encrypted = self.encrypt_sample()
        response = self.analyze(encrypted)
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()
        for field in ("label", "label_name", "reference_label", "top_predictions", "samples"):
            self.assertEqual(result[field], plain[field])
        self.assertAlmostEqual(result["confidence"], plain["confidence"], places=6)
        self.assertNotIn("explanation", result)
        self.assertNotIn(PASSWORD, response.get_data(as_text=True))
        self.assert_encrypted_artifact(result["encrypted_image"], "outputs")
        self.assert_encrypted_artifact(result["encrypted_report"], "outputs")
        before_preview = self.secure_files()
        image = self.decrypt(result["encrypted_image"])
        self.assertEqual(image.status_code, 200, image.get_json())
        self.assertEqual(image.get_json()["mime"], "image/png")
        self.assertTrue(base64.b64decode(image.get_json()["plaintext"]).startswith(PNG_MAGIC))
        report_response = self.decrypt(result["encrypted_report"])
        self.assertEqual(report_response.status_code, 200, report_response.get_json())
        report = json.loads(base64.b64decode(report_response.get_json()["plaintext"]))
        self.assertEqual(report["label"], result["label"])
        self.assertAlmostEqual(report["confidence"], result["confidence"])
        self.assertEqual(self.secure_files(), before_preview)
        self.assertTrue(all(name.endswith(".ecgenc") for name in before_preview))
        self.assertEqual(len(before_preview), 3)
        self.assertEqual((self.root / self.sample_id).read_bytes(), self.mat)
        self.assert_no_plaintext_intermediates()

    def test_uploaded_mat_and_csv_can_be_encrypted_then_analyzed_without_plaintext_files(self):
        for content, filename in ((self.mat, "uploaded.mat"), (self.csv, "uploaded.csv")):
            with self.subTest(filename=filename):
                encrypted_response = self.client.post("/api/secure/encrypt-signal", data={
                    "file": (BytesIO(content), filename), "password": PASSWORD})
                self.assertEqual(encrypted_response.status_code, 200, encrypted_response.get_json())
                encrypted = encrypted_response.get_json()
                response = self.client.post("/api/secure/analyze", data={
                    "file": (BytesIO(base64.b64decode(encrypted["ciphertext"])), encrypted["filename"]),
                    "password": PASSWORD, "model_id": self.model_id})
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertIsNone(response.get_json()["reference_label"])
                self.assertEqual(response.get_json()["sample_name"], filename)
                self.assertFalse((self.root / filename).exists())
        self.assertTrue(all(name.endswith(".ecgenc") for name in self.secure_files()))
        self.assert_no_plaintext_intermediates()

    def test_wrong_or_missing_password_does_not_load_model_or_create_outputs(self):
        encrypted = self.encrypt_sample()
        before = self.secure_files()
        with patch("ecg_cvd.gui.torch.load", side_effect=AssertionError("Model was loaded before authentication")):
            for password in ("", "Wrong test-only passphrase 739!"):
                with self.subTest(password_present=bool(password)):
                    self.assert_error(self.analyze(encrypted, password))
            self.assert_error(self.client.post("/api/secure/analyze", data={
                "artifact_id": encrypted["artifact_id"], "model_id": self.model_id}))
        self.assertEqual(self.secure_files(), before)

    def test_tampered_input_is_rejected_before_model_loading(self):
        encrypted = self.encrypt_sample()
        original = base64.b64decode(encrypted["ciphertext"])
        tampered = original[:-1] + bytes([original[-1] ^ 1])
        before = self.secure_files()
        with patch("ecg_cvd.gui.torch.load", side_effect=AssertionError("Model loaded for tampered input")):
            response = self.client.post("/api/secure/analyze", data={
                "file": (BytesIO(tampered), encrypted["filename"]),
                "password": PASSWORD, "model_id": self.model_id})
        self.assert_error(response)
        self.assertEqual(self.secure_files(), before)

    def test_output_wrong_password_and_tampering_never_returns_plaintext(self):
        result = self.analyze(self.encrypt_sample()).get_json()
        encrypted = result["encrypted_image"]
        self.assert_error(self.decrypt(encrypted, "Wrong password with enough characters"))
        ciphertext = base64.b64decode(encrypted["ciphertext"])
        tampered = dict(encrypted, ciphertext=base64.b64encode(
            ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])).decode("ascii"))
        self.assert_error(self.decrypt(tampered))

    def test_encryption_requires_password_and_exactly_one_valid_source(self):
        requests = [
            {"sample_id": self.sample_id},
            {"sample_id": self.sample_id, "password": ""},
            {"password": PASSWORD},
            {"password": PASSWORD, "sample_id": "../../outside.mat"},
            {"password": PASSWORD, "sample_id": self.sample_id,
             "file": (BytesIO(self.mat), "uploaded.mat")},
            {"password": PASSWORD, "file": (BytesIO(b"bad"), "bad.mat")},
        ]
        for data in requests:
            with self.subTest(fields=list(data)):
                self.assert_error(self.client.post("/api/secure/encrypt-signal", data=data))
        self.assertEqual(self.secure_files(), {})

    def test_analysis_rejects_multiple_sources_and_artifact_path_traversal(self):
        encrypted = self.encrypt_sample()
        before = self.secure_files()
        candidates = ("../../outside.ecgenc", "/etc/passwd", "../inputs/" + encrypted["filename"],
                      str(self.root / "artifacts_secure/inputs" / encrypted["filename"]))
        for identifier in candidates:
            with self.subTest(identifier=identifier):
                self.assert_error(self.client.post("/api/secure/analyze", data={
                    "artifact_id": identifier, "model_id": self.model_id, "password": PASSWORD}))
        self.assert_error(self.client.post("/api/secure/analyze", data={
            "artifact_id": encrypted["artifact_id"], "model_id": self.model_id, "password": PASSWORD,
            "file": (BytesIO(base64.b64decode(encrypted["ciphertext"])), encrypted["filename"])}))
        self.assertEqual(self.secure_files(), before)

    def test_secure_endpoints_reject_cross_origin_requests_and_disable_cache(self):
        for endpoint in ("encrypt-signal", "analyze", "decrypt"):
            for headers in ({"Origin": "https://example.com"}, {"Sec-Fetch-Site": "cross-site"}):
                with self.subTest(endpoint=endpoint, headers=headers):
                    response = self.client.post("/api/secure/" + endpoint, headers=headers,
                                                data={"password": PASSWORD})
                    self.assert_error(response, 403)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    def test_artifact_symlinks_cannot_escape_secure_storage(self):
        encrypted = self.encrypt_sample()
        path = Path(encrypted["saved_path"])
        if not path.is_absolute():
            path = self.root / path
        with TemporaryDirectory(prefix="ecg-secure-outside-") as outside:
            target = Path(outside) / "copied.ecgenc"
            target.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(target)
            with patch("ecg_cvd.gui.torch.load", side_effect=AssertionError("External artifact reached model")):
                self.assert_error(self.analyze(encrypted))

    @unittest.skipUnless((PROJECT_ROOT / "artifacts_final/model.pt").is_file()
                         and (PROJECT_ROOT / "MLII/1 NSR").is_dir(),
                         "Local trained model and MLII dataset unavailable")
    def test_real_checkpoint_and_mat_secure_prediction_matches_original(self):
        # Copy into the isolated fixture so this test never writes project results.
        source = next((PROJECT_ROOT / "MLII/1 NSR").glob("*.mat"))
        (self.root / self.model_id).write_bytes((PROJECT_ROOT / self.model_id).read_bytes())
        (self.root / self.sample_id).write_bytes(source.read_bytes())
        plain_response = self.client.post("/api/analyze", data={
            "sample_id": self.sample_id, "model_id": self.model_id})
        self.assertEqual(plain_response.status_code, 200, plain_response.get_json())
        response = self.analyze(self.encrypt_sample())
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["label"], plain_response.get_json()["label"])
        self.assertEqual(response.get_json()["top_predictions"], plain_response.get_json()["top_predictions"])
        self.assertAlmostEqual(response.get_json()["confidence"],
                               plain_response.get_json()["confidence"], places=6)
        self.assertTrue(all(name.endswith(".ecgenc") for name in self.secure_files()))


if __name__ == "__main__":
    unittest.main()
