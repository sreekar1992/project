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
