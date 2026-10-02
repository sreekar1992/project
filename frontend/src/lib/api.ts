import type {
  Analysis,
  AuthenticatedPrincipal,
  AuthTokens,
  AuthenticatedUser,
  ClinicalReview,
  DashboardResponse,
  EcgRecord,
  EcgSecurityStatus,
  EcgWaveform,
  Encounter,
  PaginatedResponse,
  Patient,
  Report,
} from "../types/api";

const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim();
export const API_BASE_URL = (configuredBaseUrl || "/api/v1").replace(/\/$/, "");
const ACCESS_TOKEN_KEY = "ecg-research-platform.access-token";
const EXPLICIT_SIGN_OUT_KEY = "ecg-research-platform.signed-out";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let authGeneration = 0;

export const authStorage = {
  getAccessToken(): string | null {
    return window.sessionStorage.getItem(ACCESS_TOKEN_KEY);
  },
  setAccessToken(token: string): void {
    authGeneration += 1;
    window.sessionStorage.removeItem(EXPLICIT_SIGN_OUT_KEY);
    window.sessionStorage.setItem(ACCESS_TOKEN_KEY, token);
  },
  clear(): void {
    authGeneration += 1;
    window.sessionStorage.removeItem(ACCESS_TOKEN_KEY);
  },
  markSignedOut(): void {
    authGeneration += 1;
    window.sessionStorage.removeItem(ACCESS_TOKEN_KEY);
    window.sessionStorage.setItem(EXPLICIT_SIGN_OUT_KEY, "true");
  },
  wasExplicitlySignedOut(): boolean {
    return window.sessionStorage.getItem(EXPLICIT_SIGN_OUT_KEY) === "true";
  },
};

function endpoint(path: string): string {
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

function tokenFrom(payload: AuthTokens): string | undefined {
  return payload.access_token ?? payload.accessToken;
}

async function parsePayload(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return response.json();
  }

  const text = await response.text();
  return text || undefined;
}

function messageFromPayload(payload: unknown, fallback: string): string {
  if (typeof payload === "string" && payload.trim()) {
    return payload;
  }
  if (payload && typeof payload === "object") {
    const value = payload as Record<string, unknown>;
    const candidate = value.message ?? value.detail ?? value.error;
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate;
    }
    if (candidate && typeof candidate === "object") {
      const nested = candidate as Record<string, unknown>;
      if (typeof nested.message === "string" && nested.message.trim()) {
        return nested.message;
      }
    }
  }
  return fallback;
}

let refreshInFlight: Promise<string | null> | null = null;

function canRefreshAfterUnauthorized(path: string): boolean {
  return path !== "/auth/login" && path !== "/auth/refresh" && path !== "/auth/logout";
}

async function refreshAccessToken(): Promise<string | null> {
  if (refreshInFlight) {
    return refreshInFlight;
  }

  refreshInFlight = (async () => {
    if (authStorage.wasExplicitlySignedOut()) {
      return null;
    }
    const generationAtStart = authGeneration;
    try {
      const response = await fetch(endpoint("/auth/refresh"), {
        method: "POST",
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      const payload = (await parsePayload(response)) as AuthTokens;
      const token = response.ok ? tokenFrom(payload) : undefined;
      if (token && generationAtStart === authGeneration) {
        authStorage.setAccessToken(token);
        return token;
      }
    } catch {
      // A missing/expired cookie or an unavailable local server is handled as
      // an unauthenticated state, not exposed as a raw bearer-token error.
    }
    if (generationAtStart === authGeneration) {
      authStorage.clear();
    }
    return null;
  })().finally(() => {
    refreshInFlight = null;
  });

  return refreshInFlight;
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: BodyInit | Record<string, unknown>;
  retryAfterRefresh?: boolean;
}

async function authorizedFetch(path: string, init: RequestInit, retryAfterRefresh = true): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = authStorage.getAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(endpoint(path), {
    ...init,
    credentials: "include",
    headers,
  });

  if (response.status === 401 && retryAfterRefresh && canRefreshAfterUnauthorized(path)) {
    const refreshedToken = await refreshAccessToken();
    if (refreshedToken) {
      return authorizedFetch(path, init, false);
    }
  }
  return response;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, headers, retryAfterRefresh = true, ...rest } = options;
  const isFormData = body instanceof FormData;
  const isNativeBody = body instanceof Blob || body instanceof ArrayBuffer || body instanceof URLSearchParams;
  const jsonBody: BodyInit | undefined = body && !isFormData && !isNativeBody && typeof body === "object"
    ? JSON.stringify(body)
    : (body as BodyInit | undefined);
  const requestHeaders = new Headers(headers);
  requestHeaders.set("Accept", "application/json");
  if (jsonBody && !isFormData && !requestHeaders.has("Content-Type")) {
    requestHeaders.set("Content-Type", "application/json");
  }
  const response = await authorizedFetch(path, {
    ...rest,
    body: jsonBody,
    headers: requestHeaders,
  }, retryAfterRefresh);

  const payload = await parsePayload(response);
  if (!response.ok) {
    throw new ApiError(messageFromPayload(payload, `Request failed (${response.status})`), response.status, payload);
  }
  return payload as T;
}

