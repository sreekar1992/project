export type Identifier = string;

export interface AuthTokens {
  access_token?: string;
  accessToken?: string;
  user?: AuthenticatedUser;
}

export interface AuthenticatedUser {
  id?: Identifier;
  user_id?: Identifier;
  name?: string;
  display_name?: string;
  email?: string;
  role?: string;
  roles?: string[];
  organization_id?: Identifier | null;
}

export interface AuthenticatedPrincipal {
  user_id: Identifier;
  display_name?: string;
  email?: string;
  organization_id?: Identifier | null;
  roles: string[];
}

export interface DashboardResponse {
  metrics?: Record<string, number | string | null | undefined>;
  recent_activity?: AuditActivity[];
  [key: string]: unknown;
}

export interface AdminHospital {
  organization_id: Identifier;
  name: string;
  code: string;
  status: "ACTIVE" | "DISABLED" | string;
}

export interface AdminUser {
  user_id: Identifier;
  email: string;
  display_name: string;
  active: boolean;
  organization_id?: Identifier | null;
}

export interface PlatformFeature {
  feature_uuid: Identifier;
  key: string;
  description?: string | null;
  enabled: boolean;
}

export interface ModelVersionSummary {
  model_uuid: Identifier;
  model_name: string;
  model_version_uuid: Identifier;
  version: string;
  framework?: string | null;
  status?: string | null;
  artifact_sha256?: string | null;
  metrics?: Record<string, unknown> | null;
  preprocessing?: Record<string, unknown> | null;
}

export interface AuditLogEntry {
  audit_id: Identifier;
  timestamp?: string;
  action?: string;
  resource_type?: string;
  resource_id?: string;
  success?: boolean;
  request_id?: string;
}

export interface AuditActivity {
  id?: Identifier;
  created_at?: string;
  action?: string;
  actor_name?: string;
  description?: string;
}

export interface Patient {
  id: Identifier;
  mrn?: string;
  first_name?: string;
  last_name?: string;
  preferred_name?: string;
  date_of_birth?: string;
  sex_at_birth?: string;
  phone?: string;
  email?: string;
  created_at?: string;
  updated_at?: string;
  [key: string]: unknown;
}

export interface Encounter {
  id: Identifier;
  patient_id: Identifier;
  status?: string;
  encounter_type?: string;
  reason?: string;
  occurred_at?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface EcgRecord {
  id: Identifier;
  patient_id?: Identifier;
  encounter_id?: Identifier;
  filename?: string;
  source_filename?: string;
  status?: string;
  acquired_at?: string;
  created_at?: string;
  waveform_url?: string;
  content_type?: string;
  source_kind?: "WAVEFORM" | "ECG_IMAGE" | "JPEG_DIGITIZED_WAVEFORM" | string;
  source_ecg_id?: Identifier;
  provenance?: string | {
    kind?: string;
    source_ecg_id?: Identifier;
    research_only?: boolean;
  };
  [key: string]: unknown;
}

export interface EcgWaveform {
  ecg_uuid: Identifier;
  sampling_rate_hz: number;
  duration_seconds: number;
  lead_count: number;
  amplitude_units?: string;
  samples: number[];
}

export interface EcgSecurityStatus {
  ecg_uuid: Identifier;
  storage: {
    encrypted_at_rest: boolean;
    algorithm?: string | null;
    authenticated_encryption: boolean;
    key_management: string;
    legacy_unencrypted: boolean;
  };
  integrity: {
    algorithm: string;
    verified_on_authorized_read: boolean;
    fingerprint?: string;
  };
  access: {
    tenant_scoped: boolean;
    server_side_rbac: boolean;
    audited: boolean;
  };
  research_camouflage: {
    used: boolean;
    reason: string;
  };
}

export interface ConfiguredResearchModel {
  status: "CONFIGURED" | string;
  model_name: string;
  architecture: string;
  version: string;
  framework: string;
  artifact_sha256?: string;
  supported_source_formats: string[];
  image_source_formats: string[];
  image_policy: string;
  research_only: boolean;
  safety?: string;
  preprocessing?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
}

export interface Analysis {
  id: Identifier;
  ecg_id?: Identifier;
  patient_id?: Identifier;
  status?: string;
  predicted_label?: string;
  prediction_label?: string;
  confidence?: number;
  model_version?: string;
  has_explanation?: boolean;
  top_predictions?: Array<{ rank?: number; label?: string; label_name?: string; score?: number }>;
  created_at?: string;
  reviewed_at?: string;
  [key: string]: unknown;
}

/** A read-only catalogue entry used only to help clinicians find a medicine name. */
export interface MedicineSuggestion {
  id?: Identifier;
  name: string;
  composition?: string | null;
  manufacturer?: string | null;
  category?: string | null;
}

export interface MedicineCatalogResponse {
  items: MedicineSuggestion[];
  query: string;
  catalog_available: boolean;
}

/**
 * Editable wording that summarizes an existing research analysis. It is not a
 * diagnosis, treatment plan, or prescription recommendation.
 */
export interface ResearchAssessmentSuggestion {
  analysis_id: Identifier;
  suggestion: string;
  safety: string;
}

/**
 * Server-generated result of an explicitly confirmed JPEG trace digitization.
 * The derived signal and analysis are research artifacts, not a clinical
 * interpretation of a photograph or scan.
 */
export interface EcgDigitizationResult {
  source_ecg_id: Identifier;
  derived_ecg: EcgRecord;
  analysis: Analysis;
  digitization: {
    status: string;
    source_format: string;
    output_format: "csv" | "mat" | string;
    provenance: string;
    quality?: Record<string, string | number | boolean | null>;
    limitations: string;
  };
}

export interface ClinicalReview {
  id: Identifier;
  analysis_id: Identifier;
  status?: string;
  clinician_notes?: string;
  reviewer_name?: string;
  reviewed_at?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface Report {
  id: Identifier;
  patient_id?: Identifier;
  title?: string;
  status?: string;
  created_at?: string;
  download_url?: string;
  [key: string]: unknown;
}

export interface PaginatedResponse<T> {
  items?: T[];
  data?: T[];
  results?: T[];
  total?: number;
  [key: string]: unknown;
}

export function listFromResponse<T>(payload: T[] | PaginatedResponse<T>): T[] {
  if (Array.isArray(payload)) {
    return payload;
  }

  return payload.items ?? payload.data ?? payload.results ?? [];
}

export function patientDisplayName(patient: Patient): string {
  const name = [patient.first_name, patient.last_name].filter(Boolean).join(" ");
  return patient.preferred_name || name || patient.mrn || patient.id;
}
