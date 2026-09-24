"""Server-side tenant-scoped clinical workflow services."""
from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import uuid
from typing import Any

from flask import current_app
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from .ai_adapter import ECGModelAdapter
from .db import get_session
from .models import (AIAnalysis, AIModel, AIModelVersion, AIPrediction, AuditEvent, ClinicalNote,
                     Condition, DiagnosticReport, DoctorReview, ECGFile, ECGRecord, Encounter, Feature, Organization,
                     Patient, PatientContact, PatientIdentifier, PatientOrganization, Permission,
                     Practitioner, Prescription, PrescriptionItem, ReportDocument, Role, RolePermission,
                     User, UserRole, utcnow)
from .schemas import (DiagnosisInput, EncounterRequest, FeatureRequest, PatientMatchRequest,
                      PatientRequest, PrescriptionItemInput, ReviewRequest, UserRequest)
from .security import APIError, Principal, audit
from .storage import ObjectStorage


ROLE_PERMISSIONS: dict[str, set[str]] = {
    "SUPER_ADMIN": {"*"},
    "HOSPITAL_ADMIN": {
        "hospital.read", "hospital.update", "user.read", "user.create", "user.update",
        "patient.read", "patient.create", "patient.update", "ecg.read", "report.read",
        "audit.read", "feature.read",
    },
    "DOCTOR": {
        "patient.read", "patient.create", "patient.update", "encounter.create", "encounter.read",
        "ecg.read", "ecg.upload", "ecg.analyze", "ai.read", "ai.run", "diagnosis.create",
        "diagnosis.update", "clinical_note.create", "prescription.create", "prescription.update",
        "report.create", "report.read", "report.download",
    },
    "TECHNICIAN": {
        "patient.read", "patient.create", "patient.update", "encounter.create", "encounter.read",
        "ecg.read", "ecg.upload", "ecg.analyze", "ai.read", "ai.run",
    },
    "PATIENT": {"patient.self.read", "encounter.self.read", "ecg.self.read", "report.self.read",
                "prescription.self.read"},
}

DEFAULT_FEATURES = {
    "ECG_UPLOAD": "Private ECG file upload and validation",
    "ECG_AI_ANALYSIS": "Research-only AI analysis requiring clinician review",
    "GRAD_CAM": "Model-generated Grad-CAM explanation",
    "PRESCRIPTION": "Clinician-authored prescription record",
    "PDF_REPORT": "Clinical report PDF generation",
    "FHIR": "FHIR-compatible read mappings",
    "ANALYTICS": "Role-scoped dashboard analytics",
}


def as_uuid(value: str | uuid.UUID, resource: str = "resource") -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise APIError("INVALID_IDENTIFIER", f"The {resource} identifier is invalid.", 400) from exc


def model_version_for(adapter: ECGModelAdapter, session: Session) -> AIModelVersion:
    """Register the configured, real model artifact without fabricating metadata."""
    descriptor = adapter.descriptor()
    model = session.scalar(select(AIModel).where(AIModel.model_name == descriptor.model_name))
    if model is None:
        model = AIModel(model_name=descriptor.model_name, framework="PyTorch", status="ACTIVE")
        session.add(model)
        session.flush()
    existing = session.scalar(select(AIModelVersion).where(
        AIModelVersion.model_uuid == model.model_uuid, AIModelVersion.artifact_sha256 == descriptor.sha256,
    ))
    if existing is not None:
        return existing
    version = descriptor.version
    collision = session.scalar(select(AIModelVersion).where(
        AIModelVersion.model_uuid == model.model_uuid, AIModelVersion.version == version,
    ))
    if collision is not None:
        collision.status = "RETIRED"
        version = f"{version}+{descriptor.sha256[:8]}"
    created = AIModelVersion(model_uuid=model.model_uuid, version=version,
                             # Retain a stable logical artifact reference, never the host's
                             # absolute checkpoint path in a clinical database or API payload.
                             artifact_uri=f"model://ramnv2/{descriptor.sha256}", artifact_sha256=descriptor.sha256,
                             training_dataset=None, preprocessing=descriptor.preprocessing,
                             metrics=descriptor.metrics, status="ACTIVE")
    session.add(created)
    session.flush()
    return created


def initialize_reference_data(session: Session) -> None:
    """Idempotently seed role, permission, and feature definitions (not users)."""
    roles: dict[str, Role] = {}
    for name in ROLE_PERMISSIONS:
        role = session.scalar(select(Role).where(Role.name == name))
        if role is None:
            role = Role(name=name, scope="PLATFORM" if name == "SUPER_ADMIN" else "ORGANIZATION",
                        description=f"System role: {name}")
            session.add(role)
            session.flush()
        roles[name] = role
    permission_names = sorted({permission for values in ROLE_PERMISSIONS.values() for permission in values if permission != "*"})
    permissions: dict[str, Permission] = {}
    for name in permission_names:
        item = session.scalar(select(Permission).where(Permission.name == name))
        if item is None:
            item = Permission(name=name, description=f"Platform permission: {name}")
            session.add(item)
            session.flush()
        permissions[name] = item
    for role_name, values in ROLE_PERMISSIONS.items():
        if "*" in values:
            continue
        for permission_name in values:
            found = session.scalar(select(RolePermission).where(
                RolePermission.role_id == roles[role_name].role_id,
                RolePermission.permission_id == permissions[permission_name].permission_id,
            ))
            if found is None:
                session.add(RolePermission(role_id=roles[role_name].role_id,
                                           permission_id=permissions[permission_name].permission_id))
    for key, description in DEFAULT_FEATURES.items():
        if session.scalar(select(Feature).where(Feature.key == key)) is None:
            session.add(Feature(key=key, description=description, enabled=True))
    session.commit()


