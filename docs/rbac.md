# Role-based access control

RBAC is enforced in Flask decorators and resolved on the server from the `roles`, `permissions`, `user_roles`, and `role_permissions` tables. The browser may hide unavailable navigation but is never the authorization authority.

## Built-in roles

| Role | Current scope | Main intended capabilities |
| --- | --- | --- |
| `SUPER_ADMIN` | Platform | All permissions; create and manage hospital tenants |
| `HOSPITAL_ADMIN` | Organization | Read/update its hospital, create/read users, read/create/update patients, view ECG/report/audit data, view features |
| `DOCTOR` | Organization | Patient and encounter workflow, ECG upload/analysis/read, AI result read, clinician review/diagnosis/note/prescription/report actions |
| `TECHNICIAN` | Organization | Patient and encounter workflow, ECG upload/analysis/read, AI result read; no clinician diagnosis/review or prescription authority |
| `PATIENT` | Linked patient record | Read only its own patient, encounter, ECG, report, and prescription information where routes expose those self permissions |

`SUPER_ADMIN` resolves to `*`. All other roles receive the permissions declared in `ROLE_PERMISSIONS` in `ecg_cvd.clinical.services`. A hospital-scoped user cannot pass another organization identifier into a tenant-scoped service operation. A patient principal is rejected when it requests a different patient UUID.

## Important implementation details

- Hospital administrators cannot create another hospital administrator through the current user-creation service; that assignment is reserved for a super administrator.
- Some modeled permissions, such as `user.update`, exist for future route expansion but do not yet have matching update endpoints.
- A role does not by itself prove a clinician is licensed, currently credentialed, or approved to prescribe. The `Practitioner` record stores optional registration and specialty values only.
- The review route requires the diagnosis-create permission and writes clinician-authored content. It does not derive a diagnosis or medication from the model.

## Feature flags

`features`, `role_feature_permissions`, and `organization_feature_overrides` establish a data model for platform/role/tenant features. The admin API currently lists and edits global `Feature` rows, but route-level feature evaluation and per-role/per-organization override enforcement have not yet been wired into requests. Do not use those tables as a safety or access-control boundary until that work is complete.

## Operations guidance

Create the smallest role required, keep super-admin assignments rare, and verify organization membership before creating staff accounts. A production rollout also needs administrative audit review, credentialing workflow, separation-of-duties policy, periodic access reviews, emergency-access governance, and tested deprovisioning. Those policies are outside the current codebase.
