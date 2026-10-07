"""End-to-end tests for the separate, research-only clinical platform API."""
from __future__ import annotations

from io import BytesIO
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import uuid

import numpy as np
from PIL import Image, ImageDraw
from scipy.io import savemat
from sqlalchemy import select
import torch

os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/ecg-platform-test-mpl")

from ecg_cvd.clinical.app import create_app
from ecg_cvd.clinical.db import get_session
from ecg_cvd.clinical.models import (AIAnalysis, Condition, DoctorReview, ECGFile, ECGRecord, PatientOrganization,
                                     Prescription, User)
from ecg_cvd.clinical.seed import seed_development_data
from ecg_cvd.model import RAMNV2


PASSWORD = "AResearchOnlyTestPassword!"


def waveform_mat() -> bytes:
    stream = BytesIO()
    savemat(stream, {"val": np.sin(np.arange(3600, dtype=np.float32) / 17)})
    return stream.getvalue()


def ecg_jpeg() -> bytes:
    """A small stand-in for an authorized ECG-paper/photo source image."""
    stream = BytesIO()
    Image.new("RGB", (96, 48), color=(240, 248, 255)).save(stream, format="JPEG", quality=90)
    return stream.getvalue()


def digitizable_ecg_chart_jpeg() -> bytes:
    """A clean, single-colour synthetic chart accepted by the strict experiment.

    It deliberately resembles a simple exported waveform plot rather than a
    paper scan or an arbitrary dashboard screenshot. The test checks that the
    server only converts this narrow, quality-gated input class.
    """
    width, height = 1_200, 420
    left, right, top, bottom = 72, 1_150, 46, 350
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    for x in range(left, right + 1, 108):
        draw.line((x, top, x, bottom), fill=(226, 233, 236), width=1)
    for y in range(top, bottom + 1, 61):
        draw.line((left, y, right, y), fill=(226, 233, 236), width=1)
    draw.line((left, bottom, right, bottom), fill=(101, 116, 125), width=2)
    draw.line((left, top, left, bottom), fill=(101, 116, 125), width=2)
    # A deterministic ECG-like trace with repeating narrow R peaks.  It is
    # not intended to represent a clinical waveform; it only exercises trace
    # recovery, serialization, encryption, and model plumbing.
    sample_count = right - left + 1
    x = np.linspace(0.0, 10.0, sample_count)
    signal = 0.10 * np.sin(2 * np.pi * 1.0 * x) + 0.03 * np.sin(2 * np.pi * 3.2 * x)
    for center in np.arange(0.7, 10.0, 1.0):
        signal += 1.65 * np.exp(-((x - center) / 0.018) ** 2)
        signal -= 0.34 * np.exp(-((x - (center + 0.035)) / 0.032) ** 2)
    signal = (signal - signal.min()) / (signal.max() - signal.min())
    points = [(left + index, int(300 - value * 195)) for index, value in enumerate(signal)]
    draw.line(points, fill=(0, 126, 146), width=3, joint="curve")
    stream = BytesIO()
    image.save(stream, format="JPEG", quality=96, subsampling=0)
    return stream.getvalue()


