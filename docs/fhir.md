# FHIR-compatible output

The platform provides narrow, read-only FHIR-shaped JSON mappings at:

```text
GET /api/v1/fhir/{resource_type}/{resource_id}
```

Supported resource types are `Patient`, `Organization`, `Encounter`, `Observation`, `Condition`, `MedicationRequest`, `DiagnosticReport`, and `Practitioner`. Normal application authorization and tenant/patient scope checks run before a mapping is returned.

## Mapping intent

- `Patient` emits internal EMPI and hospital MRN identifiers, name, birth date, administrative gender, and active state.
- `Organization` emits the organization code and status.
- `Encounter` references the patient and captures the local type/status/period fields.
- `Observation` represents the AI analysis, including label and uncalibrated score. It includes a note that the output requires qualified clinician review and is not autonomous diagnosis.
- `Condition`, `MedicationRequest`, and `DiagnosticReport` serialize clinician-authored records and their references.
- `Practitioner` maps the linked user display name.

The code calls this “FHIR-compatible” deliberately. It does **not** claim FHIR R4/R5 conformance, certification, interoperability certification, or safe exchange with a real EHR.

## Not implemented

There is no CapabilityStatement, SMART-on-FHIR/OAuth integration, search, Bundle, transaction, subscription, resource write operation, terminology validation, profile validation, provenance resource, document reference, binary resource, consent resource, or EHR synchronization. Resource references and code systems should be reviewed and profiled before external use.

The static `FHIR_BASE` constant is a placeholder/example URI, not a deployed public endpoint. Treat these responses as a developer integration aid only until a standards, security, identity, terminology, and clinical-governance program defines an interoperability boundary.
