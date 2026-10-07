"""Focused tests for doctor-approved, scoped ECG visual access grants."""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import uuid

import numpy as np
from PIL import Image
from scipy.io import savemat
from sqlalchemy import select
import torch

os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/ecg-visual-access-test-mpl")

from ecg_cvd.clinical.app import create_app
from ecg_cvd.clinical.db import get_session
from ecg_cvd.clinical.models import AuditEvent, ECGVisualAccessGrant, Notification
from ecg_cvd.clinical.seed import seed_development_data
from ecg_cvd.model import RAMNV2


PASSWORD = "AResearchOnlyTestPassword!"


def waveform_mat() -> bytes:
    stream = BytesIO()
    savemat(stream, {"val": np.sin(np.arange(3600, dtype=np.float32) / 17)})
    return stream.getvalue()


def ecg_jpeg() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (96, 48), color=(240, 248, 255)).save(stream, format="JPEG", quality=90)
    return stream.getvalue()


class VisualAccessGrantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory(prefix="ecg-visual-access-tests-")
        cls.root = Path(cls.temp.name)
        cls.model_path = cls.root / "artifacts_final" / "model.pt"
        cls.model_path.parent.mkdir(parents=True)
        model = RAMNV2(2)
        with torch.no_grad():
            model.classifier.weight.zero_()
            model.classifier.bias.copy_(torch.tensor([-2.0, 2.0]))
        torch.save({"state_dict": model.state_dict(), "num_classes": 2,
                    "label_map": {"1 NSR": 1, "4 AFIB": 0}}, cls.model_path)
        cls.app = create_app({
            "PROJECT_ROOT": str(cls.root), "ENVIRONMENT": "test",
            "DATABASE_URL": f"sqlite+pysqlite:///{cls.root / 'clinical.db'}",
            "JWT_SECRET": "x" * 48, "SECRET_KEY": "y" * 48,
            "AI_MODEL_PATH": str(cls.model_path), "AI_MODEL_VERSION": "test-v1",
            "LOCAL_OBJECT_STORAGE_PATH": str(cls.root / "private_objects"), "AUTO_CREATE_SCHEMA": True,
            "VISUAL_ACCESS_MAX_ATTEMPTS": 3,
            "VISUAL_ACCESS_PASSCODE_MINUTES": 15,
            "VISUAL_ACCESS_SESSION_MINUTES": 10,
        })
        seed_development_data(cls.app, PASSWORD)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.app.extensions["ecg_platform_limiter"].reset()

    def client_for(self, email: str):
        client = self.app.test_client()
        response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.get_json())
        return client, {"Authorization": f"Bearer {response.get_json()['access_token']}"}

    def create_ecg(self) -> tuple[str, str]:
        doctor, headers = self.client_for("doctor@example.test")
        suffix = uuid.uuid4().hex[:8].upper()
        patient = doctor.post("/api/v1/patients", headers=headers, json={
            "first_name": "Visual", "last_name": suffix, "date_of_birth": "1988-03-04",
            "mrn": f"VISUAL-{suffix}",
        })
        self.assertEqual(patient.status_code, 201, patient.get_json())
        encounter = doctor.post("/api/v1/encounters", headers=headers, json={
            "patient_id": patient.get_json()["id"], "encounter_type": "OUTPATIENT",
        })
        self.assertEqual(encounter.status_code, 201, encounter.get_json())
        uploaded = doctor.post("/api/v1/ecgs", headers=headers, data={
            "patient_id": patient.get_json()["id"], "encounter_id": encounter.get_json()["id"],
            "file": (BytesIO(waveform_mat()), "visual-access.mat"),
        })
        self.assertEqual(uploaded.status_code, 201, uploaded.get_json())
        return patient.get_json()["id"], uploaded.get_json()["id"]

    def create_jpeg_ecg(self) -> tuple[str, bytes]:
        doctor, headers = self.client_for("doctor@example.test")
        suffix = uuid.uuid4().hex[:8].upper()
        patient = doctor.post("/api/v1/patients", headers=headers, json={
            "first_name": "Image", "last_name": suffix, "date_of_birth": "1988-03-04",
            "mrn": f"IMAGE-{suffix}",
        })
        self.assertEqual(patient.status_code, 201, patient.get_json())
        encounter = doctor.post("/api/v1/encounters", headers=headers, json={
            "patient_id": patient.get_json()["id"], "encounter_type": "OUTPATIENT",
        })
        self.assertEqual(encounter.status_code, 201, encounter.get_json())
        source = ecg_jpeg()
        uploaded = doctor.post("/api/v1/ecgs", headers=headers, data={
            "patient_id": patient.get_json()["id"], "encounter_id": encounter.get_json()["id"],
            "file": (BytesIO(source), "visual-access.jpg"),
        })
        self.assertEqual(uploaded.status_code, 201, uploaded.get_json())
        return uploaded.get_json()["id"], source

    def test_doctor_approved_passcode_grant_is_scoped_visual_only_and_revocable(self):
        patient_id, ecg_id = self.create_ecg()
        technician, technician_headers = self.client_for("technician@example.test")
        doctor, doctor_headers = self.client_for("doctor@example.test")
        hospital_admin, hospital_admin_headers = self.client_for("hospitaladmin@example.test")

        preflight = technician.options(f"/api/v1/ecgs/{ecg_id}/visual-image", headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization, X-ECG-Visual-Grant",
        })
        self.assertEqual(preflight.status_code, 204)
        self.assertIn("X-ECG-Visual-Grant", preflight.headers.get("Access-Control-Allow-Headers", ""))

        # The technician can see only the protected preview until a doctor
        # approves a request. A wildcard administrator gets no direct bypass.
        denied = technician.get(f"/api/v1/ecgs/{ecg_id}/waveform", headers=technician_headers)
        self.assertEqual(denied.status_code, 403, denied.get_json())
        superadmin, superadmin_headers = self.client_for("superadmin@example.test")
        self.assertEqual(superadmin.get(f"/api/v1/ecgs/{ecg_id}/waveform", headers=superadmin_headers).status_code, 403)

        requested = technician.post(f"/api/v1/ecgs/{ecg_id}/visual-access-requests", headers=technician_headers)
        self.assertEqual(requested.status_code, 201, requested.get_json())
        request_id = requested.get_json()["request_id"]
        self.assertEqual(requested.get_json()["status"], "PENDING")
        status = technician.get(f"/api/v1/ecgs/{ecg_id}/visual-access-grant", headers=technician_headers)
        self.assertEqual(status.status_code, 200, status.get_json())
        self.assertEqual(status.get_json()["grant"]["request_id"], request_id)

        # SUPER_ADMIN's wildcard is deliberately not a clinical-doctor role.
        self.assertEqual(
            superadmin.post(f"/api/v1/ecgs/visual-access-requests/{request_id}/approve",
                            headers=superadmin_headers).status_code,
            403,
        )

        inbox = doctor.get("/api/v1/ecgs/visual-access-requests?status=PENDING", headers=doctor_headers)
        self.assertEqual(inbox.status_code, 200, inbox.get_json())
        self.assertIn(request_id, [item["request_id"] for item in inbox.get_json()["items"]])

        approved = doctor.post(f"/api/v1/ecgs/visual-access-requests/{request_id}/approve", headers=doctor_headers)
        self.assertEqual(approved.status_code, 200, approved.get_json())
        passcode = approved.get_json()["passcode"]
        self.assertGreaterEqual(len(passcode), 12)
        self.assertTrue(approved.get_json()["passcode_returned_once"])

        with self.app.app_context():
            grant = get_session().get(ECGVisualAccessGrant, uuid.UUID(request_id))
            self.assertIsNotNone(grant)
            self.assertEqual(grant.status, "APPROVED")
            self.assertIsNotNone(grant.secret_envelope)
            self.assertNotIn(passcode.encode("utf-8"), bytes(grant.secret_envelope))
            notes = get_session().scalars(select(Notification).where(
                Notification.kind == "ECG_VISUAL_ACCESS_REQUEST"
            )).all()
            self.assertTrue(notes)
            audit_rows = get_session().scalars(select(AuditEvent).where(
                AuditEvent.resource_id == request_id
            )).all()
            self.assertTrue(audit_rows)
            self.assertTrue(all(passcode not in str(row.metadata_json) for row in audit_rows))

        wrong = technician.post(
            f"/api/v1/ecgs/{ecg_id}/visual-access-requests/{request_id}/unlock",
            headers=technician_headers, json={"passcode": "wrong-passcode"},
        )
        self.assertEqual(wrong.status_code, 401, wrong.get_json())

        unlocked = technician.post(
            f"/api/v1/ecgs/{ecg_id}/visual-access-requests/{request_id}/unlock",
            headers=technician_headers, json={"passcode": passcode},
        )
        self.assertEqual(unlocked.status_code, 200, unlocked.get_json())
        grant_header = {**technician_headers, "X-ECG-Visual-Grant": unlocked.get_json()["access_token"]}
        waveform = technician.get(f"/api/v1/ecgs/{ecg_id}/waveform", headers=grant_header)
        self.assertEqual(waveform.status_code, 200, waveform.get_json())
        self.assertEqual(len(waveform.get_json()["samples"]), 3600)
        visual = technician.get(f"/api/v1/ecgs/{ecg_id}/visual-image", headers=grant_header)
        self.assertEqual(visual.status_code, 200)
        self.assertEqual(visual.mimetype, "image/png")
        self.assertEqual(visual.headers.get("Content-Disposition"), "inline")
        with self.app.app_context():
            grant = get_session().get(ECGVisualAccessGrant, uuid.UUID(request_id))
            self.assertEqual(grant.status, "UNLOCKED")
            self.assertIsNone(grant.secret_envelope)
            self.assertIsNotNone(grant.unlock_token_jti_hash)

        # The one-time passcode is consumed after the token is issued.
        reused = technician.post(
            f"/api/v1/ecgs/{ecg_id}/visual-access-requests/{request_id}/unlock",
            headers=technician_headers, json={"passcode": passcode},
        )
        self.assertEqual(reused.status_code, 409, reused.get_json())

        # A grant is bound to the requester, not merely its tenant or ECG ID.
        other_user = hospital_admin.get(f"/api/v1/ecgs/{ecg_id}/waveform", headers={
            **hospital_admin_headers, "X-ECG-Visual-Grant": unlocked.get_json()["access_token"],
        })
        self.assertEqual(other_user.status_code, 403, other_user.get_json())

        # Nor can the same browser token be replayed against another ECG.
        _other_patient_id, other_ecg_id = self.create_ecg()
        wrong_ecg = technician.get(f"/api/v1/ecgs/{other_ecg_id}/waveform", headers=grant_header)
        self.assertEqual(wrong_ecg.status_code, 403, wrong_ecg.get_json())

        # A visual grant never opens source-file downloads or signed URLs.
        self.assertEqual(technician.get(f"/api/v1/ecgs/{ecg_id}/file", headers=grant_header).status_code, 403)
        self.assertEqual(technician.get(f"/api/v1/ecgs/{ecg_id}/download-url", headers=grant_header).status_code, 403)

        # Build a real waveform-bearing PDF through the doctor workflow, then
        # show that the grant header cannot bypass its literal-DOCTOR route.
        analysis = doctor.post(f"/api/v1/ecgs/{ecg_id}/analyze", headers=doctor_headers)
        self.assertEqual(analysis.status_code, 201, analysis.get_json())
        review = doctor.post(f"/api/v1/ecgs/{ecg_id}/review", headers=doctor_headers, json={
            "doctor_assessment": "Independent clinician review for visual-access grant test.",
        })
        self.assertEqual(review.status_code, 201, review.get_json())
        reports = doctor.get(f"/api/v1/reports?patient_id={patient_id}", headers=doctor_headers)
        self.assertEqual(reports.status_code, 200, reports.get_json())
        report_id = reports.get_json()[0]["id"]
        self.assertEqual(doctor.post(f"/api/v1/reports/{report_id}/generate-pdf", headers=doctor_headers).status_code, 201)
        self.assertEqual(technician.get(f"/api/v1/reports/{report_id}/download", headers=grant_header).status_code, 403)

        revoked = doctor.post(f"/api/v1/ecgs/visual-access-requests/{request_id}/revoke", headers=doctor_headers)
        self.assertEqual(revoked.status_code, 200, revoked.get_json())
        self.assertEqual(revoked.get_json()["status"], "REVOKED")
        after_revoke = technician.get(f"/api/v1/ecgs/{ecg_id}/waveform", headers=grant_header)
        self.assertEqual(after_revoke.status_code, 403, after_revoke.get_json())

    def test_attempt_cap_locks_passcode_and_prevents_reuse(self):
        _patient_id, ecg_id = self.create_ecg()
        technician, technician_headers = self.client_for("technician@example.test")
        doctor, doctor_headers = self.client_for("doctor@example.test")
        requested = technician.post(f"/api/v1/ecgs/{ecg_id}/visual-access-requests", headers=technician_headers)
        request_id = requested.get_json()["request_id"]
        approved = doctor.post(f"/api/v1/ecgs/visual-access-requests/{request_id}/approve", headers=doctor_headers)
        passcode = approved.get_json()["passcode"]

        for _ in range(3):
            wrong = technician.post(
                f"/api/v1/ecgs/{ecg_id}/visual-access-requests/{request_id}/unlock",
                headers=technician_headers, json={"passcode": "invalid"},
            )
            self.assertEqual(wrong.status_code, 401, wrong.get_json())
        unavailable = technician.post(
            f"/api/v1/ecgs/{ecg_id}/visual-access-requests/{request_id}/unlock",
            headers=technician_headers, json={"passcode": passcode},
        )
        self.assertEqual(unavailable.status_code, 409, unavailable.get_json())
        with self.app.app_context():
            grant = get_session().get(ECGVisualAccessGrant, uuid.UUID(request_id))
            self.assertEqual(grant.status, "LOCKED")
            self.assertIsNone(grant.secret_envelope)

    def test_grant_uses_inline_jpeg_route_without_file_download_capability(self):
        ecg_id, source = self.create_jpeg_ecg()
        technician, technician_headers = self.client_for("technician@example.test")
        doctor, doctor_headers = self.client_for("doctor@example.test")
        request_id = technician.post(
            f"/api/v1/ecgs/{ecg_id}/visual-access-requests", headers=technician_headers,
        ).get_json()["request_id"]
        passcode = doctor.post(
            f"/api/v1/ecgs/visual-access-requests/{request_id}/approve", headers=doctor_headers,
        ).get_json()["passcode"]
        unlock = technician.post(
            f"/api/v1/ecgs/{ecg_id}/visual-access-requests/{request_id}/unlock",
            headers=technician_headers, json={"passcode": passcode},
        )
        self.assertEqual(unlock.status_code, 200, unlock.get_json())
        headers = {**technician_headers, "X-ECG-Visual-Grant": unlock.get_json()["access_token"]}
        inline = technician.get(f"/api/v1/ecgs/{ecg_id}/visual-image", headers=headers)
        self.assertEqual(inline.status_code, 200)
        self.assertEqual(inline.mimetype, "image/jpeg")
        self.assertEqual(inline.headers.get("Content-Disposition"), "inline")
        self.assertEqual(inline.data, source)
        self.assertEqual(technician.get(f"/api/v1/ecgs/{ecg_id}/file", headers=headers).status_code, 403)


if __name__ == "__main__":
    unittest.main()
