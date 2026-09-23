import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ecg_cvd.gui import create_app


class DeploymentConfigurationTests(unittest.TestCase):
    def test_health_and_readiness_are_configurable(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {
            "ECG_TRUSTED_HOSTS": "ecg-workbench",
            "ECG_REQUIRE_MODEL_FOR_READY": "true",
            "ECG_ENABLE_BACKGROUND_JOBS": "false",
            "ECG_ENABLE_TRAINING": "false",
        }, clear=False):
            app = create_app(Path(directory))
            client = app.test_client()
            headers = {"Host": "ecg-workbench"}

            self.assertEqual(client.get("/healthz", headers=headers).status_code, 200)
            self.assertEqual(client.get("/readyz", headers=headers).status_code, 503)
            state = client.get("/api/state", headers=headers).get_json()
            self.assertFalse(state["capabilities"]["background_jobs"])
            self.assertFalse(state["capabilities"]["training"])

            response = client.post("/api/train", json={}, headers=headers)
            self.assertEqual(response.status_code, 400)
            self.assertIn("disabled", response.get_json()["error"])

            response = client.post("/api/dataset/encrypt", json={}, headers=headers)
            self.assertEqual(response.status_code, 400)
            self.assertIn("disabled", response.get_json()["error"])