def non_chart_screenshot_jpeg() -> bytes:
    """A colourful UI-like image that must not be treated as an ECG chart."""
    image = Image.new("RGB", (960, 540), color=(245, 249, 250))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 960, 62), fill=(10, 130, 145))
    draw.rectangle((48, 118, 430, 310), fill=(219, 237, 244))
    draw.rectangle((492, 118, 912, 310), fill=(224, 243, 230))
    draw.rectangle((48, 355, 912, 485), fill=(238, 241, 245))
    # Straight decoration lines are purposely not a trace and should fail the
    # complexity gate even though the screenshot has saturated colours.
    draw.line((90, 205, 380, 205), fill=(0, 126, 146), width=4)
    draw.line((540, 205, 865, 205), fill=(0, 126, 146), width=4)
    stream = BytesIO()
    image.save(stream, format="JPEG", quality=94, subsampling=0)
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

    def setUp(self):
        # Each test exercises its own authentication workflow.  Reset the
        # in-memory local limiter so test ordering cannot consume the shared
        # login-route quota.
        self.app.extensions["ecg_platform_limiter"].reset()

    def test_health_probes_do_not_exhaust_application_rate_limit(self):
        client = self.app.test_client()
        for _ in range(1000):
            self.assertEqual(client.get("/healthz").status_code, 200)
        self.assertEqual(client.get("/readyz").status_code, 200)

    def test_configured_model_info_is_safe_for_authorized_research_roles_only(self):
        """Configured RAMNV2 provenance must not disclose the host model path."""
        for email in ("doctor@example.test", "technician@example.test", "superadmin@example.test"):
            client, headers = self.client_for(email)
            response = client.get("/api/v1/ai/model-info", headers=headers)
            self.assertEqual(response.status_code, 200, response.get_json())
            payload = response.get_json()
            self.assertEqual(payload["status"], "CONFIGURED")
            self.assertEqual(payload["model_name"], "RAMNV2 ECG research classifier")
            self.assertEqual(payload["architecture"], "RAMNV2")
            self.assertEqual(payload["framework"], "PyTorch")
            self.assertEqual(payload["version"], "test-v1")
            self.assertEqual(payload["supported_source_formats"], [".csv", ".mat"])
            self.assertEqual(payload["image_source_formats"], [".jpeg", ".jpg"])
            self.assertTrue(payload["research_only"])
            self.assertIn("not classifier inputs", payload["image_policy"])
            self.assertIn("not an autonomous diagnosis", payload["safety"])
            self.assertNotIn("path", payload)
            self.assertNotIn(str(self.model_path), response.get_data(as_text=True))
            self.assertNotIn(str(self.root), response.get_data(as_text=True))

        patient_client, patient_headers = self.client_for("patient@example.test")
        denied = patient_client.get("/api/v1/ai/model-info", headers=patient_headers)
        self.assertEqual(denied.status_code, 403, denied.get_json())
        self.assertEqual(denied.get_json()["error"]["code"], "FORBIDDEN")

    def client_for(self, email: str):
        client = self.app.test_client()
        response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.get_json())
        return client, {"Authorization": f"Bearer {response.get_json()['access_token']}"}

    def make_patient_and_encounter(self):
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
        return client, headers, patient_id, encounter.get_json()["id"]

    def make_patient_and_ecg(self):
        client, headers, patient_id, encounter_id = self.make_patient_and_encounter()
        upload = client.post("/api/v1/ecgs", headers=headers, data={
            "patient_id": patient_id, "encounter_id": encounter_id,
            "file": (BytesIO(waveform_mat()), "sample.mat"),
        })
        self.assertEqual(upload.status_code, 201, upload.get_json())
        return client, headers, patient_id, upload.get_json()["id"]

    def test_jpeg_ecg_upload_is_encrypted_image_only_and_cannot_be_analyzed(self):
        client, headers, patient_id, encounter_id = self.make_patient_and_encounter()
        source = ecg_jpeg()
        upload = client.post("/api/v1/ecgs", headers=headers, data={
            "patient_id": patient_id, "encounter_id": encounter_id,
            "file": (BytesIO(source), "paper-tracing.jpg"),
        })
        self.assertEqual(upload.status_code, 201, upload.get_json())
        created = upload.get_json()
        self.assertEqual(created["status"], "IMAGE_ONLY")
        self.assertEqual(created["source_kind"], "ECG_IMAGE")
        self.assertEqual(created["content_type"], "image/jpeg")
        self.assertIsNone(created["sampling_rate"])
        ecg_id = created["id"]

        listed = client.get(f"/api/v1/ecgs?patient_id={patient_id}", headers=headers)
        self.assertEqual(listed.status_code, 200, listed.get_json())
        self.assertEqual(listed.get_json()[0]["content_type"], "image/jpeg")
        self.assertEqual(listed.get_json()[0]["source_kind"], "ECG_IMAGE")

        security = client.get(f"/api/v1/ecgs/{ecg_id}/security", headers=headers)
        self.assertEqual(security.status_code, 200, security.get_json())
        self.assertTrue(security.get_json()["storage"]["encrypted_at_rest"])
        self.assertEqual(security.get_json()["storage"]["algorithm"], "AES-256-GCM")

        with self.app.app_context():
            file = get_session().scalar(select(ECGFile).where(ECGFile.ecg_uuid == uuid.UUID(ecg_id)))
            self.assertIsNotNone(file)
            stored = (self.root / "private_objects" / file.object_key).read_bytes()
            self.assertTrue(stored.startswith(b"ECG-CLINICAL-AESGCM-1\x00"))
            expected_sha256 = file.sha256

        download = client.get(f"/api/v1/ecgs/{ecg_id}/file", headers=headers)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.mimetype, "image/jpeg")
        self.assertEqual(download.data, source)
        self.assertEqual(hashlib.sha256(download.data).hexdigest(), expected_sha256)

        waveform = client.get(f"/api/v1/ecgs/{ecg_id}/waveform", headers=headers)
        self.assertEqual(waveform.status_code, 409, waveform.get_json())
        self.assertEqual(waveform.get_json()["error"]["code"], "ECG_IMAGE_ONLY")
        analysis = client.post(f"/api/v1/ecgs/{ecg_id}/analyze", headers=headers)
        self.assertEqual(analysis.status_code, 409, analysis.get_json())
        self.assertEqual(analysis.get_json()["error"]["code"], "ECG_IMAGE_ONLY")
        with self.app.app_context():
            analyses = get_session().scalars(select(AIAnalysis).where(
                AIAnalysis.ecg_uuid == uuid.UUID(ecg_id)
            )).all()
            self.assertEqual(analyses, [])

    def test_jpeg_extension_requires_a_real_decodable_jpeg(self):
        client, headers, patient_id, encounter_id = self.make_patient_and_encounter()
        response = client.post("/api/v1/ecgs", headers=headers, data={
            "patient_id": patient_id, "encounter_id": encounter_id,
            "file": (BytesIO(waveform_mat()), "disguised.jpg"),
        })
        self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(response.get_json()["error"]["code"], "INVALID_ECG_IMAGE")
        listed = client.get(f"/api/v1/ecgs?patient_id={patient_id}", headers=headers)
        self.assertEqual(listed.status_code, 200, listed.get_json())
        self.assertEqual(listed.get_json(), [])

    def test_quality_gated_jpeg_digitization_creates_encrypted_provenanced_waveform_and_analysis(self):
        """A clean chart may opt into a separate, research-only derivative."""
        client, headers, patient_id, encounter_id = self.make_patient_and_encounter()
        source = digitizable_ecg_chart_jpeg()
        upload = client.post("/api/v1/ecgs", headers=headers, data={
            "patient_id": patient_id,
            "encounter_id": encounter_id,
            "file": (BytesIO(source), "clean-chart.jpg"),
        })
        self.assertEqual(upload.status_code, 201, upload.get_json())
        source_id = upload.get_json()["id"]

        # The acknowledgement is intentionally mandatory, so the normal JPEG
        # upload path can never silently become a classifier input.
        missing_ack = client.post(f"/api/v1/ecgs/{source_id}/digitize", headers=headers,
                                  json={"output_format": "csv"})
        self.assertEqual(missing_ack.status_code, 400, missing_ack.get_json())

        converted = client.post(f"/api/v1/ecgs/{source_id}/digitize", headers=headers, json={
            "confirm_experimental": True,
            "output_format": "csv",
        })
        self.assertEqual(converted.status_code, 201, converted.get_json())
        body = converted.get_json()
        self.assertEqual(body["source_ecg_id"], source_id)
        self.assertEqual(body["digitization"]["status"], "COMPLETED")
        self.assertEqual(body["digitization"]["provenance"], "EXPERIMENTAL_JPEG_TRACE_DIGITIZATION")
        self.assertEqual(body["digitization"]["output_format"], "csv")
        self.assertTrue(body["digitization"]["source_integrity_verified"])
        self.assertEqual(body["digitization"]["quality"]["output_samples"], 3600)
        self.assertIn("not clinically validated", body["digitization"]["limitations"])

        derived = body["derived_ecg"]
        self.assertIsInstance(derived["id"], str)
        self.assertEqual(derived["source_kind"], "JPEG_DIGITIZED_WAVEFORM")
        self.assertEqual(derived["provenance"]["source_ecg_id"], source_id)
        self.assertEqual(derived["content_type"], "text/csv")
        analysis = body["analysis"]
        self.assertIsInstance(analysis["id"], str)
        self.assertEqual(analysis["ecg_id"], derived["id"])
        self.assertEqual(analysis["status"], "COMPLETED")
        self.assertIn("predicted_label", analysis)
        self.assertTrue(analysis["has_explanation"])

        # The source JPEG stays byte-identical and image-only; the separately
        # persisted derivative is AES-GCM wrapped and a valid 3,600 sample CSV.
        original = client.get(f"/api/v1/ecgs/{source_id}/file", headers=headers)
        self.assertEqual(original.status_code, 200)
        self.assertEqual(original.data, source)
        self.assertEqual(client.get(f"/api/v1/ecgs/{source_id}", headers=headers).get_json()["status"], "IMAGE_ONLY")
        waveform = client.get(f"/api/v1/ecgs/{derived['id']}/waveform", headers=headers)
        self.assertEqual(waveform.status_code, 200, waveform.get_json())
        self.assertEqual(len(waveform.get_json()["samples"]), 3600)

        # The caller can request either supported persisted representation;
        # both are validated through the normal numerical-waveform parser.
        mat_converted = client.post(f"/api/v1/ecgs/{source_id}/digitize", headers=headers, json={
            "confirm_experimental": True,
            "output_format": "mat",
        })
        self.assertEqual(mat_converted.status_code, 201, mat_converted.get_json())
        mat_derived = mat_converted.get_json()["derived_ecg"]
        self.assertEqual(mat_converted.get_json()["digitization"]["output_format"], "mat")
        self.assertEqual(mat_derived["content_type"], "application/x-matlab-data")
        mat_waveform = client.get(f"/api/v1/ecgs/{mat_derived['id']}/waveform", headers=headers)
        self.assertEqual(mat_waveform.status_code, 200, mat_waveform.get_json())
        self.assertEqual(len(mat_waveform.get_json()["samples"]), 3600)

        with self.app.app_context():
            session = get_session()
            derived_file = session.scalar(select(ECGFile).where(ECGFile.ecg_uuid == uuid.UUID(derived["id"])))
            self.assertIsNotNone(derived_file)
            self.assertEqual(derived_file.purpose, "DERIVED_FROM_JPEG")
            encrypted = (self.root / "private_objects" / derived_file.object_key).read_bytes()
            self.assertTrue(encrypted.startswith(b"ECG-CLINICAL-AESGCM-1\x00"))
            stored_analysis = session.get(AIAnalysis, uuid.UUID(analysis["id"]))
            self.assertEqual(stored_analysis.raw_output["input_provenance"]["source_ecg_id"], source_id)
            self.assertEqual(stored_analysis.raw_output["input_provenance"]["output_format"], "csv")
            self.assertEqual(stored_analysis.raw_output["input_provenance"]["quality"]["output_samples"], 3600)

    def test_digitization_rejects_blank_and_non_chart_jpegs_without_creating_derivative(self):
        client, headers, patient_id, encounter_id = self.make_patient_and_encounter()
        for index, source in enumerate((ecg_jpeg(), non_chart_screenshot_jpeg())):
            upload = client.post("/api/v1/ecgs", headers=headers, data={
                "patient_id": patient_id,
                "encounter_id": encounter_id,
                "file": (BytesIO(source), f"not-a-trace-{index}.jpg"),
            })
            self.assertEqual(upload.status_code, 201, upload.get_json())
            source_id = upload.get_json()["id"]
            rejected = client.post(f"/api/v1/ecgs/{source_id}/digitize", headers=headers, json={
                "confirm_experimental": True,
                "output_format": "mat",
            })
            self.assertEqual(rejected.status_code, 422, rejected.get_json())
            self.assertEqual(rejected.get_json()["error"]["code"], "ECG_IMAGE_DIGITIZATION_REJECTED")
            self.assertIn("sufficiently isolated", rejected.get_json()["error"]["message"])
            with self.app.app_context():
                derivatives = get_session().scalars(select(ECGRecord).where(
                    ECGRecord.device_id.like(f"EXPERIMENTAL_JPEG_TRACE:{source_id}%")
                )).all()
                self.assertEqual(derivatives, [])

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

    def test_assessment_suggestion_is_editable_research_only_and_tenant_scoped(self):
        client, headers, _patient_id, ecg_id = self.make_patient_and_ecg()

        unavailable = client.get(f"/api/v1/ecgs/{ecg_id}/assessment-suggestion", headers=headers)
        self.assertEqual(unavailable.status_code, 409, unavailable.get_json())
        self.assertEqual(unavailable.get_json()["error"]["code"], "ANALYSIS_REQUIRED")

        analysis_response = client.post(f"/api/v1/ecgs/{ecg_id}/analyze", headers=headers)
        self.assertEqual(analysis_response.status_code, 201, analysis_response.get_json())
        analysis = analysis_response.get_json()

        response = client.get(f"/api/v1/ecgs/{ecg_id}/assessment-suggestion", headers=headers)
        self.assertEqual(response.status_code, 200, response.get_json())
        suggestion = response.get_json()
        self.assertEqual(suggestion["analysis_id"], analysis["id"])
        self.assertIn("RAMNV2", suggestion["suggestion"])
        self.assertIn(analysis["prediction_name"], suggestion["suggestion"])
        self.assertIn("replace or delete", suggestion["suggestion"])
        self.assertIn("not provide a clinical finding, diagnosis", suggestion["suggestion"])
        self.assertNotIn("medicine", suggestion["suggestion"].lower())
        self.assertNotIn("dose", suggestion["suggestion"].lower())
        self.assertIn("not a diagnosis", suggestion["safety"].lower())
        self.assertIn("treatment", suggestion["safety"].lower())
        self.assertIn("prescribe medication", suggestion["safety"].lower())
        self.assertIn("dose", suggestion["safety"].lower())

        with self.app.app_context():
            reviews = get_session().scalars(select(DoctorReview).where(
                DoctorReview.ecg_uuid == uuid.UUID(ecg_id)
            )).all()
        self.assertEqual(reviews, [])

        technician_client, technician_headers = self.client_for("technician@example.test")
        denied = technician_client.get(f"/api/v1/ecgs/{ecg_id}/assessment-suggestion", headers=technician_headers)
        self.assertEqual(denied.status_code, 403, denied.get_json())
        self.assertEqual(denied.get_json()["error"]["code"], "FORBIDDEN")

        super_client, super_headers = self.client_for("superadmin@example.test")
        suffix = uuid.uuid4().hex[:8]
        hospital = super_client.post("/api/v1/admin/hospitals", headers=super_headers, json={
            "name": f"Suggestion Isolation Hospital {suffix}", "code": f"SUG{suffix}",
        })
        self.assertEqual(hospital.status_code, 201, hospital.get_json())
        other_user = super_client.post("/api/v1/admin/users", headers=super_headers, json={
            "email": f"suggestion-{suffix}@example.test", "password": PASSWORD,
            "display_name": "Suggestion Isolation Doctor", "role": "DOCTOR",
            "organization_id": hospital.get_json()["organization_id"],
        })
        self.assertEqual(other_user.status_code, 201, other_user.get_json())
        other_client, other_headers = self.client_for(f"suggestion-{suffix}@example.test")
        cross_tenant = other_client.get(f"/api/v1/ecgs/{ecg_id}/assessment-suggestion", headers=other_headers)
        self.assertEqual(cross_tenant.status_code, 404, cross_tenant.get_json())
        self.assertEqual(cross_tenant.get_json()["error"]["code"], "ECG_NOT_FOUND")

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

    def test_refresh_cookie_and_logout_work_with_sqlite_timestamps(self):
        """The refresh-cookie lifecycle must work when SQLite strips tzinfo."""
        client = self.app.test_client()
        login = client.post("/api/v1/auth/login", json={"email": "doctor@example.test", "password": PASSWORD})
        self.assertEqual(login.status_code, 200, login.get_json())

        refreshed = client.post("/api/v1/auth/refresh")
        self.assertEqual(refreshed.status_code, 200, refreshed.get_json())
        access_token = refreshed.get_json()["access_token"]
        identity = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
        self.assertEqual(identity.status_code, 200, identity.get_json())

        logout = client.post("/api/v1/auth/logout")
        self.assertEqual(logout.status_code, 200, logout.get_json())
        self.assertEqual(logout.get_json()["status"], "logged_out")

        after_logout = client.post("/api/v1/auth/refresh")
        self.assertEqual(after_logout.status_code, 401, after_logout.get_json())

    def test_new_local_ecg_assets_use_authenticated_aes_gcm_storage(self):
        client, headers, _patient_id, ecg_id = self.make_patient_and_ecg()
        security = client.get(f"/api/v1/ecgs/{ecg_id}/security", headers=headers)
        self.assertEqual(security.status_code, 200, security.get_json())
        payload = security.get_json()
        self.assertTrue(payload["storage"]["encrypted_at_rest"])
        self.assertEqual(payload["storage"]["algorithm"], "AES-256-GCM")
        self.assertTrue(payload["storage"]["authenticated_encryption"])
        self.assertTrue(payload["integrity"]["verified_on_authorized_read"])
        self.assertFalse(payload["research_camouflage"]["used"])

        with self.app.app_context():
            file = get_session().scalar(select(ECGFile).where(ECGFile.ecg_uuid == uuid.UUID(ecg_id)))
            self.assertIsNotNone(file)
            stored = (self.root / "private_objects" / file.object_key).read_bytes()
            expected_sha256 = file.sha256
        self.assertTrue(stored.startswith(b"ECG-CLINICAL-AESGCM-1\x00"))

        download = client.get(f"/api/v1/ecgs/{ecg_id}/file", headers=headers)
        self.assertEqual(download.status_code, 200, download.get_json())
        self.assertEqual(hashlib.sha256(download.data).hexdigest(), expected_sha256)

    def test_only_doctors_receive_original_ecg_data_or_waveform_visualizations(self):
        """Role permissions must never make original ECG bytes public.

        This covers both numerical waveform and JPEG source assets plus the
        less-obvious rendered paths: Grad-CAM and waveform-bearing reports.
        A literal DOCTOR role is required; SUPER_ADMIN's wildcard permission
        deliberately does not grant this clinical-data capability.
        """
        doctor, doctor_headers, patient_id, encounter_id = self.make_patient_and_encounter()
        waveform_source = waveform_mat()
        waveform_upload = doctor.post("/api/v1/ecgs", headers=doctor_headers, data={
            "patient_id": patient_id, "encounter_id": encounter_id,
            "file": (BytesIO(waveform_source), "doctor-only.mat"),
        })
        self.assertEqual(waveform_upload.status_code, 201, waveform_upload.get_json())
        waveform_id = waveform_upload.get_json()["id"]

        jpeg_source = ecg_jpeg()
        image_upload = doctor.post("/api/v1/ecgs", headers=doctor_headers, data={
            "patient_id": patient_id, "encounter_id": encounter_id,
            "file": (BytesIO(jpeg_source), "doctor-only.jpg"),
        })
        self.assertEqual(image_upload.status_code, 201, image_upload.get_json())
        image_id = image_upload.get_json()["id"]

        # A doctor retains the complete source-data workflow.
        waveform = doctor.get(f"/api/v1/ecgs/{waveform_id}/waveform", headers=doctor_headers)
        self.assertEqual(waveform.status_code, 200, waveform.get_json())
        self.assertEqual(len(waveform.get_json()["samples"]), 3600)
        self.assertEqual(doctor.get(f"/api/v1/ecgs/{waveform_id}/file", headers=doctor_headers).data,
                         waveform_source)
        self.assertEqual(doctor.get(f"/api/v1/ecgs/{image_id}/file", headers=doctor_headers).data,
                         jpeg_source)
        signed = doctor.get(f"/api/v1/ecgs/{waveform_id}/download-url", headers=doctor_headers)
        self.assertEqual(signed.status_code, 200, signed.get_json())
        self.assertEqual(signed.get_json()["url"], f"/api/v1/ecgs/{waveform_id}/file")

        analysis = doctor.post(f"/api/v1/ecgs/{waveform_id}/analyze", headers=doctor_headers)
        self.assertEqual(analysis.status_code, 201, analysis.get_json())
        explanation = doctor.get(f"/api/v1/ecgs/{waveform_id}/explain.png", headers=doctor_headers)
        self.assertEqual(explanation.status_code, 200)
        self.assertTrue(explanation.data.startswith(b"\x89PNG\r\n\x1a\n"))
        review = doctor.post(f"/api/v1/ecgs/{waveform_id}/review", headers=doctor_headers, json={
            "doctor_assessment": "Independent clinician review for access-control test.",
        })
        self.assertEqual(review.status_code, 201, review.get_json())
        reports = doctor.get(f"/api/v1/reports?patient_id={patient_id}", headers=doctor_headers)
        self.assertEqual(reports.status_code, 200, reports.get_json())
        report_id = reports.get_json()[0]["id"]
        generated = doctor.post(f"/api/v1/reports/{report_id}/generate-pdf", headers=doctor_headers)
        self.assertEqual(generated.status_code, 201, generated.get_json())
        doctor_report = doctor.get(f"/api/v1/reports/{report_id}/download", headers=doctor_headers)
        self.assertEqual(doctor_report.status_code, 200)
        self.assertTrue(doctor_report.data.startswith(b"%PDF"))

        # Link a patient-login principal to this exact patient so its request
        # succeeds tenant/self scope and exercises the doctor-only boundary.
        with self.app.app_context():
            ecg = get_session().get(ECGRecord, uuid.UUID(waveform_id))
            self.assertIsNotNone(ecg)
            organization_id = str(ecg.organization_id)
        superadmin, superadmin_headers = self.client_for("superadmin@example.test")
        patient_email = f"ecg-access-patient-{uuid.uuid4().hex[:10]}@example.test"
        created_patient_user = superadmin.post("/api/v1/admin/users", headers=superadmin_headers, json={
            "email": patient_email, "password": PASSWORD, "display_name": "ECG Access Test Patient",
            "role": "PATIENT", "organization_id": organization_id, "patient_uuid": patient_id,
        })
        self.assertEqual(created_patient_user.status_code, 201, created_patient_user.get_json())
        patient, patient_headers = self.client_for(patient_email)
        technician, technician_headers = self.client_for("technician@example.test")
        hospital_admin, hospital_admin_headers = self.client_for("hospitaladmin@example.test")

        blocked_principals = (
            ("technician", technician, technician_headers),
            ("hospital administrator", hospital_admin, hospital_admin_headers),
            ("patient", patient, patient_headers),
            ("super administrator", superadmin, superadmin_headers),
        )
        for label, client, headers in blocked_principals:
            for ecg_id, original in ((waveform_id, waveform_source), (image_id, jpeg_source)):
                file_response = client.get(f"/api/v1/ecgs/{ecg_id}/file", headers=headers)
                self.assertEqual(file_response.status_code, 403, (label, file_response.get_json()))
                self.assertEqual(file_response.get_json()["error"]["code"], "FORBIDDEN")
                self.assertNotEqual(file_response.data, original)

                url_response = client.get(f"/api/v1/ecgs/{ecg_id}/download-url", headers=headers)
                self.assertEqual(url_response.status_code, 403, (label, url_response.get_json()))
                self.assertNotIn("url", url_response.get_json())

            trace_response = client.get(f"/api/v1/ecgs/{waveform_id}/waveform", headers=headers)
            self.assertEqual(trace_response.status_code, 403, (label, trace_response.get_json()))
            self.assertNotIn("samples", trace_response.get_json())

            gradcam_response = client.get(f"/api/v1/ecgs/{waveform_id}/explain.png", headers=headers)
            self.assertEqual(gradcam_response.status_code, 403, (label, gradcam_response.get_json()))
            self.assertFalse(gradcam_response.data.startswith(b"\x89PNG\r\n\x1a\n"))

            explanation_url = client.post(f"/api/v1/ecgs/{waveform_id}/explain", headers=headers)
            self.assertEqual(explanation_url.status_code, 403, (label, explanation_url.get_json()))

            pdf_response = client.get(f"/api/v1/reports/{report_id}/download", headers=headers)
            self.assertEqual(pdf_response.status_code, 403, (label, pdf_response.get_json()))
            self.assertFalse(pdf_response.data.startswith(b"%PDF"))

            generate_response = client.post(f"/api/v1/reports/{report_id}/generate-pdf", headers=headers)
            self.assertEqual(generate_response.status_code, 403, (label, generate_response.get_json()))

            preview = client.get(f"/api/v1/ecgs/{waveform_id}/protected-preview.png", headers=headers)
            self.assertEqual(preview.status_code, 200, (label, preview.get_json()))
            self.assertEqual(preview.mimetype, "image/png")
            self.assertTrue(preview.data.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertNotEqual(preview.data, explanation.data)
            self.assertNotIn(waveform_source, preview.data)
            self.assertNotIn(jpeg_source, preview.data)
            with Image.open(BytesIO(preview.data)) as redacted:
                self.assertEqual(redacted.size, (1_200, 420))
                self.assertEqual(redacted.format, "PNG")

        # The patient can see report metadata but never receives a waveform-
        # bearing download link. The same status response truthfully exposes
        # storage encryption while marking original access unavailable.
        patient_reports = patient.get(f"/api/v1/reports?patient_id={patient_id}", headers=patient_headers)
        self.assertEqual(patient_reports.status_code, 200, patient_reports.get_json())
        self.assertNotIn("download_url", patient_reports.get_json()[0])
        patient_security = patient.get(f"/api/v1/ecgs/{waveform_id}/security", headers=patient_headers)
        self.assertEqual(patient_security.status_code, 200, patient_security.get_json())
        self.assertFalse(patient_security.get_json()["access"]["original_ecg_available_to_current_user"])
        self.assertEqual(patient_security.get_json()["access"]["original_ecg_requires_role"], "DOCTOR")


if __name__ == "__main__":
    unittest.main()
