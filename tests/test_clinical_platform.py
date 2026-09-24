"""End-to-end tests for the separate, research-only clinical platform API."""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import uuid

import numpy as np
from scipy.io import savemat
from sqlalchemy import select
import torch

os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/ecg-platform-test-mpl")

from ecg_cvd.clinical.app import create_app
from ecg_cvd.clinical.db import get_session
from ecg_cvd.clinical.models import (Condition, ECGRecord, PatientOrganization, Prescription, User)
from ecg_cvd.clinical.seed import seed_development_data
from ecg_cvd.model import RAMNV2


PASSWORD = "AResearchOnlyTestPassword!"


def waveform_mat() -> bytes:
    stream = BytesIO()
    savemat(stream, {"val": np.sin(np.arange(3600, dtype=np.float32) / 17)})
    return stream.getvalue()


class ClinicalPlatformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory(prefix="ecg-platform-tests-")
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
        })
        seed_development_data(cls.app, PASSWORD)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def client_for(self, email: str):
        client = self.app.test_client()
        response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.get_json())
        return client, {"Authorization": f"Bearer {response.get_json()['access_token']}"}

    def make_patient_and_ecg(self):
        client, headers = self.client_for("doctor@example.test")
        suffix = uuid.uuid4().hex[:8].upper()
        patient = client.post("/api/v1/patients", headers=headers, json={
            "first_name": "Anita", "last_name": suffix, "date_of_birth": "1988-03-04",
            "mrn": f"CITY-TEST-{suffix}",
        })
        self.assertEqual(patient.status_code, 201, patient.get_json())
        patient_id = patient.get_json()["id"]
        encounter = client.post("/api/v1/encounters", headers=headers, json={
            "patient_id": patient_id, "encounter_type": "OUTPATIENT", "reason": "research workflow test",
        })
        self.assertEqual(encounter.status_code, 201, encounter.get_json())
        upload = client.post("/api/v1/ecgs", headers=headers, data={
            "patient_id": patient_id, "encounter_id": encounter.get_json()["id"],
            "file": (BytesIO(waveform_mat()), "sample.mat"),
        })
        self.assertEqual(upload.status_code, 201, upload.get_json())
        return client, headers, patient_id, upload.get_json()["id"]

    def test_real_model_workflow_preserves_prediction_and_requires_review(self):
        client, headers, patient_id, ecg_id = self.make_patient_and_ecg()
        waveform = client.get(f"/api/v1/ecgs/{ecg_id}/waveform", headers=headers)
        self.assertEqual(waveform.status_code, 200, waveform.get_json())
        self.assertEqual(waveform.get_json()["sampling_rate_hz"], 360.0)
        self.assertEqual(len(waveform.get_json()["samples"]), 3600)
        result = client.post(f"/api/v1/ecgs/{ecg_id}/analyze", headers=headers)
        self.assertEqual(result.status_code, 201, result.get_json())
        analysis = result.get_json()
        self.assertEqual(analysis["prediction"], "1 NSR")
        self.assertEqual(analysis["model_version"], "test-v1")
        self.assertTrue(analysis["has_explanation"])
        self.assertIn("not an autonomous diagnosis", analysis["safety"])
        image = client.get(f"/api/v1/ecgs/{ecg_id}/explain.png", headers=headers)
        self.assertEqual(image.status_code, 200)
        self.assertTrue(image.data.startswith(b"\x89PNG\r\n\x1a\n"))

        review = client.post(f"/api/v1/ecgs/{ecg_id}/review", headers=headers, json={
            "doctor_assessment": "Clinician documented an independent review.",
            "diagnosis": {"display": "Clinician-entered observation"},
            "clinical_note": "Clinical note entered by the doctor.",
            "prescription_items": [{"medicine": "Clinician-selected demo medicine", "dose": "as documented"}],
        })
        self.assertEqual(review.status_code, 201, review.get_json())
        self.assertIn("clinician-authored", review.get_json()["safety"])
        reports = client.get(f"/api/v1/reports?patient_id={patient_id}", headers=headers)
        self.assertEqual(reports.status_code, 200)
        report_id = reports.get_json()[0]["id"]
        pdf = client.post(f"/api/v1/reports/{report_id}/generate-pdf", headers=headers)
        self.assertEqual(pdf.status_code, 201, pdf.get_json())
        download = client.get(f"/api/v1/reports/{report_id}/download", headers=headers)
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.data.startswith(b"%PDF"))
        patient_fhir = client.get(f"/api/v1/fhir/Patient/{patient_id}", headers=headers)
        self.assertEqual(patient_fhir.status_code, 200, patient_fhir.get_json())
        self.assertEqual(patient_fhir.get_json()["resourceType"], "Patient")

    def test_review_api_alias_retains_only_clinician_authored_clinical_fields(self):
        client, headers, _patient_id, ecg_id = self.make_patient_and_ecg()
        analysis = client.post(f"/api/v1/ecgs/{ecg_id}/analyze", headers=headers)
        self.assertEqual(analysis.status_code, 201, analysis.get_json())
        review = client.post("/api/v1/reviews", headers=headers, json={
            "analysis_id": analysis.get_json()["id"],
            "doctor_assessment": "Clinician assessment entered independently of the model.",
            "review_status": "REQUIRES_FOLLOW_UP",
            "diagnosis": {"display": "Clinician-entered finding"},
            "clinical_note": "Clinician-entered narrative note.",
            "prescription_items": [{"medicine": "Clinician-entered demo item", "dose": "as documented"}],
        })
        self.assertEqual(review.status_code, 201, review.get_json())
        self.assertEqual(review.get_json()["status"], "REQUIRES_FOLLOW_UP")
        self.assertEqual(review.get_json()["clinician_notes"], "Clinician assessment entered independently of the model.")

    def test_other_hospital_cannot_discover_patient_and_technician_cannot_review(self):
        doctor_client, doctor_headers, patient_id, ecg_id = self.make_patient_and_ecg()
        tech_client, tech_headers = self.client_for("technician@example.test")
        denied = tech_client.post(f"/api/v1/ecgs/{ecg_id}/review", headers=tech_headers,
                                  json={"doctor_assessment": "not allowed"})
        self.assertEqual(denied.status_code, 403, denied.get_json())

        super_client, super_headers = self.client_for("superadmin@example.test")
        hospital = super_client.post("/api/v1/admin/hospitals", headers=super_headers,
                                     json={"name": "Other Hospital", "code": "OTHER"})
        self.assertEqual(hospital.status_code, 201, hospital.get_json())
        other_id = hospital.get_json()["organization_id"]
        user = super_client.post("/api/v1/admin/users", headers=super_headers, json={
            "email": "otherdoctor@example.test", "password": PASSWORD, "display_name": "Other Doctor",
            "role": "DOCTOR", "organization_id": other_id,
        })
        self.assertEqual(user.status_code, 201, user.get_json())
        other_client, other_headers = self.client_for("otherdoctor@example.test")
        inaccessible = other_client.get(f"/api/v1/patients/{patient_id}", headers=other_headers)
        self.assertEqual(inaccessible.status_code, 404, inaccessible.get_json())
        self.assertEqual(inaccessible.get_json()["error"]["code"], "PATIENT_NOT_FOUND")

    def test_shared_patient_does_not_cross_tenant_fhir_or_identifier_boundaries(self):
        city_client, city_headers, patient_id, ecg_id = self.make_patient_and_ecg()
        super_client, super_headers = self.client_for("superadmin@example.test")
        hospital = super_client.post("/api/v1/admin/hospitals", headers=super_headers,
                                     json={"name": f"Shared Identity Hospital {uuid.uuid4().hex[:8]}", "code": f"SH{uuid.uuid4().hex[:6]}"})
        self.assertEqual(hospital.status_code, 201, hospital.get_json())
        other_id = hospital.get_json()["organization_id"]
        user = super_client.post("/api/v1/admin/users", headers=super_headers, json={
            "email": f"shared-doctor-{uuid.uuid4().hex[:8]}@example.test", "password": PASSWORD,
            "display_name": "Shared Identity Doctor", "role": "DOCTOR", "organization_id": other_id,
        })
        self.assertEqual(user.status_code, 201, user.get_json())

        with self.app.app_context():
            session = get_session()
            ecg = session.get(ECGRecord, uuid.UUID(ecg_id))
            city_doctor = session.scalar(select(User).where(User.email == "doctor@example.test"))
            self.assertIsNotNone(ecg)
            self.assertIsNotNone(city_doctor)
            session.add(PatientOrganization(patient_uuid=ecg.patient_uuid, organization_id=uuid.UUID(other_id),
                                            mrn=f"SHARED-{uuid.uuid4().hex[:10].upper()}"))
            condition = Condition(patient_uuid=ecg.patient_uuid, encounter_uuid=ecg.encounter_uuid,
                                  organization_id=ecg.organization_id, display="City-only condition",
                                  recorded_by=city_doctor.user_id)
            prescription = Prescription(patient_uuid=ecg.patient_uuid, encounter_uuid=ecg.encounter_uuid,
                                        organization_id=ecg.organization_id, prescriber_id=city_doctor.user_id)
            session.add_all([condition, prescription])
            session.commit()
            condition_id, prescription_id = str(condition.condition_uuid), str(prescription.prescription_uuid)

        other_client, other_headers = self.client_for(user.get_json()["email"])
        identifiers = other_client.get(f"/api/v1/patients/{patient_id}/identifiers", headers=other_headers)
        self.assertEqual(identifiers.status_code, 200, identifiers.get_json())
        self.assertEqual(identifiers.get_json(), [])
        condition_response = other_client.get(f"/api/v1/fhir/Condition/{condition_id}", headers=other_headers)
        self.assertEqual(condition_response.status_code, 404, condition_response.get_json())
        prescription_response = other_client.get(f"/api/v1/fhir/MedicationRequest/{prescription_id}", headers=other_headers)
        self.assertEqual(prescription_response.status_code, 404, prescription_response.get_json())

    def test_no_authentication_or_cross_origin_bypass(self):
        client = self.app.test_client()
        no_token = client.get("/api/v1/patients")
        self.assertEqual(no_token.status_code, 401)
        _, headers = self.client_for("doctor@example.test")
        blocked = client.get("/api/v1/patients", headers={**headers, "Origin": "https://attacker.example"})
        self.assertEqual(blocked.status_code, 403, blocked.get_json())


if __name__ == "__main__":
    unittest.main()
