# Database design

## Persistence choices

`DATABASE_URL` selects the SQLAlchemy database. Local development falls back to SQLite at `var/ecg_health.db`; Docker Compose uses PostgreSQL 16. The database schema is defined in `ecg_cvd.clinical.models` and versioned with Alembic under `migrations/`.

SQLite is a developer convenience, not a multi-user hospital deployment database. Run `alembic upgrade head` against PostgreSQL before starting a non-development API instance. Development/test startup may create the schema automatically; production startup intentionally does not.

## Main entities

| Area | Tables and purpose |
| --- | --- |
| Tenant and identity | `organizations`, `organization_addresses`, `users`, `roles`, `permissions`, `user_roles`, `role_permissions`, `refresh_tokens` |
| Patient identity | `patients`, `patient_organizations`, `patient_identifiers`, `patient_contacts`, `patient_addresses`, `patient_merge_history` |
| Care workflow | `practitioners`, `practitioner_roles`, `encounters`, `conditions`, `clinical_notes`, `medications`, `prescriptions`, `prescription_items` |
| ECG assets | `ecg_records`, `ecg_files` |
| AI provenance | `ai_models`, `ai_model_versions`, `ai_analyses`, `ai_predictions`, `doctor_reviews` |
| Reports and controls | `diagnostic_reports`, `report_documents`, `features`, `role_feature_permissions`, `organization_feature_overrides`, `audit_events`, `notifications`, `background_jobs` |

All primary identifiers are UUIDs. Most mutable business tables include UTC creation and update timestamps. External ECG and PDF bytes are deliberately not stored in database rows; `storage_uri`, `object_key`, `sha256`, content type, size, and original filename provide the reference and integrity metadata.

## Tenancy and identity keys

- `Organization` is the hospital/tenant boundary.
- `Patient` is the master identity record. `empi_id` is globally unique in this implementation.
- `PatientOrganization` links a patient to a hospital and gives the membership a hospital-local MRN. `(organization_id, mrn)` is unique.
- `PatientIdentifier` supports additional identifiers and makes `(organization_id, identifier_system, identifier_value)` unique.
- Encounters, ECG records, analyses, reviews, conditions, prescriptions, reports, and audit events carry `organization_id` for scoped queries and auditability.

`ECGRecord` has one patient, encounter, organization, and accession number. Its original uploaded file is represented by `ECGFile`; the design permits additional file purposes later. An analysis links to the exact model-version row, input hash, top predictions, result status, optional explanation asset, and processing metadata.

## Workflow relationships

```text
Patient --< PatientOrganization >-- Organization
Patient --< Encounter --< ECGRecord --< AIAnalysis --< AIPrediction
                                  |                 |
                                  |                 +-- DoctorReview
                                  +-- ECGFile
Encounter --< Condition / ClinicalNote / Prescription --< PrescriptionItem
ECGRecord --1 DiagnosticReport --< ReportDocument
```

A clinician review can add a clinician-authored condition, note, prescription draft/items, and report conclusion. It is intentionally separate from `AIAnalysis`, so a model result is not silently promoted to a diagnosis or prescription.

## Migration discipline and limits

The initial migration is metadata-driven to support the supported development/deployment engines. Future schema changes should use explicit Alembic operations, include an upgrade and downgrade review, and be exercised on a copy of representative non-production data.

The current schema does not implement consent management, legal hold, retention/deletion policy, soft deletion for every entity, data residency controls, a complete merge workflow, or independent tamper-evident audit storage. Those are required design and governance work before handling real clinical records.