async function authorizedBlob(path: string, fallbackMessage: string): Promise<Blob> {
  const response = await authorizedFetch(path, { headers: { Accept: "application/octet-stream" } });
  if (!response.ok) {
    const payload = await parsePayload(response);
    throw new ApiError(messageFromPayload(payload, fallbackMessage), response.status, payload);
  }
  return response.blob();
}

export const api = {
  auth: {
    async login(values: { email: string; password: string }): Promise<AuthenticatedUser | undefined> {
      const payload = await request<AuthTokens>("/auth/login", {
        method: "POST",
        body: values,
        retryAfterRefresh: false,
      });
      const token = tokenFrom(payload);
      if (!token) {
        throw new ApiError("The login response did not include an access token.", 502, payload);
      }
      authStorage.setAccessToken(token);
      return payload.user;
    },
    async refresh(): Promise<string | null> {
      return refreshAccessToken();
    },
    async logout(): Promise<void> {
      try {
        await request<void>("/auth/logout", { method: "POST", retryAfterRefresh: false });
      } finally {
        authStorage.clear();
      }
    },
    async restore(): Promise<AuthenticatedPrincipal | undefined> {
      if (authStorage.wasExplicitlySignedOut()) {
        return undefined;
      }
      if (!authStorage.getAccessToken()) {
        const refreshedToken = await refreshAccessToken();
        if (!refreshedToken) return undefined;
      }
      return request<AuthenticatedPrincipal>("/auth/me");
    },
    me: () => request<AuthenticatedPrincipal>("/auth/me"),
  },
  dashboard: () => request<DashboardResponse>("/dashboard"),
  patients: {
    list: (query?: URLSearchParams) => request<Patient[] | PaginatedResponse<Patient>>(`/patients${query?.size ? `?${query.toString()}` : ""}`),
    get: (id: string) => request<Patient>(`/patients/${encodeURIComponent(id)}`),
    create: (values: Record<string, unknown>) => request<Patient>("/patients", { method: "POST", body: values }),
  },
  encounters: {
    list: (patientId: string) => request<Encounter[] | PaginatedResponse<Encounter>>(`/encounters?patient_id=${encodeURIComponent(patientId)}`),
    create: (values: Record<string, unknown>) => request<Encounter>("/encounters", { method: "POST", body: values }),
  },
  ecgs: {
    list: (patientId: string) => request<EcgRecord[] | PaginatedResponse<EcgRecord>>(`/ecgs?patient_id=${encodeURIComponent(patientId)}`),
    upload: (values: { patientId: string; encounterId?: string; file: File }) => {
      const form = new FormData();
      form.append("patient_id", values.patientId);
      if (values.encounterId) form.append("encounter_id", values.encounterId);
      form.append("file", values.file);
      return request<EcgRecord>("/ecgs", { method: "POST", body: form });
    },
    download: (ecgId: string) => authorizedBlob(`/ecgs/${encodeURIComponent(ecgId)}/file`, "The ECG file could not be downloaded."),
    waveform: (ecgId: string) => request<EcgWaveform>(`/ecgs/${encodeURIComponent(ecgId)}/waveform`),
    security: (ecgId: string) => request<EcgSecurityStatus>(`/ecgs/${encodeURIComponent(ecgId)}/security`),
  },
  analyses: {
    list: (patientId: string) => request<Analysis[] | PaginatedResponse<Analysis>>(`/analyses?patient_id=${encodeURIComponent(patientId)}`),
    create: (values: { ecg_id: string }) => request<Analysis>("/analyses", { method: "POST", body: values }),
    explanation: (ecgId: string) => authorizedBlob(`/ecgs/${encodeURIComponent(ecgId)}/explain.png`, "The model explanation could not be loaded."),
  },
  reviews: {
    list: (patientId: string) => request<ClinicalReview[] | PaginatedResponse<ClinicalReview>>(`/reviews?patient_id=${encodeURIComponent(patientId)}`),
    create: (values: Record<string, unknown>) => request<ClinicalReview>("/reviews", { method: "POST", body: values }),
  },
  reports: {
    list: (patientId: string) => request<Report[] | PaginatedResponse<Report>>(`/reports?patient_id=${encodeURIComponent(patientId)}`),
    generate: (reportId: string) => request<Report>(`/reports/${encodeURIComponent(reportId)}/generate-pdf`, { method: "POST" }),
    download: (reportId: string) => authorizedBlob(`/reports/${encodeURIComponent(reportId)}/download`, "The report could not be downloaded."),
  },
};

export function apiErrorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "The server could not complete this request. Please try again.";
}
