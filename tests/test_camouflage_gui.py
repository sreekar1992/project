"""Camouflage UI endpoints must keep recovery authenticated and exact."""
import base64
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import numpy as np
from PIL import Image
from scipy.io import loadmat, savemat
import torch

from ecg_cvd.crypto import MAGIC, NONCE_BYTES, TAG_BYTES
from ecg_cvd.gui import create_app
from ecg_cvd.model import RAMNV2


class CamouflageGUIAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_threads = torch.get_num_threads()
        torch.set_num_threads(2)
        model = RAMNV2(2)
        with torch.no_grad():
            model.classifier.weight.zero_()
            model.classifier.bias.copy_(torch.tensor([2.0, -2.0]))
        stream = BytesIO()
        torch.save({"state_dict": model.state_dict(), "num_classes": 2,
                    "label_map": {"1 NSR": 0, "2 APB": 1}}, stream)
        cls.checkpoint = stream.getvalue()
        stream = BytesIO()
        savemat(stream, {"val": np.sin(np.arange(3600, dtype=np.float32) / 17)})
        cls.original = stream.getvalue()

    @classmethod
    def tearDownClass(cls):
        torch.set_num_threads(cls.previous_threads)

    def setUp(self):
        self.temp = TemporaryDirectory(prefix="ecg-camouflage-api-")
        self.root = Path(self.temp.name)
        self.source = self.root / "MLII/1 NSR/sample.mat"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(self.original)
        self.model_id = "artifacts_final/model.pt"
        model_path = self.root / self.model_id
        model_path.parent.mkdir()
        model_path.write_bytes(self.checkpoint)
        self.app = create_app(self.root)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["ecg_executor"].shutdown(wait=True)
        self.temp.cleanup()

    def create_batch(self, mode="camouflage"):
        result = self.client.post("/api/dataset/encrypt", json={"mode": mode, "model_id": self.model_id,
                                                               "camouflage_epsilon": 0.5,
                                                               "camouflage_steps": 2})
        self.assertEqual(result.status_code, 202, result.get_json())
        identifier = result.get_json()["job_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = self.client.get("/api/jobs/" + identifier).get_json()
            if job["status"] == "done":
                batch = job["result"]
                record = self.client.get("/api/dataset/records", query_string={"batch_id": batch["batch_id"]}).get_json()["records"][0]
                return batch, record
            if job["status"] == "error":
                self.fail(job["error"])
            time.sleep(0.02)
        self.fail("One-record camouflage job timed out")

    def params(self, batch, record):
        return {"batch_id": batch["batch_id"], "record_id": record["record_id"]}

    def unlock(self, batch, record, password="987654321"):
        return self.client.post("/api/dataset/decrypt", json={**self.params(batch, record),
                                                             "password": password, "model_id": self.model_id})

    def test_public_decoy_preview_and_exact_password_recovery(self):
        batch, record = self.create_batch()
        self.assertEqual(batch["mode"], "camouflage")
        self.assertEqual(batch["camouflaged_count"], 1)
        self.assertEqual(batch["label_changed_count"], 0)  # constant classifier: no fabricated success
        preview = self.client.get("/api/dataset/preview", query_string=self.params(batch, record))
        self.assertEqual(preview.status_code, 200, preview.get_json())
        public = preview.get_json()["camouflage"]
        self.assertFalse(public["metadata_authenticated"])
        self.assertEqual(public["prediction"]["label"], "1 NSR")
        self.assertNotIn("original", public)
        self.assertNotIn("restored_mat", preview.get_json())
        self.assertTrue(base64.b64decode(preview.get_json()["encrypted_preview"]).startswith(b"\x89PNG\r\n\x1a\n"))
        response = self.unlock(batch, record)
        self.assertEqual(response.status_code, 200, response.get_json())
        data = response.get_json()
        self.assertEqual(base64.b64decode(data["restored_mat"]["plaintext"]), self.original)
        self.assertTrue(data["camouflage"]["recovery_bytes_identical"])
        self.assertEqual(data["camouflage"]["original_sha256"], data["camouflage"]["restored_sha256"])
        self.assertFalse(data["camouflage"]["label_changed"])
        self.assertEqual(data["comparison"]["mse"], 0)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertNotIn("key", data["camouflage"])
        self.assertEqual(data["encrypted_preview"], preview.get_json()["encrypted_preview"])

    def test_locked_noise_preview_uses_ciphertext_without_keys_sources_or_model(self):
        for mode in ("aes", "camouflage"):
            with self.subTest(mode=mode):
                self.source.write_bytes(self.original)
                batch, record = self.create_batch(mode=mode)
                folder = self.root / "artifacts_dataset" / batch["batch_id"]
                ciphertext = (folder / (record["record_id"] + ".png.ecgenc")).read_bytes()
                self.source.unlink()
                (folder / "key.ecgkey").unlink()
                (folder / "manifest.json.ecgenc").unlink()
                with patch("ecg_cvd.gui.torch.load", side_effect=AssertionError("No model before authentication")), \
                     patch("ecg_cvd.batch_crypto.decrypt_bytes", side_effect=AssertionError("No decryption for preview")), \
                     patch("ecg_cvd.batch_crypto._unwrap_key", side_effect=AssertionError("No recovery key for preview")):
                    response = self.client.get("/api/dataset/preview", query_string=self.params(batch, record))
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                public = response.get_json()
                self.assertEqual(set(public), {"camouflage", "encrypted_preview", "encrypted_preview_note"})
                image_bytes = base64.b64decode(public["encrypted_preview"])
                with Image.open(BytesIO(image_bytes)) as image:
                    actual = np.asarray(image)
                    self.assertEqual(image.mode, "L")
                self.assertLessEqual(actual.shape[0], 256)
                values = np.frombuffer(ciphertext[len(MAGIC) + NONCE_BYTES:-TAG_BYTES], dtype=np.uint8)
                np.testing.assert_array_equal(actual.reshape(-1), values[:actual.size])
                self.assertNotIn("original_image", public)
                self.assertNotIn("restored_mat", public)
                self.assertNotIn("key", public)
                self.assertNotEqual(image_bytes, self.original)
                if mode == "aes":
                    self.assertIsNone(public["camouflage"])

    def test_locked_noise_preview_rejects_invalid_ids_membership_and_unsafe_files(self):
        batch, record = self.create_batch(mode="aes")
        params = self.params(batch, record)
        for changes in ({"batch_id": "../"}, {"record_id": "../../outside"},
                        {"batch_id": "f" * 32}, {"record_id": "f" * 32}):
            with self.subTest(changes=changes):
                response = self.client.get("/api/dataset/preview", query_string={**params, **changes})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(set(response.get_json()), {"error"})
        folder = self.root / "artifacts_dataset" / batch["batch_id"]
        target = folder / (record["record_id"] + ".png.ecgenc")
        payload = target.read_bytes()
        target.unlink()
        target.symlink_to(self.source)
        response = self.client.get("/api/dataset/preview", query_string=params)
        self.assertEqual(response.status_code, 400)
        target.unlink()
        for invalid_payload in (self.original, payload[:10]):
            target.write_bytes(invalid_payload)
            response = self.client.get("/api/dataset/preview", query_string=params)
            self.assertEqual(response.status_code, 400)
        target.write_bytes(payload)
        catalog = folder / "index.json"
        index = json.loads(catalog.read_bytes())
        index["records"].append(index["records"][0])
        catalog.write_text(json.dumps(index))
        self.assertEqual(self.client.get("/api/dataset/preview", query_string=params).status_code, 400)

    def test_recovery_does_not_need_original_source_and_wrong_password_is_gated(self):
        batch, record = self.create_batch()
        self.source.unlink()
        with patch("ecg_cvd.gui.torch.load", side_effect=AssertionError("Model before authentication")):
            response = self.unlock(batch, record, "wrong")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(set(response.get_json()), {"error"})
        response = self.unlock(batch, record)
        self.assertEqual(response.status_code, 200, response.get_json())
        data = response.get_json()
        self.assertIsNone(data["original_image"])
        self.assertEqual(base64.b64decode(data["restored_mat"]["plaintext"]), self.original)

    def test_downloads_contain_intentionally_public_camouflage_not_plain_original(self):
        batch, record = self.create_batch()
        response = self.client.get("/api/dataset/camouflaged", query_string=self.params(batch, record))
        self.assertEqual(response.status_code, 200)
        arrays = loadmat(BytesIO(response.data))
        self.assertEqual(arrays["val"].size, 3600)
        self.assertNotEqual(response.data, self.original)
        self.assertIn(".camouflaged.mat", response.headers["Content-Disposition"])
        response = self.client.get("/api/dataset/download", query_string={"batch_id": batch["batch_id"]})
        with ZipFile(BytesIO(response.data)) as archive:
            self.assertEqual(len([x for x in archive.namelist() if x.endswith(".camouflaged.mat")]), 1)
            self.assertNotIn(self.original, [archive.read(x) for x in archive.namelist()])

    def test_aes_only_batches_and_invalid_settings(self):
        batch, record = self.create_batch(mode="aes")
        self.assertIsNone(self.client.get("/api/dataset/preview", query_string=self.params(batch, record)).get_json()["camouflage"])
        self.assertEqual(self.client.get("/api/dataset/camouflaged", query_string=self.params(batch, record)).status_code, 400)
        for settings in ({"mode": "bad"}, {"camouflage_epsilon": 0}, {"camouflage_epsilon": True},
                         {"camouflage_epsilon": 10 ** 400},
                         {"camouflage_steps": 0}, {"camouflage_steps": 1.5}, {"model_id": "../model.pt"}):
            response = self.client.post("/api/dataset/encrypt", json={"mode": "camouflage",
                                        "model_id": self.model_id, **settings})
            self.assertEqual(response.status_code, 400, settings)

    def test_archive_skips_unlisted_decoys_and_aes_mode_plaintext_mat(self):
        for mode in ("aes", "camouflage"):
            with self.subTest(mode=mode):
                batch, record = self.create_batch(mode=mode)
                folder = self.root / "artifacts_dataset" / batch["batch_id"]
                fake_name = (record["record_id"] if mode == "aes" else "f" * 32) + ".camouflaged.mat"
                (folder / fake_name).write_bytes(self.original)
                response = self.client.get("/api/dataset/download", query_string={"batch_id": batch["batch_id"]})
                self.assertEqual(response.status_code, 200)
                with ZipFile(BytesIO(response.data)) as archive:
                    self.assertNotIn(batch["batch_id"] + "/" + fake_name, archive.namelist())

    def test_archive_rejects_original_mat_disguised_as_known_camouflage(self):
        batch, record = self.create_batch()
        path = self.root / "artifacts_dataset" / batch["batch_id"] / (record["record_id"] + ".camouflaged.mat")
        path.write_bytes(self.original)
        response = self.client.get("/api/dataset/download", query_string={"batch_id": batch["batch_id"]})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
