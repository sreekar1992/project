"""Batch encryption must preserve source data and authenticate before returning it."""
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cryptography.exceptions import InvalidTag
import numpy as np
from scipy.io import savemat

from ecg_cvd.batch_crypto import decrypt_record, encrypt_dataset, list_batches, list_records


PASSWORD = "987654321"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class BatchCryptoTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="ecg-batch-crypto-tests-")
        self.root = Path(self.temp.name)
        self.signals = {}
        for index, label in enumerate(("1 NSR", "4 AFIB")):
            buffer = BytesIO()
            savemat(buffer, {"val": np.sin(np.arange(3600, dtype=np.float32) / (17 + index * 3))})
            path = self.root / "MLII" / label / f"{100 + index}m (0).mat"
            path.parent.mkdir(parents=True)
            path.write_bytes(buffer.getvalue())
            self.signals[path] = buffer.getvalue()

    def tearDown(self):
        self.temp.cleanup()

    def encrypt(self):
        self.progress = []
        batch = encrypt_dataset(self.root, progress=lambda *args: self.progress.append(args))
        return batch, list_records(self.root, batch["batch_id"])

    def contents(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in (self.root / "artifacts_dataset").rglob("*") if path.is_file()}

    def test_all_mats_and_generated_images_roundtrip_without_plaintext_intermediates(self):
        batch, records = self.encrypt()
        self.assertEqual(batch["count"], 2)
        self.assertEqual(batch["mat_count"], 2)
        self.assertEqual(batch["image_count"], 2)
        self.assertEqual(len(records), 2)
        self.assertTrue(self.progress)
        self.assertEqual(self.progress[-1][:2], (2, 2))
        self.assertEqual([item["batch_id"] for item in list_batches(self.root)], [batch["batch_id"]])
        ciphertext = self.contents()
        self.assertEqual(len([name for name in ciphertext if name.endswith(".mat.ecgenc")]), 2)
        self.assertEqual(len([name for name in ciphertext if name.endswith(".png.ecgenc")]), 2)
        returned = set()
        for record in records:
            restored = decrypt_record(self.root, batch["batch_id"], record["record_id"], PASSWORD)
            returned.add(restored["mat_bytes"])
            self.assertEqual(restored["mat_bytes"], restored["original_mat_bytes"])
            self.assertEqual(restored["image_bytes"], restored["original_image_bytes"])
            self.assertTrue(restored["image_bytes"].startswith(PNG_MAGIC))
            self.assertEqual(len(restored["key"]), 32)
            self.assertNotEqual(restored["image_ciphertext"], restored["image_bytes"])
            for name, payload in ciphertext.items():
                self.assertNotIn(restored["key"], payload, name)
        self.assertEqual(returned, set(self.signals.values()))
        self.assertEqual(self.contents(), ciphertext)
        for path, original in self.signals.items():
            self.assertEqual(path.read_bytes(), original)
        for name, payload in ciphertext.items():
            self.assertTrue(name.endswith(".ecgenc") or Path(name).name in {"index.json", "key.ecgkey"}, name)
            self.assertNotIn(PASSWORD.encode(), payload, name)
            self.assertFalse(payload.startswith(PNG_MAGIC), name)
            self.assertNotIn(payload, self.signals.values(), name)
            self.assertEqual((self.root / name).stat().st_mode & 0o777, 0o600)

    def test_reencrypting_creates_independent_batches_and_fresh_ciphertext(self):
        first, first_records = self.encrypt()
        second, second_records = self.encrypt()
        self.assertNotEqual(first["batch_id"], second["batch_id"])
        restored_first = decrypt_record(self.root, first["batch_id"], first_records[0]["record_id"], PASSWORD)
        restored_second = decrypt_record(self.root, second["batch_id"], second_records[0]["record_id"], PASSWORD)
        self.assertEqual(restored_first["mat_bytes"], restored_second["mat_bytes"])
        self.assertEqual(restored_first["image_bytes"], restored_second["image_bytes"])
        self.assertNotEqual(restored_first["image_ciphertext"], restored_second["image_ciphertext"])
        self.assertNotEqual(restored_first["key"], restored_second["key"])

    def test_wrong_password_never_returns_data_or_changes_files(self):
        batch, records = self.encrypt()
        before = self.contents()
        for password in ("123456789", "987654321 ", "", None):
            with self.subTest(password_type=type(password).__name__):
                with self.assertRaises((ValueError, InvalidTag)):
                    decrypt_record(self.root, batch["batch_id"], records[0]["record_id"], password)
        self.assertEqual(self.contents(), before)

    def test_tampered_mat_or_image_is_rejected(self):
        batch, records = self.encrypt()
        folder = self.root / "artifacts_dataset" / batch["batch_id"]
        for extension in (".mat.ecgenc", ".png.ecgenc"):
            originals = {path: path.read_bytes() for path in folder.rglob(f"*{extension}")}
            self.assertEqual(len(originals), 2)
            try:
                for path, payload in originals.items():
                    path.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
                with self.subTest(kind=extension), self.assertRaises((ValueError, InvalidTag)):
                    decrypt_record(self.root, batch["batch_id"], records[0]["record_id"], PASSWORD)
            finally:
                for path, payload in originals.items():
                    path.write_bytes(payload)

    def test_swapping_valid_ciphertexts_between_records_is_rejected_by_manifest(self):
        batch, records = self.encrypt()
        folder = self.root / "artifacts_dataset" / batch["batch_id"]
        files = sorted(folder.glob("*.mat.ecgenc"))
        first, second = (path.read_bytes() for path in files)
        files[0].write_bytes(second)
        files[1].write_bytes(first)
        for record in records:
            with self.subTest(record=record["record_id"]), self.assertRaises(ValueError):
                decrypt_record(self.root, batch["batch_id"], record["record_id"], PASSWORD)

    def test_tampered_manifest_or_wrapped_key_is_rejected(self):
        batch, records = self.encrypt()
        folder = self.root / "artifacts_dataset" / batch["batch_id"]
        for name in ("manifest.json.ecgenc", "key.ecgkey"):
            path = folder / name
            original = path.read_bytes()
            try:
                path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
                with self.subTest(file=name), self.assertRaises((ValueError, InvalidTag)):
                    decrypt_record(self.root, batch["batch_id"], records[0]["record_id"], PASSWORD)
            finally:
                path.write_bytes(original)

    def test_unauthenticated_catalog_labels_do_not_override_authenticated_metadata(self):
        batch, records = self.encrypt()
        path = self.root / "artifacts_dataset" / batch["batch_id"] / "index.json"
        index = json.loads(path.read_bytes())
        index["records"][0]["name"] = "forged.mat"
        index["records"][0]["label"] = "forged label"
        path.write_text(json.dumps(index))
        restored = decrypt_record(self.root, batch["batch_id"], records[0]["record_id"], PASSWORD)
        self.assertEqual(restored["metadata"]["name"], records[0]["name"])
        self.assertEqual(restored["metadata"]["label"], records[0]["label"])

    def test_missing_original_does_not_prevent_decryption_or_invent_comparison(self):
        batch, records = self.encrypt()
        for path in self.signals:
            path.unlink()
        restored = decrypt_record(self.root, batch["batch_id"], records[0]["record_id"], PASSWORD)
        self.assertIn(restored["mat_bytes"], self.signals.values())
        self.assertTrue(restored["image_bytes"].startswith(PNG_MAGIC))
        self.assertIsNone(restored["original_mat_bytes"])
        self.assertIsNone(restored["original_image_bytes"])
        self.assertIsNone(restored["metadata"]["byte_equal"])
        self.assertIs(restored["metadata"]["original_available"], False)

    def test_invalid_identifiers_cannot_escape_storage(self):
        batch, records = self.encrypt()
        for invalid in ("../", "../../outside", "/etc/passwd", "f" * 32, "", None):
            with self.subTest(identifier=invalid):
                with self.assertRaises(ValueError):
                    list_records(self.root, invalid)
                with self.assertRaises(ValueError):
                    decrypt_record(self.root, invalid, records[0]["record_id"], PASSWORD)
                with self.assertRaises(ValueError):
                    decrypt_record(self.root, batch["batch_id"], invalid, PASSWORD)

    def test_empty_dataset_is_rejected(self):
        with TemporaryDirectory(prefix="ecg-empty-batch-tests-") as temporary:
            root = Path(temporary)
            self.assertEqual(list_batches(root), [])
            with self.assertRaises(ValueError):
                encrypt_dataset(root)

    def test_external_dataset_symlink_is_not_encrypted(self):
        with TemporaryDirectory(prefix="ecg-outside-batch-tests-") as temporary:
            path = Path(temporary) / "outside.mat"
            path.write_bytes(next(iter(self.signals.values())))
            (self.root / "MLII" / "1 NSR" / "outside.mat").symlink_to(path)
            # Refusal or exclusion is safe; importing an external source is not.
            try:
                batch, records = self.encrypt()
            except ValueError:
                pass
            else:
                self.assertEqual(batch["count"], 2)
                self.assertEqual(len(records), 2)
            self.assertEqual(path.read_bytes(), next(iter(self.signals.values())))


if __name__ == "__main__":
    unittest.main()
