"""Readable decoy signals must not weaken authenticated, byte-exact recovery."""
from io import BytesIO
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import numpy as np
from scipy.io import loadmat, savemat

from ecg_cvd.batch_crypto import (camouflage_preview, decrypt_record, encrypt_dataset,
                                  list_batches, list_records)
from ecg_cvd.camouflage_container import (MAX_CONTAINER_BYTES, create_camouflaged_mat,
                                         inspect_camouflaged_mat, restore_camouflaged_mat)


def mat_bytes(signal):
    stream = BytesIO()
    savemat(stream, {"val": signal.reshape(1, -1), "original_metadata": np.array([[42]])})
    return stream.getvalue()


def rewrite_container(payload, mutation):
    fields = {name: value for name, value in loadmat(BytesIO(payload)).items() if not name.startswith("__")}
    mutation(fields)
    stream = BytesIO()
    savemat(stream, fields)
    return stream.getvalue()


class CamouflageContainerTests(unittest.TestCase):
    def setUp(self):
        self.signal = np.sin(np.arange(3600, dtype=np.float32) / 17)
        self.decoy = self.signal + np.float32(0.2) * np.cos(np.arange(3600, dtype=np.float32))
        self.original = mat_bytes(self.signal)
        self.key = AESGCM.generate_key(bit_length=256)
        self.context = "a" * 32 + ":" + "b" * 32
        self.payload = create_camouflaged_mat(self.original, self.decoy, self.key, self.context)

    def test_readable_changed_signal_and_exact_original_byte_recovery(self):
        plain_mat = loadmat(BytesIO(self.payload))
        np.testing.assert_array_equal(plain_mat["val"].reshape(-1), self.decoy)
        self.assertFalse(np.array_equal(plain_mat["val"].reshape(-1), self.signal))
        self.assertNotIn(self.original, self.payload)
        inspected = inspect_camouflaged_mat(self.payload, self.context)
        np.testing.assert_array_equal(inspected["signal"], self.decoy)
        self.assertFalse(inspected["authenticated"])
        self.assertEqual(restore_camouflaged_mat(self.payload, self.key, self.context), self.original)

    def test_new_nonce_every_time(self):
        second = create_camouflaged_mat(self.original, self.decoy, self.key, self.context)
        self.assertNotEqual(self.payload, second)
        self.assertNotEqual(inspect_camouflaged_mat(self.payload, self.context)["nonce"],
                            inspect_camouflaged_mat(second, self.context)["nonce"])

    def test_modified_signal_is_rejected(self):
        modified = rewrite_container(self.payload, lambda values: values["val"].__setitem__((0, 12), 99.0))
        with self.assertRaises(InvalidTag):
            restore_camouflaged_mat(modified, self.key, self.context)

    def test_modified_payload_and_nonce_are_rejected(self):
        for name in ("recovery_payload", "recovery_nonce"):
            with self.subTest(variable=name):
                modified = rewrite_container(self.payload, lambda values: values[name].__setitem__(
                    (0, 0), int(values[name][0, 0]) ^ 1))
                with self.assertRaises(InvalidTag):
                    restore_camouflaged_mat(modified, self.key, self.context)

    def test_wrong_key_or_recording_context_is_rejected(self):
        with self.assertRaises(InvalidTag):
            restore_camouflaged_mat(self.payload, AESGCM.generate_key(bit_length=256), self.context)
        with self.assertRaises((InvalidTag, ValueError)):
            restore_camouflaged_mat(self.payload, self.key, self.context + "different")
        changed_context = "c" * 32 + ":" + "d" * 32
        modified = rewrite_container(self.payload, lambda values: values.__setitem__(
            "camouflage_context", np.frombuffer(changed_context.encode(), dtype=np.uint8).reshape(1, -1)))
        with self.assertRaises(InvalidTag):
            restore_camouflaged_mat(modified, self.key, changed_context)

    def test_unchanged_signal_can_be_stored_without_claiming_a_flip(self):
        payload = create_camouflaged_mat(self.original, self.signal, self.key, self.context)
        self.assertEqual(restore_camouflaged_mat(payload, self.key, self.context), self.original)

    def test_invalid_or_nonfinite_signal_is_rejected(self):
        for signal in (np.zeros(3601), np.zeros((1, 3600)), np.full(3600, np.nan),
                       np.full(3600, np.inf), np.ones(3600, dtype=complex), np.full(3600, "x")):
            with self.subTest(shape=signal.shape), self.assertRaises(ValueError):
                create_camouflaged_mat(self.original, signal, self.key, self.context)

    def test_malformed_oversized_and_compressed_containers_are_rejected(self):
        fields = {name: value for name, value in loadmat(BytesIO(self.payload)).items()
                  if not name.startswith("__")}
        stream = BytesIO()
        savemat(stream, fields, do_compression=True)
        for payload in (b"", b"not a MAT", self.payload[:-10], b"x" * (MAX_CONTAINER_BYTES + 1),
                        stream.getvalue()):
            with self.subTest(size=len(payload)), self.assertRaises(ValueError):
                inspect_camouflaged_mat(payload, self.context)


class CamouflageBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="ecg-camouflage-storage-")
        self.root = Path(self.temp.name)
        self.originals = {}
        for index, label in enumerate(("1 NSR", "4 AFIB")):
            signal = np.sin(np.arange(3600, dtype=np.float32) / (17 + index))
            payload = mat_bytes(signal)
            path = self.root / "MLII" / label / f"{100 + index}m (0).mat"
            path.parent.mkdir(parents=True)
            path.write_bytes(payload)
            self.originals[path.name] = payload

    def tearDown(self):
        self.temp.cleanup()

    def generator(self, payload, label):
        signal = loadmat(BytesIO(payload))["val"].reshape(-1).astype(np.float32)
        decoy = signal + 0.1 * np.cos(np.arange(3600, dtype=np.float32))
        report = {"model": "research-model", "epsilon": 0.1, "label_changed": label == "1 NSR",
                  "camouflaged_prediction": {"label": "2 APB", "confidence": 0.6},
                  "clean_prediction": {"label": label, "confidence": 0.9}}
        return decoy, report

    def encrypt(self):
        summary = encrypt_dataset(self.root, camouflage_generator=self.generator,
                                  camouflage_config={"model": "research-model", "epsilon": 0.1})
        return summary, list_records(self.root, summary["batch_id"])

    def test_batch_counts_public_preview_and_private_report(self):
        batch, records = self.encrypt()
        self.assertEqual(batch["mode"], "camouflage")
        self.assertEqual(batch["camouflaged_count"], 2)
        self.assertEqual(batch["label_changed_count"], 1)
        self.assertEqual(list_batches(self.root)[0]["mode"], "camouflage")
        folder = self.root / "artifacts_dataset" / batch["batch_id"]
        self.assertEqual(len(list(folder.glob("*.camouflaged.mat"))), 2)
        self.assertNotIn("clean_prediction", (folder / "index.json").read_text())
        for entry in records:
            preview = camouflage_preview(self.root, batch["batch_id"], entry["record_id"])
            self.assertFalse(preview["metadata_authenticated"])
            self.assertNotIn("clean_prediction", preview["report"])
            self.assertEqual(preview["report"]["camouflaged_prediction"]["label"], "2 APB")
            self.assertEqual((folder / preview["filename"]).stat().st_mode & 0o777, 0o600)
            restored = decrypt_record(self.root, batch["batch_id"], entry["record_id"], "987654321")
            self.assertEqual(restored["mat_bytes"], self.originals[entry["name"]])
            self.assertEqual(restored["camouflage"]["report"]["clean_prediction"]["label"], entry["label"])
            self.assertTrue(restored["camouflage"]["metadata_authenticated"])
            self.assertTrue(restored["metadata"]["byte_equal"])
            np.testing.assert_array_equal(preview["signal"], restored["camouflage"]["signal"])

    def test_recovery_does_not_need_source_dataset(self):
        batch, records = self.encrypt()
        shutil.rmtree(self.root / "MLII")
        for entry in records:
            restored = decrypt_record(self.root, batch["batch_id"], entry["record_id"], "987654321")
            self.assertEqual(restored["mat_bytes"], self.originals[entry["name"]])
            self.assertIsNone(restored["original_mat_bytes"])
            self.assertTrue(restored["camouflage"]["metadata_authenticated"])

    def test_forged_public_report_cannot_override_authenticated_report(self):
        batch, records = self.encrypt()
        path = self.root / "artifacts_dataset" / batch["batch_id"] / "index.json"
        index = json.loads(path.read_bytes())
        index["records"][0]["camouflage"]["camouflaged_prediction"]["label"] = "forged"
        index["records"][0]["camouflage"]["clean_prediction"] = {"label": "injected"}
        index["records"][0]["camouflage"]["filename"] = "../../outside.mat"
        path.write_text(json.dumps(index))
        preview = camouflage_preview(self.root, batch["batch_id"], records[0]["record_id"])
        self.assertEqual(preview["report"]["camouflaged_prediction"]["label"], "forged")
        self.assertFalse(preview["metadata_authenticated"])
        self.assertNotIn("clean_prediction", preview["report"])
        restored = decrypt_record(self.root, batch["batch_id"], records[0]["record_id"], "987654321")
        self.assertEqual(restored["camouflage"]["report"]["camouflaged_prediction"]["label"], "2 APB")

    def test_modified_camouflage_file_is_rejected(self):
        batch, records = self.encrypt()
        folder = self.root / "artifacts_dataset" / batch["batch_id"]
        path = folder / f"{records[0]['record_id']}.camouflaged.mat"
        path.write_bytes(rewrite_container(path.read_bytes(), lambda values: values["val"].__setitem__((0, 0), 300)))
        with self.assertRaises((ValueError, InvalidTag)):
            decrypt_record(self.root, batch["batch_id"], records[0]["record_id"], "987654321")

    def test_legacy_batches_keep_aes_mode_and_have_no_camouflage_preview(self):
        summary = encrypt_dataset(self.root)
        entry = list_records(self.root, summary["batch_id"])[0]
        folder = self.root / "artifacts_dataset" / summary["batch_id"]
        index = json.loads((folder / "index.json").read_bytes())
        for field in ("mode", "camouflaged_count", "label_changed_count", "camouflage_config"):
            index.pop(field)
        (folder / "index.json").write_text(json.dumps(index))
        self.assertEqual(list_batches(self.root)[0]["mode"], "aes")
        self.assertIsNone(camouflage_preview(self.root, summary["batch_id"], entry["record_id"]))
        restored = decrypt_record(self.root, summary["batch_id"], entry["record_id"], "987654321")
        self.assertNotIn("camouflage", restored)
        self.assertEqual(restored["mat_bytes"], self.originals[entry["name"]])

    def test_preview_rejects_traversal_and_symlink_files(self):
        batch, records = self.encrypt()
        for bad_id in ("../", "", None, "x" * 32):
            with self.subTest(identifier=bad_id), self.assertRaises(ValueError):
                camouflage_preview(self.root, batch["batch_id"], bad_id)
        folder = self.root / "artifacts_dataset" / batch["batch_id"]
        path = folder / f"{records[0]['record_id']}.camouflaged.mat"
        target = self.root / "outside.mat"
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            camouflage_preview(self.root, batch["batch_id"], records[0]["record_id"])


if __name__ == "__main__":
    unittest.main()
