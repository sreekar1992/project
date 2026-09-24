"""Versioned REST API for the tenant-aware ECG health platform."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, TypeVar
import uuid

from flask import Blueprint, Response, current_app, g, jsonify, request, send_file
from flask_limiter import Limiter
from pydantic import BaseModel
from sqlalchemy import func, select
from werkzeug.utils import secure_filename

from . import fhir
from .db import get_session
from .models import (AIAnalysis, AIModel, AIModelVersion, Condition, DiagnosticReport, ECGRecord,
                     Encounter, Feature, Organization, PatientContact, PatientIdentifier, Practitioner,
                     Prescription, PrescriptionItem, ReportDocument, User)
from .schemas import (EncounterRequest, FeatureRequest, LoginRequest, OrganizationRequest, PatientMatchRequest,
                      PatientRequest, ReviewRequest, UserRequest)
from .security import (APIError, audit, current_principal, issue_token_pair, password_matches,
                       require_any_permission, require_permission, revoke_refresh_token, settings,
                       user_from_refresh_token)
from .services import ClinicalService, as_uuid


ModelT = TypeVar("ModelT", bound=BaseModel)


def _body(schema: type[ModelT]) -> ModelT:
    if not request.is_json:
        raise APIError("JSON_REQUIRED", "This endpoint requires an application/json request body.", 415)
    return schema.model_validate(request.get_json())


def _patient_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "id": payload["patient_uuid"], "sex_at_birth": payload.get("administrative_gender")}


def _encounter_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "id": payload["encounter_uuid"], "patient_id": payload["patient_uuid"],
            "occurred_at": payload["start_time"]}


def _ecg_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "id": payload["ecg_uuid"], "patient_id": payload["patient_uuid"],
            "encounter_id": payload["encounter_uuid"], "acquired_at": payload["recorded_at"]}


def _analysis_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "id": payload["ai_analysis_uuid"], "ecg_id": payload["ecg_uuid"],
            "predicted_label": payload.get("prediction_name") or payload.get("prediction"),
            "created_at": payload.get("inference_timestamp")}


def _review_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "id": payload["doctor_review_uuid"], "analysis_id": payload["ai_analysis_uuid"],
            "clinician_notes": payload["assessment"]}


def _report_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "id": payload["report_uuid"], "title": "ECG clinical decision-support report"}


def _token_response(pair: dict[str, Any]) -> Response:
    refresh = str(pair.pop("refresh_token"))
    response = jsonify(pair)
    response.set_cookie("ecg_health_refresh", refresh, httponly=True, secure=not settings().development,
                        samesite="Lax", max_age=settings().refresh_token_days * 24 * 60 * 60, path="/api/v1/auth")
    return response


def create_api_blueprint(limiter: Limiter) -> Blueprint:
    api = Blueprint("ecg_platform_api", __name__, url_prefix="/api/v1")

    @api.post("/auth/login")
    @limiter.limit("10 per minute")
    def login():
        payload = _body(LoginRequest)
        session = get_session()
        user = session.scalar(select(User).where(func.lower(User.email) == payload.email.lower()))
        if user is None or not password_matches(user, payload.password):
            audit("LOGIN", "auth", success=False)
            session.commit()
            raise APIError("INVALID_CREDENTIALS", "The email address or password is invalid.", 401)
        user.last_login_at = __import__("ecg_cvd.clinical.models", fromlist=["utcnow"]).utcnow()
        pair = issue_token_pair(user)
        audit("LOGIN", "auth", str(user.user_id), success=True, organization_id=user.organization_id)
        session.commit()
        return _token_response(pair)

    @api.post("/auth/refresh")
    @limiter.limit("30 per minute")
    def refresh():
        data = request.get_json(silent=True) or {}
        token = data.get("refresh_token") if isinstance(data, dict) else None
        token = token or request.cookies.get("ecg_health_refresh")
        if not isinstance(token, str):
            raise APIError("AUTH_REQUIRED", "A refresh token is required.", 401)
        user, token_id = user_from_refresh_token(token)
        revoke_refresh_token(token_id)
        pair = issue_token_pair(user)
        audit("TOKEN_REFRESHED", "auth", str(user.user_id), organization_id=user.organization_id)
        get_session().commit()
        return _token_response(pair)

    @api.post("/auth/logout")
    def logout():
        data = request.get_json(silent=True) or {}
        token = data.get("refresh_token") if isinstance(data, dict) else None
        token = token or request.cookies.get("ecg_health_refresh")
        if isinstance(token, str):
            try:
                _user, token_id = user_from_refresh_token(token)
                revoke_refresh_token(token_id)
            except APIError:
                # Logout is idempotent and must not disclose token state.
                pass
        response = jsonify(status="logged_out")
        response.delete_cookie("ecg_health_refresh", path="/api/v1/auth")
        return response

    @api.get("/auth/me")
    @require_any_permission("patient.read", "patient.self.read", "hospital.read")
    def whoami():
        principal = current_principal()
        user = get_session().get(User, principal.user_id)
        return jsonify(user_id=str(user.user_id), display_name=user.display_name, email=user.email,
                       organization_id=str(principal.organization_id) if principal.organization_id else None,
                       roles=sorted(principal.roles))

    @api.get("/dashboard")
    @require_any_permission("patient.read", "patient.self.read")
    def dashboard():
        return jsonify(ClinicalService(current_principal()).dashboard())

    @api.get("/admin/dashboard")
    @require_permission("hospital.create")
    def admin_dashboard():
        session = get_session()
        return jsonify(metrics={
            "hospitals": session.scalar(select(func.count()).select_from(Organization)) or 0,
            "users": session.scalar(select(func.count()).select_from(User)) or 0,
            "patients": session.scalar(select(func.count()).select_from(__import__("ecg_cvd.clinical.models", fromlist=["Patient"]).Patient)) or 0,
            "ecgs": session.scalar(select(func.count()).select_from(ECGRecord)) or 0,
            "analyses": session.scalar(select(func.count()).select_from(AIAnalysis)) or 0,
            "reports": session.scalar(select(func.count()).select_from(DiagnosticReport)) or 0,
        })

    @api.get("/admin/hospitals")
    @require_permission("hospital.read")
    def hospitals():
        return jsonify(ClinicalService(current_principal()).list_organizations())

    @api.post("/admin/hospitals")
    @require_permission("hospital.create")
    def create_hospital():
        payload = _body(OrganizationRequest)
        return jsonify(ClinicalService(current_principal()).create_organization(payload.name, payload.code)), 201

    @api.get("/admin/hospitals/<organization_id>")
    @require_permission("hospital.read")
    def hospital(organization_id: str):
        service = ClinicalService(current_principal())
        target = service._organization_id(organization_id)
        item = get_session().get(Organization, target)
        return jsonify(organization_id=str(item.organization_id), name=item.name, code=item.code, status=item.status)

    @api.patch("/admin/hospitals/<organization_id>")
    @require_permission("hospital.update")
    def update_hospital(organization_id: str):
        if not request.is_json or not isinstance(request.get_json(), dict):
            raise APIError("JSON_REQUIRED", "This endpoint requires a JSON object.", 415)
        body = request.get_json()
        status = body.get("status")
        if status not in {"ACTIVE", "DISABLED"}:
            raise APIError("INVALID_STATUS", "Hospital status must be ACTIVE or DISABLED.", 400)
        service = ClinicalService(current_principal())
        target = service._organization_id(organization_id)
        item = get_session().get(Organization, target)
        item.status = status
        get_session().commit()
        audit("HOSPITAL_UPDATED", "organization", str(target), organization_id=target)
        get_session().commit()
        return jsonify(organization_id=str(item.organization_id), status=item.status)

    @api.get("/admin/users")
    @require_permission("user.read")
    def users():
        service = ClinicalService(current_principal())
        query = select(User).order_by(User.display_name)
        if not service.principal.is_super_admin:
            query = query.where(User.organization_id == service._organization_id())
        return jsonify([{"user_id": str(user.user_id), "email": user.email, "display_name": user.display_name,
                         "active": user.active, "organization_id": str(user.organization_id) if user.organization_id else None}
                        for user in get_session().scalars(query)])

    @api.post("/admin/users")
    @require_permission("user.create")
    def create_user():
        return jsonify(ClinicalService(current_principal()).create_user(_body(UserRequest))), 201

    @api.get("/admin/features")
    @require_permission("feature.read")
    def features():
        return jsonify(ClinicalService(current_principal()).list_features())

    @api.post("/admin/features")
    @require_permission("feature.create")
    def create_feature():
        return jsonify(ClinicalService(current_principal()).create_feature(_body(FeatureRequest))), 201

    @api.patch("/admin/features/<feature_id>")
    @require_permission("feature.update")
    def update_feature(feature_id: str):
        if not request.is_json or not isinstance(request.get_json(), dict):
            raise APIError("JSON_REQUIRED", "This endpoint requires a JSON object.", 415)
        item = get_session().get(Feature, as_uuid(feature_id, "feature"))
        if item is None:
            raise APIError("FEATURE_NOT_FOUND", "The feature could not be found.", 404)
        enabled = request.get_json().get("enabled")
        if not isinstance(enabled, bool):
            raise APIError("VALIDATION_ERROR", "Feature enabled must be a boolean.", 400)
        item.enabled = enabled
        get_session().commit()
        audit("FEATURE_CHANGED", "feature", str(item.feature_uuid))
        get_session().commit()
        return jsonify(feature_uuid=str(item.feature_uuid), key=item.key, enabled=item.enabled)

    @api.get("/admin/ai-models")
    @require_permission("hospital.read")
    def ai_models():
        session = get_session()
        versions = session.execute(select(AIModelVersion, AIModel).join(AIModel,
                                  AIModel.model_uuid == AIModelVersion.model_uuid).order_by(AIModelVersion.created_at.desc())).all()
        return jsonify([{"model_uuid": str(model.model_uuid), "model_name": model.model_name,
                         "model_version_uuid": str(version.model_version_uuid), "version": version.version,
                         "framework": model.framework, "status": version.status,
                         "artifact_sha256": version.artifact_sha256, "metrics": version.metrics,
                         "preprocessing": version.preprocessing} for version, model in versions])

    @api.get("/admin/audit-logs")
    @require_permission("audit.read")
    def audit_logs():
        return jsonify(ClinicalService(current_principal()).audit_events(int(request.args.get("limit", "100"))))

    @api.post("/patients/matches")
    @require_permission("patient.create")
    def patient_matches():
        return jsonify(ClinicalService(current_principal()).find_patient_matches(_body(PatientMatchRequest)))

    @api.get("/patients")
    @require_any_permission("patient.read", "patient.self.read")
    def list_patients():
        records = ClinicalService(current_principal()).list_patients(request.args.get("search"),
                                                                      int(request.args.get("limit", "50")))
        return jsonify([_patient_response(record) for record in records])

    @api.post("/patients")
    @require_permission("patient.create")
    def create_patient():
        return jsonify(_patient_response(ClinicalService(current_principal()).create_patient(_body(PatientRequest)))), 201

    @api.get("/patients/<patient_id>")
    @require_any_permission("patient.read", "patient.self.read")
    def get_patient(patient_id: str):
        return jsonify(_patient_response(ClinicalService(current_principal()).get_patient(patient_id)))

    @api.get("/patients/<patient_id>/identifiers")
    @require_any_permission("patient.read", "patient.self.read")
    def patient_identifiers(patient_id: str):
        service = ClinicalService(current_principal())
        patient, _membership = service._patient(patient_id)
        query = select(PatientIdentifier).where(PatientIdentifier.patient_uuid == patient.patient_uuid,
                                                PatientIdentifier.active.is_(True))
        # Identifier values are hospital-scoped.  A shared EMPI must not make
        # one hospital's identifiers visible to another hospital.
        if service.principal.organization_id is not None:
            query = query.where(PatientIdentifier.organization_id == service.principal.organization_id)
        rows = get_session().scalars(query).all()
        return jsonify([{"identifier_system": row.identifier_system, "identifier_type": row.identifier_type,
                         "identifier_value": row.identifier_value, "assigning_organization": row.assigning_organization}
                        for row in rows])

    @api.get("/patients/<patient_id>/encounters")
    @require_any_permission("encounter.read", "encounter.self.read")
    def patient_encounters(patient_id: str):
        return jsonify([_encounter_response(row) for row in ClinicalService(current_principal()).list_encounters(patient_id)])

    @api.get("/encounters")
    @require_any_permission("encounter.read", "encounter.self.read")
    def encounters():
        patient_id = request.args.get("patient_id")
        if not patient_id:
            raise APIError("PATIENT_REQUIRED", "patient_id is required.", 400)
        return jsonify([_encounter_response(row) for row in ClinicalService(current_principal()).list_encounters(patient_id)])

    @api.post("/encounters")
    @require_permission("encounter.create")
    def create_encounter():
        raw = request.get_json(silent=True) or {}
        if "patient_id" in raw and "patient_uuid" not in raw:
            raw["patient_uuid"] = raw.pop("patient_id")
        return jsonify(_encounter_response(ClinicalService(current_principal()).create_encounter(
            EncounterRequest.model_validate(raw)))), 201

    @api.get("/encounters/<encounter_id>")
    @require_any_permission("encounter.read", "encounter.self.read")
    def get_encounter(encounter_id: str):
        return jsonify(_encounter_response(ClinicalService(current_principal()).encounter_dict(
            ClinicalService(current_principal())._encounter(encounter_id))))

    @api.get("/ecgs")
    @require_any_permission("ecg.read", "ecg.self.read")
    def list_ecgs():
        patient_id = request.args.get("patient_id")
        if not patient_id:
            raise APIError("PATIENT_REQUIRED", "patient_id is required.", 400)
        return jsonify([_ecg_response(row) for row in ClinicalService(current_principal()).list_ecgs(patient_id)])

    @api.post("/ecgs")
    @api.post("/ecgs/upload")
    @require_permission("ecg.upload")
    def upload_ecg():
        uploaded = request.files.get("file")
        encounter_id = request.form.get("encounter_id") or request.form.get("encounter_uuid")
        if uploaded is None or not uploaded.filename:
            raise APIError("ECG_FILE_REQUIRED", "Choose one ECG .mat or .csv file to upload.", 400)
        if not encounter_id:
            raise APIError("ENCOUNTER_REQUIRED", "An existing encounter is required before ECG upload.", 400)
        service = ClinicalService(current_principal())
        encounter = service._encounter(encounter_id)
        supplied_patient = request.form.get("patient_id")
        if supplied_patient and as_uuid(supplied_patient, "patient") != encounter.patient_uuid:
            raise APIError("ENCOUNTER_PATIENT_MISMATCH", "The selected encounter is not for the requested patient.", 409)
        record = service.upload_ecg(str(encounter.encounter_uuid), uploaded.read(),
                                    secure_filename(uploaded.filename) or "uploaded.mat",
                                    request.form.get("device_id"))
        return jsonify(_ecg_response(record)), 201

    @api.get("/ecgs/<ecg_id>")
    @require_any_permission("ecg.read", "ecg.self.read")
    def get_ecg(ecg_id: str):
        return jsonify(_ecg_response(ClinicalService(current_principal()).get_ecg(ecg_id)))

    @api.get("/ecgs/<ecg_id>/file")
    @require_any_permission("ecg.read", "ecg.self.read")
    def download_ecg(ecg_id: str):
        data, asset = ClinicalService(current_principal()).download_ecg(ecg_id)
        return send_file(BytesIO(data), mimetype=asset.content_type, as_attachment=True,
                         download_name=asset.original_filename, max_age=0)

    @api.get("/ecgs/<ecg_id>/waveform")
    @require_any_permission("ecg.read", "ecg.self.read")
    def waveform_data(ecg_id: str):
        return jsonify(ClinicalService(current_principal()).waveform_data(ecg_id))

    @api.get("/ecgs/<ecg_id>/download-url")
    @require_any_permission("ecg.read", "ecg.self.read")
    def ecg_download_url(ecg_id: str):
        service = ClinicalService(current_principal())
        ecg = service._ecg(ecg_id)
        asset = service._file_for_ecg(ecg)
        signed = service.storage.presigned_get(asset.object_key)
        return jsonify(url=signed or f"/api/v1/ecgs/{ecg.ecg_uuid}/file", expires_in_seconds=300 if signed else None)

    def _start_analysis(ecg_id: str):
        service = ClinicalService(current_principal())
        analysis = service.create_analysis(ecg_id)
        if settings().async_analysis:
            from .worker import enqueue_analysis
            enqueue_analysis(analysis.ai_analysis_uuid, analysis.organization_id)
            return jsonify(_analysis_response(service.analysis_dict(analysis))), 202
        completed = service.run_analysis(analysis.ai_analysis_uuid)
        return jsonify(_analysis_response(service.analysis_dict(completed))), 201

    @api.post("/ecgs/<ecg_id>/analyze")
    @require_permission("ecg.analyze")
    def analyze_ecg(ecg_id: str):
        return _start_analysis(ecg_id)

    @api.post("/analyses")
    @require_permission("ecg.analyze")
    def create_analysis():
        body = request.get_json(silent=True) or {}
        ecg_id = body.get("ecg_id")
        if not isinstance(ecg_id, str):
            raise APIError("ECG_REQUIRED", "ecg_id is required.", 400)
        return _start_analysis(ecg_id)

    @api.get("/ecgs/<ecg_id>/analysis")
    @require_permission("ai.read")
    def ecg_analysis(ecg_id: str):
        return jsonify(_analysis_response(ClinicalService(current_principal()).latest_analysis(ecg_id)))

    @api.get("/analyses")
    @require_permission("ai.read")
    def analyses():
        patient_id = request.args.get("patient_id")
        if not patient_id:
            raise APIError("PATIENT_REQUIRED", "patient_id is required.", 400)
        return jsonify([_analysis_response(row) for row in ClinicalService(current_principal()).list_analyses(patient_id)])

    @api.post("/ecgs/<ecg_id>/explain")
    @require_permission("ai.read")
    def explain_ecg(ecg_id: str):
        # The actual Grad-CAM is persisted during the real analysis call.  This
        # route intentionally never manufactures an explanation on a label alone.
        result = ClinicalService(current_principal()).latest_analysis(ecg_id)
        if not result["has_explanation"]:
            raise APIError("EXPLANATION_NOT_FOUND", "No stored model explanation exists for this ECG.", 404)
        return jsonify(available=True, url=f"/api/v1/ecgs/{ecg_id}/explain.png", analysis=_analysis_response(result))

    @api.get("/ecgs/<ecg_id>/explain.png")
    @require_permission("ai.read")
    def explanation_image(ecg_id: str):
        data = ClinicalService(current_principal()).explanation_bytes(ecg_id)
        return send_file(BytesIO(data), mimetype="image/png", max_age=0)

    @api.post("/ecgs/<ecg_id>/review")
    @require_permission("diagnosis.create")
    def review_ecg(ecg_id: str):
        return jsonify(ClinicalService(current_principal()).save_review(ecg_id, _body(ReviewRequest))), 201

    @api.post("/reviews")
    @require_permission("diagnosis.create")
    def create_review():
        body = request.get_json(silent=True) or {}
        analysis_id = body.get("analysis_id")
        if not isinstance(analysis_id, str):
            raise APIError("VALIDATION_ERROR", "analysis_id is required.", 400)
        analysis = get_session().get(AIAnalysis, as_uuid(analysis_id, "analysis"))
        if analysis is None:
            raise APIError("ANALYSIS_NOT_FOUND", "The analysis could not be found.", 404)
        service = ClinicalService(current_principal())
        ecg = service._ecg(analysis.ecg_uuid)
        # This API-facing convenience route accepts the UI's `clinician_notes`
        # and `status` names, then validates the full clinician-authored review
        # contract. It never derives a diagnosis or prescription from the AI.
        payload = dict(body)
        payload.pop("analysis_id", None)
        if "doctor_assessment" not in payload and "clinician_notes" in payload:
            payload["doctor_assessment"] = payload["clinician_notes"]
        payload.pop("clinician_notes", None)
        if "review_status" not in payload and "status" in payload:
            payload["review_status"] = str(payload["status"]).upper()
        payload.pop("status", None)
        result = service.save_review(str(ecg.ecg_uuid), ReviewRequest.model_validate(payload))
        review = get_session().get(__import__("ecg_cvd.clinical.models", fromlist=["DoctorReview"]).DoctorReview,
                                   as_uuid(result["doctor_review_uuid"], "review"))
        return jsonify(_review_response({"doctor_review_uuid": str(review.doctor_review_uuid),
                                         "ai_analysis_uuid": str(review.ai_analysis_uuid), "ecg_uuid": str(review.ecg_uuid),
                                         "status": review.review_status, "assessment": review.assessment,
                                         "reviewed_at": review.reviewed_at.isoformat(),
                                         "reviewer": current_principal().user_id.hex})), 201

    @api.get("/reviews")
    @require_permission("ai.read")
    def reviews():
        patient_id = request.args.get("patient_id")
        if not patient_id:
            raise APIError("PATIENT_REQUIRED", "patient_id is required.", 400)
        return jsonify([_review_response(row) for row in ClinicalService(current_principal()).list_reviews(patient_id)])

    @api.get("/reports")
    @require_any_permission("report.read", "report.self.read")
    def reports():
        patient_id = request.args.get("patient_id")
        if not patient_id:
            raise APIError("PATIENT_REQUIRED", "patient_id is required.", 400)
        values = []
        for report in ClinicalService(current_principal()).list_reports(patient_id):
            record = _report_response(report)
            if report["has_pdf"]:
                record["download_url"] = f"/api/v1/reports/{report['report_uuid']}/download"
            values.append(record)
        return jsonify(values)

    @api.get("/reports/<report_id>")
    @require_any_permission("report.read", "report.self.read")
    def get_report(report_id: str):
        service = ClinicalService(current_principal())
        report, ecg, patient, membership = service._report_context(report_id)
        audit("REPORT_VIEWED", "report", str(report.report_uuid), organization_id=ecg.organization_id)
        get_session().commit()
        return jsonify(_report_response({"report_uuid": str(report.report_uuid), "ecg_uuid": str(ecg.ecg_uuid),
                                         "patient_uuid": str(patient.patient_uuid), "status": report.status,
                                         "created_at": report.created_at.isoformat(),
                                         "issued_at": report.issued_at.isoformat() if report.issued_at else None,
                                         "conclusion": report.conclusion, "mrn": membership.mrn}))

    @api.post("/reports/<report_id>/generate-pdf")
    @require_permission("report.create")
    def generate_report(report_id: str):
        return jsonify(ClinicalService(current_principal()).generate_report_pdf(report_id)), 201

    @api.get("/reports/<report_id>/download")
    @require_any_permission("report.download", "report.self.read")
    def download_report(report_id: str):
        payload, asset = ClinicalService(current_principal()).download_report(report_id)
        return send_file(BytesIO(payload), mimetype=asset.content_type, as_attachment=True,
                         download_name=f"ecg-report-{report_id}.pdf", max_age=0)

    @api.get("/audit")
    @require_permission("audit.read")
    def audit_api():
        return jsonify(ClinicalService(current_principal()).audit_events(int(request.args.get("limit", "100"))))

    @api.get("/fhir/<resource_type>/<resource_id>")
    @require_any_permission("patient.read", "patient.self.read", "report.read", "report.self.read")
    def fhir_resource(resource_type: str, resource_id: str):
        service = ClinicalService(current_principal())
        session = get_session()
        if resource_type == "Patient":
            patient, membership = service._patient(resource_id)
            return jsonify(fhir.patient_resource(patient, membership))
        if resource_type == "Encounter":
            return jsonify(fhir.encounter_resource(service._encounter(resource_id)))
        if resource_type == "Observation":
            analysis = session.get(AIAnalysis, as_uuid(resource_id, "analysis"))
            if analysis is None:
                raise APIError("OBSERVATION_NOT_FOUND", "The observation could not be found.", 404)
            ecg = service._ecg(analysis.ecg_uuid)
            return jsonify(fhir.observation_resource(ecg, analysis, session.get(AIModelVersion, analysis.model_version_uuid)))
        if resource_type == "DiagnosticReport":
            report, ecg, _patient, _membership = service._report_context(resource_id)
            analysis = session.scalar(select(AIAnalysis).where(AIAnalysis.ecg_uuid == ecg.ecg_uuid,
                                                                AIAnalysis.status == "COMPLETED").order_by(AIAnalysis.created_at.desc()))
            return jsonify(fhir.diagnostic_report_resource(report, ecg, analysis))
        if resource_type == "Condition":
            query = select(Condition).where(Condition.condition_uuid == as_uuid(resource_id, "condition"))
            if service.principal.organization_id is not None:
                query = query.where(Condition.organization_id == service.principal.organization_id)
            condition = session.scalar(query)
            if condition is None:
                raise APIError("CONDITION_NOT_FOUND", "The condition could not be found.", 404)
            service._patient(condition.patient_uuid)
            return jsonify(fhir.condition_resource(condition))
        if resource_type == "MedicationRequest":
            query = select(Prescription).where(Prescription.prescription_uuid == as_uuid(resource_id, "prescription"))
            if service.principal.organization_id is not None:
                query = query.where(Prescription.organization_id == service.principal.organization_id)
            prescription = session.scalar(query)
            if prescription is None:
                raise APIError("PRESCRIPTION_NOT_FOUND", "The prescription could not be found.", 404)
            service._patient(prescription.patient_uuid)
            items = session.scalars(select(PrescriptionItem).where(
                PrescriptionItem.prescription_uuid == prescription.prescription_uuid)).all()
            return jsonify(fhir.medication_request_resource(prescription, items))
        if resource_type == "Organization":
            organization_id = service._organization_id(resource_id)
            item = session.get(Organization, organization_id)
            return jsonify(fhir.organization_resource(item))
        if resource_type == "Practitioner":
            practitioner = session.get(Practitioner, as_uuid(resource_id, "practitioner"))
            if practitioner is None or (service.principal.organization_id is not None and
                                         practitioner.organization_id != service.principal.organization_id):
                raise APIError("PRACTITIONER_NOT_FOUND", "The practitioner could not be found.", 404)
            user = session.get(User, practitioner.user_id)
            return jsonify({"resourceType": "Practitioner", "id": str(practitioner.practitioner_id),
                            "name": [{"text": user.display_name}]})
        raise APIError("FHIR_RESOURCE_UNSUPPORTED", "This FHIR-compatible resource type is not supported.", 404)

    return api
