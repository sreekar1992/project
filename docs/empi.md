# EMPI and patient identity approach

## Implemented model

The platform separates a person-level `Patient` record from hospital membership:

- Each patient receives a globally unique internal `empi_id`, currently generated as an opaque `EMPI-...` value.
- `PatientOrganization` gives that patient an MRN that is unique within one organization.
- `PatientIdentifier` holds additional identifiers with a system, type, value, and optional assigning organization.
- Contacts and addresses are separate records rather than columns on the master patient row.

This supports one patient being known by multiple hospitals without making an MRN globally unique. The API always scopes ordinary patient lookup and list operations to the caller's organization; it does not expose a cross-hospital patient search endpoint.

## Duplicate prevention flow

Before creating a patient, `POST /patients/matches` can search the current hospital using one of:

- MRN;
- normalized first name, last name, and date of birth;
- phone; or
- an identifier value such as an ABHA ID.

Patient creation repeats a basic name/date-of-birth/phone check. If candidate records are found, it returns `POSSIBLE_PATIENT_MATCH` rather than automatically merging data. A caller can explicitly confirm an existing patient UUID when appropriate. This is an intentionally conservative manual-review step.

## What it does not do

This is not a full enterprise master-patient-index implementation. It does not verify ABHA or any national identifier, normalize addresses/phones, calculate probabilistic matching scores, query an external registry, use phonetic matching, or run automated merges. The schema includes `patient_merge_history` and `merged_into_patient_id`, but there is no merge API or operational merge workflow yet.

Before integrating real patient identity sources, define identity-assurance rules, patient-consent/legal authority, source-of-truth policy, duplicate review queue, merge/unmerge approvals, survivorship rules, downstream notification, and audit/retention requirements. Never treat a current match result as proof of identity.