class ClinicalService:
    def __init__(self, principal: Principal, session: Session | None = None):
        self.principal = principal
        self.session = session or get_session()
        self.storage: ObjectStorage = current_app.extensions["ecg_platform_storage"]
        self.adapter: ECGModelAdapter | None = current_app.extensions.get("ecg_platform_adapter")

    def _organization_id(self, explicit: str | uuid.UUID | None = None) -> uuid.UUID:
        if self.principal.organization_id is not None:
            if explicit is not None and as_uuid(explicit, "organization") != self.principal.organization_id:
                raise APIError("FORBIDDEN", "You cannot select another hospital organization.", 403)
            return self.principal.organization_id
        if not self.principal.is_super_admin:
            raise APIError("FORBIDDEN", "No hospital organization is assigned to this user.", 403)
        if explicit is None:
            raise APIError("ORGANIZATION_REQUIRED", "A super administrator must select an organization.", 400)
        organization_id = as_uuid(explicit, "organization")
        if self.session.get(Organization, organization_id) is None:
            raise APIError("ORGANIZATION_NOT_FOUND", "The organization could not be found.", 404)
        return organization_id

    def _patient(self, value: str | uuid.UUID, *, include_patient_self: bool = False) -> tuple[Patient, PatientOrganization]:
        patient_id = as_uuid(value, "patient")
        if self.principal.patient_id is not None and self.principal.patient_id != patient_id:
            raise APIError("PATIENT_NOT_FOUND", "The patient could not be found.", 404)
        if self.principal.organization_id is None:
            if not self.principal.is_super_admin:
                raise APIError("PATIENT_NOT_FOUND", "The patient could not be found.", 404)
            patient = self.session.get(Patient, patient_id)
            if patient is None:
                raise APIError("PATIENT_NOT_FOUND", "The patient could not be found.", 404)
            membership = self.session.scalar(select(PatientOrganization).where(PatientOrganization.patient_uuid == patient_id))
            if membership is None:
                raise APIError("PATIENT_NOT_FOUND", "The patient could not be found.", 404)
            return patient, membership
        row = self.session.execute(select(Patient, PatientOrganization).join(
            PatientOrganization, PatientOrganization.patient_uuid == Patient.patient_uuid
        ).where(Patient.patient_uuid == patient_id, PatientOrganization.organization_id == self.principal.organization_id,
                PatientOrganization.active.is_(True))).first()
        if row is None:
            # A 404 prevents discovery of patients in another tenant.
            raise APIError("PATIENT_NOT_FOUND", "The patient could not be found.", 404)
        return row

    def _encounter(self, value: str | uuid.UUID) -> Encounter:
        encounter_id = as_uuid(value, "encounter")
        query = select(Encounter).where(Encounter.encounter_uuid == encounter_id)
        if self.principal.organization_id is not None:
            query = query.where(Encounter.organization_id == self.principal.organization_id)
        encounter = self.session.scalar(query)
        if encounter is None:
            raise APIError("ENCOUNTER_NOT_FOUND", "The encounter could not be found.", 404)
        self._patient(encounter.patient_uuid)
        return encounter

    def _ecg(self, value: str | uuid.UUID) -> ECGRecord:
        ecg_id = as_uuid(value, "ECG")
        query = select(ECGRecord).where(ECGRecord.ecg_uuid == ecg_id)
        if self.principal.organization_id is not None:
            query = query.where(ECGRecord.organization_id == self.principal.organization_id)
        ecg = self.session.scalar(query)
        if ecg is None:
            raise APIError("ECG_NOT_FOUND", "The ECG record could not be found.", 404)
        self._patient(ecg.patient_uuid)
        return ecg

    @staticmethod
    def patient_dict(patient: Patient, membership: PatientOrganization) -> dict[str, Any]:
        return {"patient_uuid": str(patient.patient_uuid), "empi_id": patient.empi_id, "mrn": membership.mrn,
                "first_name": patient.first_name, "middle_name": patient.middle_name, "last_name": patient.last_name,
                "date_of_birth": patient.date_of_birth.isoformat(),
                "administrative_gender": patient.administrative_gender, "created_at": patient.created_at.isoformat()}

    @staticmethod
    def encounter_dict(encounter: Encounter) -> dict[str, Any]:
        return {"encounter_uuid": str(encounter.encounter_uuid), "patient_uuid": str(encounter.patient_uuid),
                "encounter_number": encounter.encounter_number, "encounter_type": encounter.encounter_type,
                "status": encounter.status, "start_time": encounter.start_time.isoformat(), "reason": encounter.reason}

    @staticmethod
    def ecg_dict(ecg: ECGRecord) -> dict[str, Any]:
        return {"ecg_uuid": str(ecg.ecg_uuid), "patient_uuid": str(ecg.patient_uuid),
                "encounter_uuid": str(ecg.encounter_uuid), "accession_number": ecg.accession_number,
                "recorded_at": ecg.recorded_at.isoformat(), "device_id": ecg.device_id,
                "sampling_rate": ecg.sampling_rate, "lead_count": ecg.lead_count,
                "duration_seconds": ecg.duration_seconds, "status": ecg.status}

    def create_organization(self, name: str, code: str) -> dict[str, Any]:
        if not self.principal.is_super_admin:
            raise APIError("FORBIDDEN", "Only a super administrator can create hospitals.", 403)
        organization = Organization(name=name, code=code.upper(), status="ACTIVE")
        self.session.add(organization)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise APIError("ORGANIZATION_EXISTS", "A hospital with that name or code already exists.", 409) from exc
        audit("HOSPITAL_CREATED", "organization", str(organization.organization_id), organization_id=organization.organization_id)
        self.session.commit()
        return {"organization_id": str(organization.organization_id), "name": organization.name, "code": organization.code,
                "status": organization.status}

    def list_organizations(self) -> list[dict[str, Any]]:
        query = select(Organization).order_by(Organization.name)
        if not self.principal.is_super_admin:
            query = query.where(Organization.organization_id == self._organization_id())
        return [{"organization_id": str(item.organization_id), "name": item.name, "code": item.code,
                 "status": item.status} for item in self.session.scalars(query)]

    def create_user(self, payload: UserRequest) -> dict[str, Any]:
        target_org = self._organization_id(payload.organization_id) if payload.organization_id else self._organization_id()
        if payload.role == "HOSPITAL_ADMIN" and not self.principal.is_super_admin:
            raise APIError("FORBIDDEN", "Only a super administrator can assign hospital administrators.", 403)
        if payload.role == "PATIENT" and not payload.patient_uuid:
            raise APIError("PATIENT_REQUIRED", "A patient account must be linked to a patient record.", 400)
        patient_uuid = as_uuid(payload.patient_uuid, "patient") if payload.patient_uuid else None
        if patient_uuid is not None:
            self._patient(patient_uuid)
        role = self.session.scalar(select(Role).where(Role.name == payload.role))
        if role is None:
            raise APIError("ROLE_NOT_FOUND", "The requested role is unavailable.", 400)
        user = User(organization_id=target_org, patient_id=patient_uuid, email=payload.email.lower(),
                    password_hash=generate_password_hash(payload.password), display_name=payload.display_name)
        self.session.add(user)
        self.session.flush()
        self.session.add(UserRole(user_id=user.user_id, role_id=role.role_id, organization_id=target_org))
        if payload.role in {"DOCTOR", "TECHNICIAN"}:
            self.session.add(Practitioner(user_id=user.user_id, organization_id=target_org))
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise APIError("USER_EXISTS", "A user with that email or patient association already exists.", 409) from exc
        audit("USER_CREATED", "user", str(user.user_id), organization_id=target_org)
        self.session.commit()
        return {"user_id": str(user.user_id), "email": user.email, "display_name": user.display_name,
                "role": payload.role, "organization_id": str(target_org)}

    def find_patient_matches(self, payload: PatientMatchRequest) -> list[dict[str, Any]]:
        org = self._organization_id()
        query = select(Patient, PatientOrganization).join(
            PatientOrganization, PatientOrganization.patient_uuid == Patient.patient_uuid
        ).where(PatientOrganization.organization_id == org, PatientOrganization.active.is_(True))
        if payload.mrn:
            query = query.where(PatientOrganization.mrn == payload.mrn)
        elif payload.first_name and payload.last_name and payload.date_of_birth:
            query = query.where(func.lower(Patient.first_name) == payload.first_name.lower(),
                                func.lower(Patient.last_name) == payload.last_name.lower(),
                                Patient.date_of_birth == payload.date_of_birth)
        elif payload.phone or payload.abha_id:
            identifier = select(PatientIdentifier.patient_uuid).where(PatientIdentifier.organization_id == org,
                                                                       PatientIdentifier.active.is_(True))
            contact = select(PatientContact.patient_uuid).where(PatientContact.active.is_(True))
            clauses = []
            if payload.phone:
                clauses.append(Patient.patient_uuid.in_(contact.where(PatientContact.value == payload.phone)))
            if payload.abha_id:
                clauses.append(Patient.patient_uuid.in_(identifier.where(PatientIdentifier.identifier_value == payload.abha_id)))
            if clauses:
                query = query.where(*clauses)
        else:
            raise APIError("MATCH_FIELDS_REQUIRED", "Provide MRN, name plus date of birth, phone, or ABHA ID.", 400)
        rows = self.session.execute(query.limit(10)).all()
        return [self.patient_dict(patient, membership) for patient, membership in rows]

    def create_patient(self, payload: PatientRequest) -> dict[str, Any]:
        org = self._organization_id()
        if payload.confirm_existing_patient_uuid:
            patient, membership = self._patient(payload.confirm_existing_patient_uuid)
            audit("PATIENT_REUSED", "patient", str(patient.patient_uuid), organization_id=org)
            self.session.commit()
            return {**self.patient_dict(patient, membership), "reused_existing_patient": True}
        matching_request = PatientMatchRequest(first_name=payload.first_name, last_name=payload.last_name,
                                               date_of_birth=payload.date_of_birth, phone=payload.phone,
                                               mrn=payload.mrn)
        matches = self.find_patient_matches(matching_request)
        if matches:
            raise APIError("POSSIBLE_PATIENT_MATCH", "A possible patient match exists. Review it and explicitly "
                           "select the existing patient or correct the registration details.", 409)
        patient = Patient(empi_id=f"EMPI-{uuid.uuid4().hex[:12].upper()}", first_name=payload.first_name,
                          middle_name=payload.middle_name, last_name=payload.last_name,
                          date_of_birth=payload.date_of_birth,
                          administrative_gender=payload.administrative_gender or payload.sex_at_birth)
        self.session.add(patient)
        self.session.flush()
        mrn = payload.mrn or f"{str(org).replace('-', '')[:6].upper()}-{uuid.uuid4().hex[:8].upper()}"
        membership = PatientOrganization(patient_uuid=patient.patient_uuid, organization_id=org, mrn=mrn)
        self.session.add(membership)
        self.session.add(PatientIdentifier(patient_uuid=patient.patient_uuid, organization_id=org,
                                           identifier_system="urn:ecg-health:mrn", identifier_type="MRN",
                                           identifier_value=mrn, assigning_organization=str(org)))
        if payload.phone:
            self.session.add(PatientContact(patient_uuid=patient.patient_uuid, contact_type="phone", value=payload.phone,
                                            use="mobile"))
        if payload.email:
            self.session.add(PatientContact(patient_uuid=patient.patient_uuid, contact_type="email", value=payload.email,
                                            use="home"))
        for identifier in payload.identifiers:
            self.session.add(PatientIdentifier(patient_uuid=patient.patient_uuid, organization_id=org,
                                               identifier_system=identifier.identifier_system,
                                               identifier_type=identifier.identifier_type,
                                               identifier_value=identifier.identifier_value,
                                               assigning_organization=identifier.assigning_organization))
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise APIError("IDENTIFIER_EXISTS", "An identifier or MRN is already active for this hospital.", 409) from exc
        audit("PATIENT_CREATED", "patient", str(patient.patient_uuid), organization_id=org)
        self.session.commit()
        return self.patient_dict(patient, membership)

    def list_patients(self, search: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        org = self._organization_id()
        query = select(Patient, PatientOrganization).join(
            PatientOrganization, PatientOrganization.patient_uuid == Patient.patient_uuid
        ).where(PatientOrganization.organization_id == org, PatientOrganization.active.is_(True))
        if self.principal.patient_id is not None:
            query = query.where(Patient.patient_uuid == self.principal.patient_id)
        if search:
            term = f"%{search.lower()}%"
            query = query.where((func.lower(Patient.first_name).like(term)) | (func.lower(Patient.last_name).like(term)) |
                                (func.lower(PatientOrganization.mrn).like(term)) | (func.lower(Patient.empi_id).like(term)))
        rows = self.session.execute(query.order_by(Patient.last_name, Patient.first_name).limit(min(max(limit, 1), 100))).all()
        return [self.patient_dict(patient, membership) for patient, membership in rows]

    def get_patient(self, patient_uuid: str) -> dict[str, Any]:
        patient, membership = self._patient(patient_uuid)
        audit("PATIENT_VIEWED", "patient", str(patient.patient_uuid), organization_id=membership.organization_id)
        self.session.commit()
        return self.patient_dict(patient, membership)

    def create_encounter(self, payload: EncounterRequest) -> dict[str, Any]:
        org = self._organization_id()
        patient, _ = self._patient(payload.patient_uuid)
        encounter = Encounter(patient_uuid=patient.patient_uuid, organization_id=org,
                              encounter_number=f"ENC-{uuid.uuid4().hex[:12].upper()}",
                              encounter_type=payload.encounter_type, reason=payload.reason)
        self.session.add(encounter)
        self.session.commit()
        audit("ENCOUNTER_CREATED", "encounter", str(encounter.encounter_uuid), organization_id=org)
        self.session.commit()
        return self.encounter_dict(encounter)

    def list_encounters(self, patient_uuid: str) -> list[dict[str, Any]]:
        patient, _ = self._patient(patient_uuid)
        query = select(Encounter).where(Encounter.patient_uuid == patient.patient_uuid)
        if self.principal.organization_id is not None:
            query = query.where(Encounter.organization_id == self.principal.organization_id)
        return [self.encounter_dict(row) for row in self.session.scalars(query.order_by(Encounter.start_time.desc()))]

    def list_ecgs(self, patient_uuid: str) -> list[dict[str, Any]]:
        patient, _ = self._patient(patient_uuid)
        query = select(ECGRecord).where(ECGRecord.patient_uuid == patient.patient_uuid)
        if self.principal.organization_id is not None:
            query = query.where(ECGRecord.organization_id == self.principal.organization_id)
        records = []
        for ecg in self.session.scalars(query.order_by(ECGRecord.recorded_at.desc())):
            file = self._file_for_ecg(ecg)
            records.append({**self.ecg_dict(ecg), "filename": file.original_filename, "content_type": file.content_type,
                            "size_bytes": file.size_bytes})
        return records

    def upload_ecg(self, encounter_uuid: str, payload: bytes, filename: str, device_id: str | None = None) -> dict[str, Any]:
        if self.adapter is None:
            raise APIError("MODEL_CONFIGURATION_ERROR", "No compatible ECG model is configured for file validation.", 503)
        if Path(filename).suffix.lower() not in {".mat", ".csv"}:
            raise APIError("UNSUPPORTED_ECG_FORMAT", "Only .mat and .csv ECG waveforms are accepted.", 400)
        if not payload:
            raise APIError("EMPTY_FILE", "The ECG upload is empty.", 400)
        if len(payload) > current_app.config["MAX_CONTENT_LENGTH"]:
            raise APIError("FILE_TOO_LARGE", "The ECG upload exceeds the configured file limit.", 413)
        self.adapter.validate(payload, filename)
        encounter = self._encounter(encounter_uuid)
        org = self._organization_id()
        ecg = ECGRecord(patient_uuid=encounter.patient_uuid, encounter_uuid=encounter.encounter_uuid,
                        organization_id=org, accession_number=f"ECG-{uuid.uuid4().hex[:12].upper()}",
                        device_id=device_id, status="VALIDATING")
        self.session.add(ecg)
        self.session.flush()
        suffix = Path(filename).suffix.lower()
        object_key = f"organizations/{org}/ecgs/{ecg.ecg_uuid}/original/{uuid.uuid4().hex}{suffix}"
        content_type = "application/x-matlab-data" if suffix == ".mat" else "text/csv"
        stored = self.storage.put_bytes(object_key, payload, content_type)
        self.session.add(ECGFile(ecg_uuid=ecg.ecg_uuid, storage_uri=stored.uri, object_key=stored.object_key,
                                 sha256=stored.sha256, content_type=stored.content_type,
                                 original_filename=Path(filename).name, size_bytes=stored.size_bytes))
        ecg.status = "READY"
        self.session.commit()
        audit("ECG_UPLOADED", "ecg", str(ecg.ecg_uuid), organization_id=org,
              metadata={"format": suffix.removeprefix("."), "size_bytes": stored.size_bytes})
        self.session.commit()
        return self.ecg_dict(ecg)

    def _file_for_ecg(self, ecg: ECGRecord) -> ECGFile:
        file = self.session.scalar(select(ECGFile).where(ECGFile.ecg_uuid == ecg.ecg_uuid,
                                                         ECGFile.purpose == "ORIGINAL"))
        if file is None:
            raise APIError("ECG_FILE_NOT_FOUND", "The ECG waveform asset is unavailable.", 404)
        return file

    def get_ecg(self, ecg_uuid: str) -> dict[str, Any]:
        ecg = self._ecg(ecg_uuid)
        file = self._file_for_ecg(ecg)
        audit("ECG_VIEWED", "ecg", str(ecg.ecg_uuid), organization_id=ecg.organization_id)
        self.session.commit()
        return {**self.ecg_dict(ecg), "file": {"filename": file.original_filename, "content_type": file.content_type,
                                                 "size_bytes": file.size_bytes, "sha256": file.sha256}}

    def download_ecg(self, ecg_uuid: str) -> tuple[bytes, ECGFile]:
        ecg = self._ecg(ecg_uuid)
        file = self._file_for_ecg(ecg)
        data = self.storage.get_bytes(file.object_key)
        if hashlib.sha256(data).hexdigest() != file.sha256:
            raise APIError("ASSET_INTEGRITY_ERROR", "The stored ECG asset failed an integrity check.", 500)
        audit("ECG_VIEWED", "ecg_file", str(file.ecg_file_id), organization_id=ecg.organization_id)
        self.session.commit()
        return data, file

    def _raw_ecg_waveform(self, ecg: ECGRecord) -> tuple[Any, ECGFile]:
        if self.adapter is None:
            raise APIError("MODEL_CONFIGURATION_ERROR", "No compatible ECG model is configured for waveform validation.", 503)
        file = self._file_for_ecg(ecg)
        data = self.storage.get_bytes(file.object_key)
        if hashlib.sha256(data).hexdigest() != file.sha256:
            raise APIError("ASSET_INTEGRITY_ERROR", "The stored ECG asset failed an integrity check.", 500)
        return self.adapter.validate(data, file.original_filename), file

    def waveform_data(self, ecg_uuid: str) -> dict[str, Any]:
        """Return the actual authorized single-lead data for the web viewer."""
        ecg = self._ecg(ecg_uuid)
        waveform, _file = self._raw_ecg_waveform(ecg)
        audit("ECG_WAVEFORM_VIEWED", "ecg", str(ecg.ecg_uuid), organization_id=ecg.organization_id,
              metadata={"sampling_rate_hz": ecg.sampling_rate, "sample_count": int(waveform.size)})
        self.session.commit()
        return {"ecg_uuid": str(ecg.ecg_uuid), "sampling_rate_hz": ecg.sampling_rate,
                "duration_seconds": ecg.duration_seconds, "lead_count": ecg.lead_count,
                "amplitude_units": "dataset units", "samples": waveform.astype(float).tolist()}

    def waveform_png(self, ecg: ECGRecord) -> bytes:
        """Create a non-persistent waveform rendering for an authorized report."""
        waveform, _file = self._raw_ecg_waveform(ecg)
        assert self.adapter is not None
        return self.adapter.render_waveform(waveform)

    def create_analysis(self, ecg_uuid: str) -> AIAnalysis:
        ecg = self._ecg(ecg_uuid)
        if self.adapter is None:
            raise APIError("MODEL_CONFIGURATION_ERROR", "No compatible ECG model artifact is configured.", 503)
        if ecg.status not in {"READY", "ANALYZED", "REVIEW_REQUIRED", "REVIEWED"}:
            raise APIError("ECG_NOT_READY", "This ECG is not ready for analysis.", 409)
        file = self._file_for_ecg(ecg)
        version = model_version_for(self.adapter, self.session)
        analysis = AIAnalysis(ecg_uuid=ecg.ecg_uuid, organization_id=ecg.organization_id,
                              model_version_uuid=version.model_version_uuid, status="QUEUED",
                              input_sha256=file.sha256)
        self.session.add(analysis)
        ecg.status = "PROCESSING"
        self.session.commit()
        audit("ECG_ANALYSIS_STARTED", "ai_analysis", str(analysis.ai_analysis_uuid), organization_id=ecg.organization_id,
              metadata={"model_version": version.version})
        self.session.commit()
        return analysis

    def run_analysis(self, analysis_uuid: str | uuid.UUID) -> AIAnalysis:
        analysis_id = as_uuid(analysis_uuid, "analysis")
        analysis = self.session.get(AIAnalysis, analysis_id)
        if analysis is None:
            raise APIError("ANALYSIS_NOT_FOUND", "The analysis could not be found.", 404)
        ecg = self._ecg(analysis.ecg_uuid)
        if self.adapter is None:
            raise APIError("MODEL_CONFIGURATION_ERROR", "No compatible ECG model artifact is configured.", 503)
        file = self._file_for_ecg(ecg)
        analysis.status = "PROCESSING"
        self.session.commit()
        try:
            raw = self.storage.get_bytes(file.object_key)
            if hashlib.sha256(raw).hexdigest() != file.sha256:
                raise ValueError("Stored waveform integrity check failed.")
            result = self.adapter.predict(raw, file.original_filename, include_explanation=True)
            if result.explanation_png:
                key = f"organizations/{ecg.organization_id}/ecgs/{ecg.ecg_uuid}/analyses/{analysis.ai_analysis_uuid}/gradcam.png"
                explanation = self.storage.put_bytes(key, result.explanation_png, "image/png")
                analysis.explanation_uri = explanation.uri
                analysis.explanation_sha256 = explanation.sha256
            report = result.report
            analysis.predicted_label = report["label"]
            analysis.predicted_label_name = report["label_name"]
            analysis.confidence = report["confidence"]
            analysis.processing_time_ms = int(report["duration_ms"])
            analysis.inference_timestamp = utcnow()
            analysis.raw_output = report
            analysis.status = "COMPLETED"
            for rank, item in enumerate(report["top_predictions"], start=1):
                self.session.add(AIPrediction(ai_analysis_uuid=analysis.ai_analysis_uuid, rank=rank,
                                              label=item["label"], label_name=item["label_name"],
                                              score=item["probability"]))
            ecg.status = "REVIEW_REQUIRED"
            self.session.commit()
            audit("ECG_ANALYSIS_COMPLETED", "ai_analysis", str(analysis.ai_analysis_uuid),
                  organization_id=ecg.organization_id,
                  metadata={"model_version_id": str(analysis.model_version_uuid)})
            self.session.commit()
            return analysis
        except Exception:
            analysis.status = "FAILED"
            analysis.error_message = "Inference failed. The stored ECG was retained for authorized investigation."
            ecg.status = "FAILED"
            self.session.commit()
            audit("ECG_ANALYSIS_FAILED", "ai_analysis", str(analysis.ai_analysis_uuid), success=False,
                  organization_id=ecg.organization_id)
            self.session.commit()
            raise

    def analysis_dict(self, analysis: AIAnalysis) -> dict[str, Any]:
        predictions = self.session.scalars(select(AIPrediction).where(
            AIPrediction.ai_analysis_uuid == analysis.ai_analysis_uuid
        ).order_by(AIPrediction.rank)).all()
        version = self.session.get(AIModelVersion, analysis.model_version_uuid)
        return {"ai_analysis_uuid": str(analysis.ai_analysis_uuid), "ecg_uuid": str(analysis.ecg_uuid),
                "status": analysis.status, "prediction": analysis.predicted_label,
                "prediction_name": analysis.predicted_label_name, "confidence": analysis.confidence,
                "model_version": version.version if version else None,
                "model_artifact_sha256": version.artifact_sha256 if version else None,
                "inference_timestamp": analysis.inference_timestamp.isoformat() if analysis.inference_timestamp else None,
                "processing_time_ms": analysis.processing_time_ms,
                "top_predictions": [{"rank": row.rank, "label": row.label, "label_name": row.label_name,
                                     "score": row.score} for row in predictions],
                "has_explanation": bool(analysis.explanation_uri),
                "safety": "AI-generated research/clinical-decision-support output. It requires qualified clinician "
                          "review and is not an autonomous diagnosis or treatment recommendation."}

    def latest_analysis(self, ecg_uuid: str) -> dict[str, Any]:
        ecg = self._ecg(ecg_uuid)
        analysis = self.session.scalar(select(AIAnalysis).where(AIAnalysis.ecg_uuid == ecg.ecg_uuid)
                                       .order_by(AIAnalysis.created_at.desc()))
        if analysis is None:
            raise APIError("ANALYSIS_NOT_FOUND", "No AI analysis exists for this ECG.", 404)
        audit("AI_RESULT_VIEWED", "ai_analysis", str(analysis.ai_analysis_uuid), organization_id=ecg.organization_id)
        self.session.commit()
        return self.analysis_dict(analysis)

    def explanation_bytes(self, ecg_uuid: str) -> bytes:
        ecg = self._ecg(ecg_uuid)
        analysis = self.session.scalar(select(AIAnalysis).where(AIAnalysis.ecg_uuid == ecg.ecg_uuid,
                                                                  AIAnalysis.status == "COMPLETED")
                                       .order_by(AIAnalysis.created_at.desc()))
        if analysis is None or not analysis.explanation_uri:
            raise APIError("EXPLANATION_NOT_FOUND", "No model-generated explanation exists for this ECG.", 404)
        if analysis.explanation_uri.startswith("local://"):
            key = analysis.explanation_uri.removeprefix("local://")
        elif analysis.explanation_uri.startswith("s3://"):
            key = analysis.explanation_uri.split("/", 3)[-1]
        else:
            raise APIError("EXPLANATION_NOT_FOUND", "The explanation storage reference is unsupported.", 404)
        payload = self.storage.get_bytes(key)
        if analysis.explanation_sha256 and hashlib.sha256(payload).hexdigest() != analysis.explanation_sha256:
            raise APIError("ASSET_INTEGRITY_ERROR", "The explanation asset failed an integrity check.", 500)
        audit("AI_RESULT_VIEWED", "explanation", str(analysis.ai_analysis_uuid), organization_id=ecg.organization_id)
        self.session.commit()
        return payload

    def save_review(self, ecg_uuid: str, payload: ReviewRequest) -> dict[str, Any]:
        ecg = self._ecg(ecg_uuid)
        analysis = self.session.scalar(select(AIAnalysis).where(AIAnalysis.ecg_uuid == ecg.ecg_uuid,
                                                                  AIAnalysis.status == "COMPLETED")
                                       .order_by(AIAnalysis.created_at.desc()))
        if analysis is None:
            raise APIError("ANALYSIS_REQUIRED", "A completed AI analysis is required before a clinician review.", 409)
        review = DoctorReview(ecg_uuid=ecg.ecg_uuid, ai_analysis_uuid=analysis.ai_analysis_uuid,
                              organization_id=ecg.organization_id, reviewed_by=self.principal.user_id,
                              review_status=payload.review_status, assessment=payload.doctor_assessment)
        self.session.add(review)
        condition: Condition | None = None
        if payload.diagnosis:
            condition = Condition(patient_uuid=ecg.patient_uuid, encounter_uuid=ecg.encounter_uuid,
                                  organization_id=ecg.organization_id, display=payload.diagnosis.display,
                                  code_system=payload.diagnosis.code_system, code=payload.diagnosis.code,
                                  recorded_by=self.principal.user_id)
            self.session.add(condition)
        if payload.clinical_note:
            self.session.add(ClinicalNote(patient_uuid=ecg.patient_uuid, encounter_uuid=ecg.encounter_uuid,
                                          organization_id=ecg.organization_id, author_id=self.principal.user_id,
                                          note_type="ECG_REVIEW", body=payload.clinical_note))
        prescription: Prescription | None = None
        if payload.prescription_items:
            prescription = Prescription(patient_uuid=ecg.patient_uuid, encounter_uuid=ecg.encounter_uuid,
                                        organization_id=ecg.organization_id, prescriber_id=self.principal.user_id,
                                        status="DRAFT", instructions=payload.prescription_instructions)
            self.session.add(prescription)
            self.session.flush()
            for item in payload.prescription_items:
                self.session.add(PrescriptionItem(prescription_uuid=prescription.prescription_uuid,
                                                  medicine=item.medicine, dose=item.dose, route=item.route,
                                                  frequency=item.frequency, duration=item.duration,
                                                  instructions=item.instructions))
        report = self.session.scalar(select(DiagnosticReport).where(DiagnosticReport.ecg_uuid == ecg.ecg_uuid))
        if report is None:
            report = DiagnosticReport(ecg_uuid=ecg.ecg_uuid, organization_id=ecg.organization_id,
                                      author_id=self.principal.user_id)
            self.session.add(report)
        report.conclusion = payload.doctor_assessment
        report.status = "PRELIMINARY"
        ecg.status = "REVIEWED"
        self.session.commit()
        audit("DIAGNOSIS_CREATED" if condition else "ECG_REVIEWED", "ecg", str(ecg.ecg_uuid),
              organization_id=ecg.organization_id)
        if prescription:
            audit("PRESCRIPTION_CREATED", "prescription", str(prescription.prescription_uuid),
                  organization_id=ecg.organization_id)
        self.session.commit()
        return {"doctor_review_uuid": str(review.doctor_review_uuid), "ecg_uuid": str(ecg.ecg_uuid), "status": ecg.status,
                "doctor_assessment": report.conclusion,
                "condition_uuid": str(condition.condition_uuid) if condition else None,
                "prescription_uuid": str(prescription.prescription_uuid) if prescription else None,
                "report_uuid": str(report.report_uuid),
                "safety": "This assessment and any prescription items are clinician-authored. They are not generated "
                          "by the ECG model."}

    def list_analyses(self, patient_uuid: str) -> list[dict[str, Any]]:
        patient, _ = self._patient(patient_uuid)
        query = (select(AIAnalysis).join(ECGRecord, ECGRecord.ecg_uuid == AIAnalysis.ecg_uuid)
                 .where(ECGRecord.patient_uuid == patient.patient_uuid))
        if self.principal.organization_id is not None:
            query = query.where(AIAnalysis.organization_id == self.principal.organization_id)
        return [self.analysis_dict(item) for item in self.session.scalars(query.order_by(AIAnalysis.created_at.desc()))]

    def list_reviews(self, patient_uuid: str) -> list[dict[str, Any]]:
        patient, _ = self._patient(patient_uuid)
        query = (select(DoctorReview, User).join(ECGRecord, ECGRecord.ecg_uuid == DoctorReview.ecg_uuid)
                 .join(User, User.user_id == DoctorReview.reviewed_by)
                 .where(ECGRecord.patient_uuid == patient.patient_uuid))
        if self.principal.organization_id is not None:
            query = query.where(DoctorReview.organization_id == self.principal.organization_id)
        return [{"doctor_review_uuid": str(review.doctor_review_uuid), "ai_analysis_uuid": str(review.ai_analysis_uuid),
                 "ecg_uuid": str(review.ecg_uuid), "status": review.review_status, "assessment": review.assessment,
                 "reviewed_at": review.reviewed_at.isoformat(), "reviewer": user.display_name}
                for review, user in self.session.execute(query.order_by(DoctorReview.reviewed_at.desc())).all()]

    def _report_context(self, report_uuid: str | uuid.UUID) -> tuple[DiagnosticReport, ECGRecord, Patient, PatientOrganization]:
        report_id = as_uuid(report_uuid, "report")
        query = select(DiagnosticReport).where(DiagnosticReport.report_uuid == report_id)
        if self.principal.organization_id is not None:
            query = query.where(DiagnosticReport.organization_id == self.principal.organization_id)
        report = self.session.scalar(query)
        if report is None:
            raise APIError("REPORT_NOT_FOUND", "The report could not be found.", 404)
        ecg = self._ecg(report.ecg_uuid)
        patient, membership = self._patient(ecg.patient_uuid)
        return report, ecg, patient, membership

    def list_reports(self, patient_uuid: str) -> list[dict[str, Any]]:
        patient, _ = self._patient(patient_uuid)
        query = (select(DiagnosticReport).join(ECGRecord, ECGRecord.ecg_uuid == DiagnosticReport.ecg_uuid)
                 .where(ECGRecord.patient_uuid == patient.patient_uuid))
        if self.principal.organization_id is not None:
            query = query.where(DiagnosticReport.organization_id == self.principal.organization_id)
        output = []
        for report in self.session.scalars(query.order_by(DiagnosticReport.created_at.desc())):
            document = self.session.scalar(select(ReportDocument).where(ReportDocument.report_uuid == report.report_uuid)
                                           .order_by(ReportDocument.created_at.desc()))
            output.append({"report_uuid": str(report.report_uuid), "ecg_uuid": str(report.ecg_uuid),
                           "status": report.status, "created_at": report.created_at.isoformat(),
                           "issued_at": report.issued_at.isoformat() if report.issued_at else None,
                           "has_pdf": document is not None})
        return output

    def generate_report_pdf(self, report_uuid: str) -> dict[str, Any]:
        from .reports import build_ecg_report_pdf

        report, ecg, patient, membership = self._report_context(report_uuid)
        organization = self.session.get(Organization, ecg.organization_id)
        analysis = self.session.scalar(select(AIAnalysis).where(AIAnalysis.ecg_uuid == ecg.ecg_uuid,
                                                                  AIAnalysis.status == "COMPLETED")
                                       .order_by(AIAnalysis.created_at.desc()))
        version = self.session.get(AIModelVersion, analysis.model_version_uuid) if analysis else None
        review = self.session.scalar(select(DoctorReview).where(DoctorReview.ecg_uuid == ecg.ecg_uuid)
                                     .order_by(DoctorReview.reviewed_at.desc()))
        condition = self.session.scalar(select(Condition).where(Condition.encounter_uuid == ecg.encounter_uuid)
                                        .order_by(Condition.created_at.desc()))
        prescription = self.session.scalar(select(Prescription).where(Prescription.encounter_uuid == ecg.encounter_uuid)
                                          .order_by(Prescription.created_at.desc()))
        items = self.session.scalars(select(PrescriptionItem).where(
            PrescriptionItem.prescription_uuid == prescription.prescription_uuid
        )).all() if prescription else []
        author = self.session.get(User, report.author_id)
        waveform_png = self.waveform_png(ecg)
        pdf = build_ecg_report_pdf(
            hospital=organization.name if organization else "Hospital not recorded",
            patient_name=" ".join(filter(None, [patient.first_name, patient.middle_name, patient.last_name])),
            empi_id=patient.empi_id, mrn=membership.mrn, encounter_number=ecg.encounter_uuid.hex,
            accession_number=ecg.accession_number, clinician=author.display_name if author else "Not recorded",
            ai_prediction=analysis.predicted_label_name if analysis else None,
            ai_score=analysis.confidence if analysis else None, model_version=version.version if version else None,
            clinician_assessment=review.assessment if review else report.conclusion,
            diagnosis=condition.display if condition else None,
            prescription_summary=[" | ".join(filter(None, [item.medicine, item.dose, item.route, item.frequency,
                                                            item.duration, item.instructions])) for item in items],
            waveform_png=waveform_png,
        )
        key = f"organizations/{ecg.organization_id}/reports/{report.report_uuid}/{uuid.uuid4().hex}.pdf"
        stored = self.storage.put_bytes(key, pdf, "application/pdf")
        document = ReportDocument(report_uuid=report.report_uuid, storage_uri=stored.uri, object_key=stored.object_key,
                                  sha256=stored.sha256, content_type=stored.content_type, size_bytes=stored.size_bytes)
        self.session.add(document)
        report.issued_at = utcnow()
        report.status = "FINAL"
        self.session.commit()
        audit("REPORT_CREATED", "report", str(report.report_uuid), organization_id=ecg.organization_id)
        self.session.commit()
        return {"report_uuid": str(report.report_uuid), "report_document_uuid": str(document.report_document_uuid),
                "status": report.status, "content_type": document.content_type, "size_bytes": document.size_bytes}

    def download_report(self, report_uuid: str) -> tuple[bytes, ReportDocument]:
        report, ecg, _patient, _membership = self._report_context(report_uuid)
        document = self.session.scalar(select(ReportDocument).where(ReportDocument.report_uuid == report.report_uuid)
                                       .order_by(ReportDocument.created_at.desc()))
        if document is None:
            raise APIError("REPORT_DOCUMENT_NOT_FOUND", "No PDF has been generated for this report.", 404)
        payload = self.storage.get_bytes(document.object_key)
        if hashlib.sha256(payload).hexdigest() != document.sha256:
            raise APIError("ASSET_INTEGRITY_ERROR", "The report asset failed an integrity check.", 500)
        audit("REPORT_DOWNLOADED", "report", str(report.report_uuid), organization_id=ecg.organization_id)
        self.session.commit()
        return payload, document

    def dashboard(self) -> dict[str, Any]:
        org = self._organization_id()
        if self.principal.patient_id is not None:
            # A patient account never receives hospital-wide analytics, even
            # aggregate counts. It can only see totals for its linked record.
            patient, _membership = self._patient(self.principal.patient_id)
            return {"organization_id": str(org), "metrics": {
                "my_encounters": self.session.scalar(select(func.count()).select_from(Encounter).where(
                    Encounter.organization_id == org, Encounter.patient_uuid == patient.patient_uuid)) or 0,
                "my_ecgs": self.session.scalar(select(func.count()).select_from(ECGRecord).where(
                    ECGRecord.organization_id == org, ECGRecord.patient_uuid == patient.patient_uuid)) or 0,
                "my_reports": self.session.scalar(select(func.count()).select_from(DiagnosticReport).join(
                    ECGRecord, ECGRecord.ecg_uuid == DiagnosticReport.ecg_uuid).where(
                    DiagnosticReport.organization_id == org, ECGRecord.patient_uuid == patient.patient_uuid)) or 0,
            }, "safety": "Counts are limited to the authenticated patient's own authorized records."}
        role_counts = {
            "patients": self.session.scalar(select(func.count()).select_from(PatientOrganization).where(
                PatientOrganization.organization_id == org, PatientOrganization.active.is_(True))) or 0,
            "ecgs": self.session.scalar(select(func.count()).select_from(ECGRecord).where(
                ECGRecord.organization_id == org)) or 0,
            "pending_reviews": self.session.scalar(select(func.count()).select_from(ECGRecord).where(
                ECGRecord.organization_id == org, ECGRecord.status == "REVIEW_REQUIRED")) or 0,
            "reports": self.session.scalar(select(func.count()).select_from(DiagnosticReport).where(
                DiagnosticReport.organization_id == org)) or 0,
        }
        return {"organization_id": str(org), "metrics": role_counts,
                "safety": "Counts are organization-scoped. AI output remains separate from clinician diagnosis."}

    def list_features(self) -> list[dict[str, Any]]:
        return [{"feature_uuid": str(item.feature_uuid), "key": item.key, "description": item.description,
                 "enabled": item.enabled} for item in self.session.scalars(select(Feature).order_by(Feature.key))]

    def create_feature(self, payload: FeatureRequest) -> dict[str, Any]:
        item = Feature(key=payload.key, description=payload.description, enabled=payload.enabled)
        self.session.add(item)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise APIError("FEATURE_EXISTS", "A feature with that key already exists.", 409) from exc
        audit("FEATURE_CHANGED", "feature", str(item.feature_uuid))
        self.session.commit()
        return {"feature_uuid": str(item.feature_uuid), "key": item.key, "enabled": item.enabled}

    def audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        query = select(AuditEvent).order_by(AuditEvent.timestamp.desc()).limit(min(max(limit, 1), 200))
        if not self.principal.is_super_admin:
            query = query.where(AuditEvent.organization_id == self._organization_id())
        return [{"audit_id": str(row.audit_id), "timestamp": row.timestamp.isoformat(), "action": row.action,
                 "resource_type": row.resource_type, "resource_id": row.resource_id, "success": row.success,
                 "request_id": row.request_id} for row in self.session.scalars(query)]
