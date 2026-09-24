"""Narrow FHIR-compatible serialization; it is not a FHIR conformance claim."""
from __future__ import annotations

from typing import Any

from .models import (AIAnalysis, AIModelVersion, Condition, DiagnosticReport, ECGRecord, Encounter,
                     Organization, Patient, PatientOrganization, Practitioner, Prescription, PrescriptionItem, User)


FHIR_BASE = "https://ecg-health-platform.example/fhir"


def _identifier(system: str, value: str, kind: str | None = None) -> dict[str, str]:
    result = {"system": system, "value": value}
    if kind:
        result["type"] = {"text": kind}
    return result


def patient_resource(patient: Patient, membership: PatientOrganization) -> dict[str, Any]:
    return {"resourceType": "Patient", "id": str(patient.patient_uuid),
            "identifier": [_identifier("urn:ecg-health:empi", patient.empi_id, "EMPI"),
                           _identifier("urn:ecg-health:mrn", membership.mrn, "MRN")],
            "name": [{"family": patient.last_name, "given": [patient.first_name] + ([patient.middle_name] if patient.middle_name else [])}],
            "birthDate": patient.date_of_birth.isoformat(), "gender": patient.administrative_gender or "unknown",
            "active": not patient.deceased}


def organization_resource(organization: Organization) -> dict[str, Any]:
    return {"resourceType": "Organization", "id": str(organization.organization_id),
            "identifier": [_identifier("urn:ecg-health:organization-code", organization.code)],
            "name": organization.name, "active": organization.status == "ACTIVE"}


def encounter_resource(encounter: Encounter) -> dict[str, Any]:
    return {"resourceType": "Encounter", "id": str(encounter.encounter_uuid), "status": encounter.status.lower(),
            "class": {"code": encounter.encounter_type},
            "subject": {"reference": f"Patient/{encounter.patient_uuid}"},
            "period": {"start": encounter.start_time.isoformat(),
                       **({"end": encounter.end_time.isoformat()} if encounter.end_time else {})}}


def observation_resource(ecg: ECGRecord, analysis: AIAnalysis, version: AIModelVersion | None) -> dict[str, Any]:
    component = []
    if analysis.predicted_label:
        component.append({"code": {"text": "Model-generated rhythm label"},
                          "valueCodeableConcept": {"text": analysis.predicted_label_name or analysis.predicted_label}})
    if analysis.confidence is not None:
        component.append({"code": {"text": "Uncalibrated model score"}, "valueDecimal": analysis.confidence})
    return {"resourceType": "Observation", "id": str(analysis.ai_analysis_uuid), "status": "final" if analysis.status == "COMPLETED" else "preliminary",
            "code": {"text": "ECG AI research/clinical-decision-support analysis"},
            "subject": {"reference": f"Patient/{ecg.patient_uuid}"},
            "encounter": {"reference": f"Encounter/{ecg.encounter_uuid}"},
            "effectiveDateTime": ecg.recorded_at.isoformat(), "component": component,
            "note": [{"text": "Model-generated result requiring qualified clinician review; not an autonomous diagnosis."}],
            "extension": ([{"url": "urn:ecg-health:model-version", "valueString": version.version}]
                          if version else [])}


def condition_resource(condition: Condition) -> dict[str, Any]:
    coding = []
    if condition.code_system and condition.code:
        coding.append({"system": condition.code_system, "code": condition.code, "display": condition.display})
    return {"resourceType": "Condition", "id": str(condition.condition_uuid), "subject": {"reference": f"Patient/{condition.patient_uuid}"},
            "encounter": {"reference": f"Encounter/{condition.encounter_uuid}"},
            "clinicalStatus": {"text": condition.clinical_status}, "code": {"coding": coding, "text": condition.display}}


def medication_request_resource(prescription: Prescription, items: list[PrescriptionItem]) -> dict[str, Any]:
    return {"resourceType": "MedicationRequest", "id": str(prescription.prescription_uuid), "status": prescription.status.lower(),
            "intent": "order", "subject": {"reference": f"Patient/{prescription.patient_uuid}"},
            "encounter": {"reference": f"Encounter/{prescription.encounter_uuid}"},
            "note": [{"text": prescription.instructions}] if prescription.instructions else [],
            "dosageInstruction": [{"text": " | ".join(filter(None, [item.medicine, item.dose, item.route,
                                                                    item.frequency, item.duration, item.instructions]))}
                                  for item in items]}


def diagnostic_report_resource(report: DiagnosticReport, ecg: ECGRecord, analysis: AIAnalysis | None) -> dict[str, Any]:
    results = [{"reference": f"Observation/{analysis.ai_analysis_uuid}"}] if analysis else []
    return {"resourceType": "DiagnosticReport", "id": str(report.report_uuid), "status": report.status.lower(),
            "code": {"text": "ECG report"}, "subject": {"reference": f"Patient/{ecg.patient_uuid}"},
            "encounter": {"reference": f"Encounter/{ecg.encounter_uuid}"}, "result": results,
            "conclusion": report.conclusion or ""}
