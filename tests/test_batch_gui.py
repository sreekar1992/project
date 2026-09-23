"""Dataset-wide encryption, password-gated previews, and comparison API checks."""
import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import numpy as np
from scipy.io import savemat
import torch

from ecg_cvd.gui import create_app
from ecg_cvd.model import RAMNV2
from ecg_cvd.batch_crypto import decrypt_record
from ecg_cvd.crypto import decrypt_bytes
from ecg_cvd.care import care_information


PASSWORD = "987654321"  # User-requested demonstration password, not a production secret.
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class BatchGUIAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_threads = torch.get_num_threads()
        torch.set_num_threads(2)
        model = RAMNV2(2)
        with torch.no_grad():
            model.classifier.weight.zero_()
            model.classifier.bias.copy_(torch.tensor([-2.0, 2.0]))
        buffer = BytesIO()
        torch.save({"state_dict": model.state_dict(), "num_classes": 2,
                    "label_map": {"1 NSR": 1, "4 AFIB": 0}}, buffer)
        cls.checkpoint = buffer.getvalue()
        cls.signals = {}
        for index, label in enumerate(("1 NSR", "4 AFIB")):
            stream = BytesIO()
            signal = np.sin(np.arange(3600, dtype=np.float32) / (17 + index * 3))
            savemat(stream, {"val": signal})
            cls.signals[f"MLII/{label}/{100 + index}m (0).mat"] = stream.getvalue()

    @classmethod
    def tearDownClass(cls):
        torch.set_num_threads(cls.previous_threads)

    def setUp(self):
        self.temp = TemporaryDirectory(prefix="ecg-batch-gui-tests-")
        self.root = Path(self.temp.name)
        self.model_id = "artifacts_final/model.pt"
        for name, payload in {**self.signals, self.model_id: self.checkpoint}.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
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

    def create_batch(self):
        response = self.client.post("/api/dataset/encrypt", json={})
        self.assertEqual(response.status_code, 202, response.get_json())
        identifier = response.get_json()["job_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/jobs/{identifier}")
            self.assertEqual(response.status_code, 200, response.get_json())
            job = response.get_json()
            if job["status"] in ("done", "error"):
                break
            time.sleep(.02)
        else:
            self.fail("The two-record encryption job did not finish in 30 seconds.")
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(job["progress"], 100)
        return job["result"]

    def records(self, batch):
        response = self.client.get("/api/dataset/records", query_string={"batch_id": batch["batch_id"]})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["records"]

    def decrypt(self, batch, record, password=PASSWORD, **extra):
        return self.client.post("/api/dataset/decrypt", json={
            "batch_id": batch["batch_id"], "record_id": record["record_id"],
            "password": password, "model_id": self.model_id, **extra})

    def artifact_files(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for folder in ("artifacts_dataset", "artifacts_secure")
                for path in (self.root / folder).rglob("*") if path.is_file()}

    def test_batch_encryption_requires_no_password_and_reports_all_records(self):
        state = self.client.get("/api/dataset/state")
        self.assertEqual(state.status_code, 200, state.get_json())
        self.assertEqual(state.get_json()["batches"], [])
        self.assertIsNone(state.get_json()["default_batch"])
        batch = self.create_batch()
        self.assertEqual(batch["count"], 2)
        self.assertEqual(batch["mat_count"], 2)
        self.assertEqual(batch["image_count"], 2)
        records = self.records(batch)
        self.assertEqual(len(records), 2)
        state = self.client.get("/api/dataset/state").get_json()
        self.assertEqual(state["default_batch"], batch["batch_id"])
        self.assertEqual(len(state["batches"]), 1)
        self.assertNotIn(str(self.root), json.dumps(state))
        for name, original in self.signals.items():
            self.assertEqual((self.root / name).read_bytes(), original)

    def test_decrypt_returns_pngs_exact_comparisons_and_encrypted_prediction_outputs(self):
        batch = self.create_batch()
        record = self.records(batch)[0]
        before = self.artifact_files()
        response = self.decrypt(batch, record)
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()
        for key in ("original_image", "decrypted_image", "encrypted_preview"):
            self.assertTrue(base64.b64decode(result[key], validate=True).startswith(PNG_MAGIC), key)
        self.assertEqual(result["original_image"], result["decrypted_image"])
        self.assertNotEqual(result["encrypted_preview"], result["decrypted_image"])
        comparison = result["comparison"]
        for key in ("bytes_identical", "images_identical", "source_unchanged"):
            self.assertIs(comparison[key], True)
        for key in ("max_absolute_error", "mse", "score_difference"):
            self.assertEqual(comparison[key], 0)
        self.assertEqual(comparison["original_sha256"], comparison["decrypted_sha256"])
        self.assertIn(comparison["original_sha256"],
                      {hashlib.sha256(value).hexdigest() for value in self.signals.values()})
        self.assertEqual(comparison["original_prediction"], comparison["decrypted_prediction"])
        self.assertEqual(result["label"], "1 NSR")
        self.assertAlmostEqual(result["confidence"], torch.sigmoid(torch.tensor(4.0)).item())
        self.assertEqual(result["top_predictions"][0]["label"], "1 NSR")
        self.assertEqual(comparison["original_prediction"]["label_name"], "Normal sinus rhythm")
        self.assertEqual(comparison["decrypted_prediction"]["top_predictions"], result["top_predictions"])
        self.assertEqual(comparison["encrypted_prediction"]["status"], "unavailable")
        self.assertEqual(result["care_information"], care_information("1 NSR"))
        self.assertIsNone(result["adversarial"])
        for key in ("encrypted_image", "encrypted_report"):
            artifact = result[key]
            payload = base64.b64decode(artifact["ciphertext"], validate=True)
            self.assertFalse(payload.startswith(PNG_MAGIC))
            path = self.root / artifact["saved_path"]
            self.assertEqual(path.read_bytes(), payload)
            self.assertTrue(path.name.endswith(".ecgenc"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        after = self.artifact_files()
        self.assertTrue(set(before).issubset(after))
        for name in set(after) - set(before):
            self.assertTrue(name.endswith(".ecgenc"), name)
        self.assertNotIn(PASSWORD, response.get_data(as_text=True))
        for name, original in self.signals.items():
            self.assertEqual((self.root / name).read_bytes(), original)

    def test_optional_attack_is_separate_from_encryption_and_care(self):
        batch = self.create_batch()
        # This checkpoint deliberately predicts NSR even for the AFIB reference.
        record = next(r for r in self.records(batch) if r["label"] == "4 AFIB")
        response = self.decrypt(batch, record, include_attack=True, attack_epsilon=0)
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()
        attack = result["adversarial"]
        self.assertEqual(attack["method"], "FGSM")
        self.assertEqual(attack["clean_prediction"], attack["attacked_prediction"])
        self.assertFalse(attack["label_changed"])
        self.assertFalse(attack["clean_correct"])
        self.assertFalse(attack["attack_success"])
        self.assertEqual(attack["max_absolute_perturbation"], 0)
        self.assertTrue(base64.b64decode(attack["image"]).startswith(PNG_MAGIC))
        self.assertEqual(result["care_information"], care_information(result["label"]))
        restored = decrypt_record(self.root, batch["batch_id"], record["record_id"], PASSWORD)
        encrypted_image = base64.b64decode(attack["encrypted_image"]["ciphertext"])
        self.assertTrue(attack["encrypted_image"]["filename"].endswith(".png.ecgenc"))
        self.assertEqual(decrypt_bytes(encrypted_image, restored["key"]), base64.b64decode(attack["image"]))
        saved_report = json.loads(decrypt_bytes(base64.b64decode(result["encrypted_report"]["ciphertext"]),
                                               restored["key"]))
        self.assertEqual(saved_report["care_information"], result["care_information"])
        self.assertEqual(saved_report["adversarial"]["method"], "FGSM")
        self.assertNotIn("image", saved_report["adversarial"])
        self.assertTrue(result["comparison"]["bytes_identical"])
        self.assertEqual(result["comparison"]["mse"], 0)

    def test_attack_request_validation_and_password_gate(self):
        batch = self.create_batch()
        record = self.records(batch)[0]
        for epsilon in (-1, 2, "0.05", True, None):
            with self.subTest(epsilon=epsilon):
                self.assert_error(self.decrypt(batch, record, include_attack=True, attack_epsilon=epsilon))
        self.assert_error(self.decrypt(batch, record, include_attack="yes"))
        before = self.artifact_files()
        with patch("ecg_cvd.gui.evaluate_single_attack", side_effect=AssertionError("Attack before authentication")):
            self.assert_error(self.decrypt(batch, record, "wrong", include_attack=True))
        self.assertEqual(self.artifact_files(), before)

    def test_wrong_or_missing_password_does_not_load_model_or_create_outputs(self):
        batch = self.create_batch()
        record = self.records(batch)[0]
        before = self.artifact_files()
        with patch("ecg_cvd.gui.torch.load", side_effect=AssertionError("Model loaded before authentication")):
            for password in ("wrong", "", None):
                with self.subTest(password_type=type(password).__name__):
                    self.assert_error(self.decrypt(batch, record, password))
            self.assert_error(self.client.post("/api/dataset/decrypt", json={
                "batch_id": batch["batch_id"], "record_id": record["record_id"], "model_id": self.model_id}))
        self.assertEqual(self.artifact_files(), before)

    def test_tampered_mat_fails_before_model_loading(self):
        batch = self.create_batch()
        record = self.records(batch)[0]
        folder = self.root / "artifacts_dataset" / batch["batch_id"]
        files = list(folder.rglob("*.mat.ecgenc"))
        self.assertEqual(len(files), 2)
        # Corrupt both candidates so this remains independent of record filename conventions.
        for path in files:
            payload = path.read_bytes()
            path.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
        before = self.artifact_files()
        with patch("ecg_cvd.gui.torch.load", side_effect=AssertionError("Model loaded for tampered data")):
            self.assert_error(self.decrypt(batch, record))
        self.assertEqual(self.artifact_files(), before)

    def test_download_contains_only_ciphertext_and_public_batch_metadata(self):
        batch = self.create_batch()
        response = self.client.get("/api/dataset/download", query_string={"batch_id": batch["batch_id"]})
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        with ZipFile(BytesIO(response.data)) as archive:
            names = archive.namelist()
            self.assertTrue(names)
            self.assertEqual(len([name for name in names if name.endswith(".mat.ecgenc")]), 2)
            self.assertEqual(len([name for name in names if name.endswith(".png.ecgenc")]), 2)
            for name in names:
                self.assertNotIn("..", Path(name).parts)
                self.assertFalse(Path(name).is_absolute())
                self.assertTrue(name.endswith(".ecgenc") or Path(name).name in {"index.json", "key.ecgkey"}, name)
                payload = archive.read(name)
                self.assertFalse(payload.startswith(PNG_MAGIC), name)
                self.assertNotIn(PASSWORD.encode(), payload, name)
                self.assertNotIn(payload, self.signals.values(), name)

    def test_invalid_batch_and_record_paths_are_rejected(self):
        batch = self.create_batch()
        record = self.records(batch)[0]
        for invalid in ("../", "../../outside", "/etc/passwd", "f" * 32, "", None):
            with self.subTest(identifier=invalid):
                for endpoint in ("records", "download"):
                    self.assert_error(self.client.get(f"/api/dataset/{endpoint}",
                                                     query_string={"batch_id": invalid}))
                self.assert_error(self.decrypt({"batch_id": invalid}, record))
                self.assert_error(self.decrypt(batch, {"record_id": invalid}))

    def test_cross_origin_encrypt_and_decrypt_requests_are_rejected(self):
        for endpoint in ("encrypt", "decrypt"):
            for headers in ({"Origin": "https://attacker.example"}, {"Sec-Fetch-Site": "cross-site"}):
                with self.subTest(endpoint=endpoint, headers=headers):
                    self.assert_error(self.client.post(f"/api/dataset/{endpoint}", json={}, headers=headers), 403)
        self.assertEqual(self.artifact_files(), {})


if __name__ == "__main__":
    unittest.main()
