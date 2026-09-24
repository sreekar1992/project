"""Tenant-aware relational model for the ECG clinical workflow.

Rows intentionally store opaque storage URIs and checksums rather than ECG
payload bytes.  The schema is usable with SQLite in local development and
PostgreSQL in the Docker/Kubernetes deployment.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import uuid

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey,
                        Index, Integer, JSON, LargeBinary, String, Text, UniqueConstraint,
                        Uuid, func)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                  onupdate=utcnow, nullable=False)


class Organization(Base, Timestamped):
    __tablename__ = "organizations"
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)


class OrganizationAddress(Base, Timestamped):
    __tablename__ = "organization_addresses"
    address_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.organization_id"), nullable=False,
                                                        index=True)
    line1: Mapped[str] = mapped_column(String(200), nullable=False)
    line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(32))
    country: Mapped[str] = mapped_column(String(2), default="IN", nullable=False)


class User(Base, Timestamped):
    __tablename__ = "users"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.organization_id"), index=True)
    patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patients.patient_uuid"), unique=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Role(Base, Timestamped):
    __tablename__ = "roles"
    role_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scope: Mapped[str] = mapped_column(String(32), default="ORGANIZATION", nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class Permission(Base, Timestamped):
    __tablename__ = "permissions"
    permission_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class UserRole(Base, Timestamped):
    __tablename__ = "user_roles"
    user_role_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"), nullable=False, index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.role_id"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.organization_id"), index=True)
    __table_args__ = (UniqueConstraint("user_id", "role_id", "organization_id", name="uq_user_role_scope"),)


class RolePermission(Base, Timestamped):
    __tablename__ = "role_permissions"
    role_permission_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.role_id"), nullable=False, index=True)
    permission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("permissions.permission_id"), nullable=False,
                                                     index=True)
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),)


class Patient(Base, Timestamped):
    __tablename__ = "patients"
    patient_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    empi_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    middle_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    administrative_gender: Mapped[str | None] = mapped_column(String(32))
    deceased: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deceased_date: Mapped[date | None] = mapped_column(Date)
    merged_into_patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patients.patient_uuid"))


class PatientOrganization(Base, Timestamped):
    __tablename__ = "patient_organizations"
    patient_organization_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True,
                                                                default=uuid.uuid4)
    patient_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.patient_uuid"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.organization_id"), nullable=False,
                                                        index=True)
    mrn: Mapped[str] = mapped_column(String(80), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (
        UniqueConstraint("organization_id", "mrn", name="uq_patient_organization_mrn"),
        UniqueConstraint("patient_uuid", "organization_id", name="uq_patient_organization_membership"),
    )


class PatientIdentifier(Base, Timestamped):
    __tablename__ = "patient_identifiers"
    identifier_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.patient_uuid"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.organization_id"), index=True)
    identifier_system: Mapped[str] = mapped_column(String(200), nullable=False)
    identifier_type: Mapped[str] = mapped_column(String(64), nullable=False)
    identifier_value: Mapped[str] = mapped_column(String(200), nullable=False)
    assigning_organization: Mapped[str | None] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint("organization_id", "identifier_system", "identifier_value",
                                       name="uq_patient_identifier_value"),)


class PatientContact(Base, Timestamped):
    __tablename__ = "patient_contacts"
    contact_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.patient_uuid"), nullable=False, index=True)
    contact_type: Mapped[str] = mapped_column(String(48), nullable=False)
    value: Mapped[str] = mapped_column(String(200), nullable=False)
    use: Mapped[str | None] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PatientAddress(Base, Timestamped):
    __tablename__ = "patient_addresses"
    address_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.patient_uuid"), nullable=False, index=True)
    line1: Mapped[str] = mapped_column(String(200), nullable=False)
    line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(32))
    country: Mapped[str | None] = mapped_column(String(2))


class PatientMergeHistory(Base, Timestamped):
    __tablename__ = "patient_merge_history"
    merge_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_patient_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.patient_uuid"), nullable=False)
    target_patient_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.patient_uuid"), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.organization_id"), nullable=False,
                                                        index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    performed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("source_patient_uuid <> target_patient_uuid", name="ck_patient_merge_distinct"),)


class Practitioner(Base, Timestamped):
    __tablename__ = "practitioners"
    practitioner_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"), unique=True, nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.organization_id"), nullable=False,
                                                        index=True)
    registration_number: Mapped[str | None] = mapped_column(String(100))
    specialty: Mapped[str | None] = mapped_column(String(100))


class PractitionerRole(Base, Timestamped):
    __tablename__ = "practitioner_roles"
    practitioner_role_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    practitioner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("practitioners.practitioner_id"), nullable=False,
                                                        index=True)
    role_code: Mapped[str] = mapped_column(String(64), nullable=False)


class Encounter(Base, Timestamped):
    __tablename__ = "encounters"
    encounter_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.patient_uuid"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.organization_id"), nullable=False,
                                                        index=True)
    encounter_number: Mapped[str] = mapped_column(String(80), nullable=False)
    encounter_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="OPEN", nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attending_practitioner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("practitioners.practitioner_id"))
    reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("organization_id", "encounter_number", name="uq_encounter_number"),)


class ECGRecord(Base, Timestamped):
    __tablename__ = "ecg_records"
    ecg_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.patient_uuid"), nullable=False, index=True)
    encounter_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("encounters.encounter_uuid"), nullable=False,
                                                       index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.organization_id"), nullable=False,
                                                        index=True)
    accession_number: Mapped[str] = mapped_column(String(80), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(100))
    sampling_rate: Mapped[float] = mapped_column(Float, default=360.0, nullable=False)
    lead_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="UPLOADED", nullable=False)
    __table_args__ = (UniqueConstraint("organization_id", "accession_number", name="uq_ecg_accession"),
                      Index("ix_ecg_recorded_at", "organization_id", "recorded_at"))


class ECGFile(Base, Timestamped):
    __tablename__ = "ecg_files"
    ecg_file_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ecg_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("ecg_records.ecg_uuid"), nullable=False, index=True)
    storage_uri: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(48), default="ORIGINAL", nullable=False)


class AIModel(Base, Timestamped):
    __tablename__ = "ai_models"
    model_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    framework: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)


class AIModelVersion(Base, Timestamped):
    __tablename__ = "ai_model_versions"
    model_version_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_models.model_uuid"), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    artifact_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    training_dataset: Mapped[str | None] = mapped_column(String(500))
    preprocessing: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    __table_args__ = (UniqueConstraint("model_uuid", "version", name="uq_ai_model_version"),)


class AIAnalysis(Base, Timestamped):
    __tablename__ = "ai_analyses"
    ai_analysis_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ecg_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("ecg_records.ecg_uuid"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.organization_id"), nullable=False,
                                                        index=True)
    model_version_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_model_versions.model_version_uuid"),
                                                           nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", nullable=False)
    predicted_label: Mapped[str | None] = mapped_column(String(200))
    predicted_label_name: Mapped[str | None] = mapped_column(String(200))
    confidence: Mapped[float | None] = mapped_column(Float)
    inference_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_time_ms: Mapped[int | None] = mapped_column(Integer)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_output: Mapped[dict | None] = mapped_column(JSON)
    explanation_uri: Mapped[str | None] = mapped_column(String(1024))
    explanation_sha256: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(500))


class AIPrediction(Base, Timestamped):
    __tablename__ = "ai_predictions"
    ai_prediction_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ai_analysis_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_analyses.ai_analysis_uuid"), nullable=False,
                                                         index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    label_name: Mapped[str] = mapped_column(String(200), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    __table_args__ = (UniqueConstraint("ai_analysis_uuid", "rank", name="uq_ai_prediction_rank"),)


class DoctorReview(Base, Timestamped):
    __tablename__ = "doctor_reviews"
    doctor_review_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ecg_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("ecg_records.ecg_uuid"), nullable=False, index=True)
    ai_analysis_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_analyses.ai_analysis_uuid"), nullable=False,
                                                         index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.organization_id"), nullable=False,
                                                        index=True)
    reviewed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    review_status: Mapped[str] = mapped_column(String(64), default="REVIEWED", nullable=False)
    assessment: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Condition(Base, Timestamped):
    __tablename__ = "conditions"
    condition_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.patient_uuid"), nullable=False, index=True)
    encounter_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("encounters.encounter_uuid"), nullable=False,
                                                       index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.organization_id"), nullable=False,
                                                        index=True)
    code_system: Mapped[str | None] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(100))
    display: Mapped[str] = mapped_column(String(300), nullable=False)
    clinical_status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    recorded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"), nullable=False)


class ClinicalNote(Base, Timestamped):
    __tablename__ = "clinical_notes"
    note_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.patient_uuid"), nullable=False, index=True)
    encounter_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("encounters.encounter_uuid"), nullable=False,
                                                       index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.organization_id"), nullable=False,
                                                        index=True)
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    note_type: Mapped[str] = mapped_column(String(64), default="PROGRESS", nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class Medication(Base, Timestamped):
    __tablename__ = "medications"
    medication_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code_system: Mapped[str | None] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(100))
    display: Mapped[str] = mapped_column(String(300), nullable=False)


class Prescription(Base, Timestamped):
    __tablename__ = "prescriptions"
    prescription_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.patient_uuid"), nullable=False, index=True)
    encounter_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("encounters.encounter_uuid"), nullable=False,
                                                       index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.organization_id"), nullable=False,
                                                        index=True)
    prescriber_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text)


class PrescriptionItem(Base, Timestamped):
    __tablename__ = "prescription_items"
    prescription_item_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prescription_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("prescriptions.prescription_uuid"), nullable=False,
                                                          index=True)
    medication_uuid: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("medications.medication_uuid"))
    medicine: Mapped[str] = mapped_column(String(300), nullable=False)
    dose: Mapped[str | None] = mapped_column(String(100))
    route: Mapped[str | None] = mapped_column(String(100))
    frequency: Mapped[str | None] = mapped_column(String(100))
    duration: Mapped[str | None] = mapped_column(String(100))
    instructions: Mapped[str | None] = mapped_column(Text)


class DiagnosticReport(Base, Timestamped):
    __tablename__ = "diagnostic_reports"
    report_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ecg_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("ecg_records.ecg_uuid"), nullable=False, unique=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.organization_id"), nullable=False,
                                                        index=True)
    status: Mapped[str] = mapped_column(String(32), default="PRELIMINARY", nullable=False)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    conclusion: Mapped[str | None] = mapped_column(Text)


class ReportDocument(Base, Timestamped):
    __tablename__ = "report_documents"
    report_document_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("diagnostic_reports.report_uuid"), nullable=False,
                                                    index=True)
    storage_uri: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), default="application/pdf", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)


class Feature(Base, Timestamped):
    __tablename__ = "features"
    feature_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class RoleFeaturePermission(Base, Timestamped):
    __tablename__ = "role_feature_permissions"
    role_feature_permission_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True,
                                                                      default=uuid.uuid4)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.role_id"), nullable=False, index=True)
    feature_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("features.feature_uuid"), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint("role_id", "feature_uuid", name="uq_role_feature"),)


class OrganizationFeatureOverride(Base, Timestamped):
    __tablename__ = "organization_feature_overrides"
    organization_feature_override_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True,
                                                                            default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.organization_id"), nullable=False,
                                                        index=True)
    feature_uuid: Mapped[uuid.UUID] = mapped_column(ForeignKey("features.feature_uuid"), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    __table_args__ = (UniqueConstraint("organization_id", "feature_uuid", name="uq_org_feature"),)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    audit_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.user_id"), index=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.organization_id"), index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(100))
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Notification(Base, Timestamped):
    __tablename__ = "notifications"
    notification_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.organization_id"), index=True)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshToken(Base, Timestamped):
    __tablename__ = "refresh_tokens"
    refresh_token_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"), nullable=False, index=True)
    token_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BackgroundJob(Base, Timestamped):
    __tablename__ = "background_jobs"
    job_uuid: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.organization_id"), nullable=False,
                                                        index=True)
    job_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", nullable=False)
    related_resource_id: Mapped[str | None] = mapped_column(String(100))
    queue_job_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    error_message: Mapped[str | None] = mapped_column(String(500))
