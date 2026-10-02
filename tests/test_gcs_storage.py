import sys
import types
import unittest
from unittest.mock import patch

from ecg_cvd.clinical.storage import GCSPrivateStorage


class FakeBlob:
    def __init__(self):
        self.data = None
        self.upload_args = None

    def upload_from_string(self, data, **kwargs):
        if self.data is not None and kwargs.get("if_generation_match") == 0:
            raise RuntimeError("generation precondition failed")
        self.data = data
        self.upload_args = kwargs

    def download_as_bytes(self):
        return self.data

    def exists(self):
        return self.data is not None


class FakeBucket:
    def __init__(self):
        self.objects = {}

    def blob(self, key):
        return self.objects.setdefault(key, FakeBlob())


class GCSStorageTests(unittest.TestCase):
    def test_private_asset_round_trip_and_create_only_upload(self):
        storage = object.__new__(GCSPrivateStorage)
        storage.bucket_name = "private-assets"
        storage.bucket = FakeBucket()
        exception_module = types.ModuleType("google.api_core.exceptions")
        exception_module.PreconditionFailed = RuntimeError
        with patch.dict(sys.modules, {"google.api_core.exceptions": exception_module}):
            result = storage.put_bytes("ecgs/one", b"ecg", "application/octet-stream")
        self.assertEqual(result.uri, "gs://private-assets/ecgs/one")
        self.assertEqual(storage.get_bytes("ecgs/one"), b"ecg")
        self.assertEqual(storage.bucket.blob("ecgs/one").upload_args["if_generation_match"], 0)
        self.assertIsNone(storage.presigned_get("ecgs/one"))
        self.assertTrue(storage.protection_status("ecgs/one").encrypted_at_rest)
        with patch.dict(sys.modules, {"google.api_core.exceptions": exception_module}):
            with self.assertRaisesRegex(ValueError, "overwrite"):
                storage.put_bytes("ecgs/one", b"changed", "application/octet-stream")
        self.assertEqual(storage.get_bytes("ecgs/one"), b"ecg")
        with self.assertRaises(ValueError):
            storage.get_bytes("../outside")


if __name__ == "__main__":
    unittest.main()
