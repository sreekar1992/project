import AddBusinessOutlinedIcon from "@mui/icons-material/AddBusinessOutlined";
import AdminPanelSettingsOutlinedIcon from "@mui/icons-material/AdminPanelSettingsOutlined";
import BadgeOutlinedIcon from "@mui/icons-material/BadgeOutlined";
import FactCheckOutlinedIcon from "@mui/icons-material/FactCheckOutlined";
import GroupsOutlinedIcon from "@mui/icons-material/GroupsOutlined";
import HistoryOutlinedIcon from "@mui/icons-material/HistoryOutlined";
import MemoryOutlinedIcon from "@mui/icons-material/MemoryOutlined";
import RefreshOutlinedIcon from "@mui/icons-material/RefreshOutlined";
import SecurityOutlinedIcon from "@mui/icons-material/SecurityOutlined";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { ApiErrorAlert } from "../components/ApiErrorAlert";
import { LoadingState } from "../components/LoadingState";
import { request } from "../lib/api";
import { useAuth } from "../lib/auth";
import type {
  AdminHospital,
  AdminUser,
  AuditLogEntry,
  AuthenticatedPrincipal,
  DashboardResponse,
  ModelVersionSummary,
  PlatformFeature,
} from "../types/api";

type ProvisionableRole = "HOSPITAL_ADMIN" | "DOCTOR" | "TECHNICIAN";

interface HospitalFormValues {
  name: string;
  code: string;
}

interface UserFormValues {
  display_name: string;
  email: string;
  password: string;
  role: ProvisionableRole;
  organization_id: string;
}

const initialHospitalForm: HospitalFormValues = { name: "", code: "" };
const initialUserForm: UserFormValues = {
  display_name: "",
  email: "",
  password: "",
  role: "DOCTOR",
  organization_id: "",
};

const roleLabels: Record<ProvisionableRole, string> = {
  HOSPITAL_ADMIN: "Hospital administrator",
  DOCTOR: "Doctor",
  TECHNICIAN: "Technician",
};

const adminApi = {
  identity: () => request<AuthenticatedPrincipal>("/auth/me"),
  dashboard: () => request<DashboardResponse>("/admin/dashboard"),
  hospitals: {
    list: () => request<AdminHospital[]>("/admin/hospitals"),
    create: (values: { name: string; code: string }) => request<AdminHospital>("/admin/hospitals", { method: "POST", body: values }),
  },
  users: {
    list: () => request<AdminUser[]>("/admin/users"),
    create: (values: {
      email: string;
      password: string;
      display_name: string;
      role: ProvisionableRole;
      organization_id: string;
    }) => request<AdminUser>("/admin/users", { method: "POST", body: values }),
  },
  features: () => request<PlatformFeature[]>("/admin/features"),
  models: () => request<ModelVersionSummary[]>("/admin/ai-models"),
  auditLogs: (limit = 25) => request<AuditLogEntry[]>(`/admin/audit-logs?limit=${Math.min(Math.max(Math.floor(limit), 1), 200)}`),
};

const metricIcons = [
  <AddBusinessOutlinedIcon key="hospitals" />,
  <GroupsOutlinedIcon key="users" />,
  <BadgeOutlinedIcon key="patients" />,
  <FactCheckOutlinedIcon key="ecgs" />,
  <MemoryOutlinedIcon key="analyses" />,
  <HistoryOutlinedIcon key="reports" />,
];

function humanize(value: string): string {
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function formatTimestamp(value?: string): string {
  if (!value) return "Not returned";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function metricEntries(payload?: DashboardResponse): Array<[string, string | number]> {
  if (!payload) return [];
  const source = payload.metrics ?? Object.fromEntries(
    Object.entries(payload).filter(([, value]) => typeof value === "string" || typeof value === "number"),
  );
  return Object.entries(source).filter(
    (entry): entry is [string, string | number] => typeof entry[1] === "string" || typeof entry[1] === "number",
  );
}

function statusColor(status?: string): "success" | "warning" | "default" {
  if (status === "ACTIVE" || status === "ACTIVE".toLowerCase()) return "success";
  if (status === "DISABLED" || status === "RETIRED") return "warning";
  return "default";
}

function modelMetadata(model: ModelVersionSummary): Array<[string, string | number | boolean]> {
  if (!model.metrics || typeof model.metrics !== "object") return [];
  return Object.entries(model.metrics)
    .filter((entry): entry is [string, string | number | boolean] => {
      const value = entry[1];
      return typeof value === "string" || typeof value === "number" || typeof value === "boolean";
    })
    .slice(0, 4);
}

function SectionHeader({
  icon,
  title,
  description,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "flex-start" }} gap={2} sx={{ px: 3, py: 2.25, borderBottom: "1px solid #dce7e3" }}>
      <Stack direction="row" spacing={1.5} alignItems="flex-start">
        <Box sx={{ mt: 0.25, p: 0.9, borderRadius: 2, bgcolor: "rgba(11, 93, 76, 0.08)", color: "primary.main", display: "grid", placeItems: "center" }}>
          {icon}
        </Box>
        <Box>
          <Typography variant="h6">{title}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.25, maxWidth: 760 }}>{description}</Typography>
        </Box>
      </Stack>
      {action}
    </Stack>
  );
}

function EmptyState({ children }: { children: React.ReactNode }) {
  return <Box sx={{ px: 3, py: 4 }}><Typography color="text.secondary">{children}</Typography></Box>;
}

function ResourceFailure({ error }: { error: unknown }) {
  return <Box sx={{ p: 3 }}><ApiErrorAlert error={error} /></Box>;
}

function ResourceLoading({ label }: { label: string }) {
  return <LoadingState label={label} />;
}

export function SuperAdminPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [hospitalDialogOpen, setHospitalDialogOpen] = useState(false);
  const [userDialogOpen, setUserDialogOpen] = useState(false);
  const [hospitalForm, setHospitalForm] = useState<HospitalFormValues>(initialHospitalForm);
  const [userForm, setUserForm] = useState<UserFormValues>(initialUserForm);

  const identity = useQuery({
    queryKey: ["auth", "me"],
    queryFn: adminApi.identity,
    retry: false,
  });
  const roles = useMemo(
    () => new Set([...(user?.roles ?? []), ...(identity.data?.roles ?? [])]),
    [identity.data?.roles, user?.roles],
  );
  const isSuperAdmin = roles.has("SUPER_ADMIN");

  const dashboard = useQuery({ queryKey: ["admin", "dashboard"], queryFn: adminApi.dashboard, enabled: isSuperAdmin });
  const hospitals = useQuery({ queryKey: ["admin", "hospitals"], queryFn: adminApi.hospitals.list, enabled: isSuperAdmin });
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: adminApi.users.list, enabled: isSuperAdmin });
  const features = useQuery({ queryKey: ["admin", "features"], queryFn: adminApi.features, enabled: isSuperAdmin });
  const models = useQuery({ queryKey: ["admin", "models"], queryFn: adminApi.models, enabled: isSuperAdmin });
  const auditLogs = useQuery({ queryKey: ["admin", "audit-logs"], queryFn: () => adminApi.auditLogs(25), enabled: isSuperAdmin });

  const createHospital = useMutation({
    mutationFn: adminApi.hospitals.create,
    onSuccess: () => {
      setHospitalDialogOpen(false);
      setHospitalForm(initialHospitalForm);
      void queryClient.invalidateQueries({ queryKey: ["admin", "hospitals"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "audit-logs"] });
    },
  });
  const createUser = useMutation({
    mutationFn: adminApi.users.create,
    onSuccess: () => {
      setUserDialogOpen(false);
      setUserForm(initialUserForm);
      void queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "dashboard"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "audit-logs"] });
    },
  });

  const metrics = metricEntries(dashboard.data);
  const hospitalItems = hospitals.data ?? [];
  const userItems = users.data ?? [];
  const featureItems = features.data ?? [];
  const modelItems = models.data ?? [];
  const auditItems = auditLogs.data ?? [];

  function refreshAll() {
    void queryClient.invalidateQueries({ queryKey: ["admin"] });
  }

  function submitHospital() {
    const name = hospitalForm.name.trim();
    const code = hospitalForm.code.trim().toUpperCase();
    if (!name || !code) return;
    createHospital.mutate({ name, code });
  }

  function submitUser() {
    const displayName = userForm.display_name.trim();
    const email = userForm.email.trim();
    if (!displayName || !email || userForm.password.length < 12 || !userForm.organization_id) return;
    createUser.mutate({
      display_name: displayName,
      email,
      password: userForm.password,
      role: userForm.role,
      organization_id: userForm.organization_id,
    });
  }

  if (identity.isLoading) return <LoadingState label="Checking the authenticated platform role…" />;
  if (identity.isError) {
    return (
      <Stack spacing={2}>
        <Box><Typography variant="h4">Super Administrator workspace</Typography><Typography color="text.secondary" sx={{ mt: 0.5 }}>The current role must be verified before platform administration data is requested.</Typography></Box>
        <ApiErrorAlert error={identity.error} />
      </Stack>
    );
  }
  if (!isSuperAdmin) {
    return (
      <Paper sx={{ p: 4, maxWidth: 760 }}>
        <Stack direction="row" spacing={1.5} alignItems="flex-start">
          <SecurityOutlinedIcon color="warning" />
          <Box>
            <Typography variant="h5">Super Administrator access required</Typography>
            <Typography color="text.secondary" sx={{ mt: 0.75 }}>
              Your account is authenticated, but the server did not return the `SUPER_ADMIN` role. Platform-wide hospitals, users, model inventory, and audit data remain unavailable.
            </Typography>
          </Box>
        </Stack>
      </Paper>
    );
  }

  return (
    <Stack spacing={3}>
      <Box>
        <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ md: "flex-start" }} gap={2}>
          <Box>
            <Typography variant="overline" color="primary.main" sx={{ fontWeight: 800, letterSpacing: 1.6 }}>Platform governance · Super Administrator</Typography>
            <Typography variant="h4">Research platform administration</Typography>
            <Typography color="text.secondary" sx={{ mt: 0.75, maxWidth: 900 }}>
              Global operational records are loaded from the authorized API. This workspace administers platform configuration only; it does not diagnose patients, determine treatment, or replace clinical governance.
            </Typography>
          </Box>
          <Button variant="outlined" startIcon={<RefreshOutlinedIcon />} onClick={refreshAll}>Refresh authorized data</Button>
        </Stack>
      </Box>

      <Alert severity="warning" icon={<SecurityOutlinedIcon fontSize="inherit" />}>
        Feature `enabled` values and hospital `DISABLED` statuses are currently recorded configuration data. The backend does not yet use them as access revocation, route enforcement, or a clinical safety boundary.
      </Alert>

      {dashboard.isLoading ? <ResourceLoading label="Loading global operational metrics…" /> : dashboard.isError ? <ApiErrorAlert error={dashboard.error} /> : metrics.length ? (
        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))", xl: "repeat(3, minmax(0, 1fr))" }, gap: 2 }}>
          {metrics.map(([key, value], index) => (
            <Card key={key}>
              <CardContent>
                <Stack direction="row" spacing={1.5} alignItems="center">
                  <Box sx={{ p: 1, borderRadius: 2, bgcolor: "rgba(11, 93, 76, 0.08)", color: "primary.main", display: "grid", placeItems: "center" }}>{metricIcons[index % metricIcons.length]}</Box>
                  <Box><Typography variant="h5">{String(value)}</Typography><Typography variant="body2" color="text.secondary">{humanize(key)}</Typography></Box>
                </Stack>
              </CardContent>
            </Card>
          ))}
        </Box>
      ) : <Paper><EmptyState>No global metrics were returned by the authorized API.</EmptyState></Paper>}

      <Paper>
        <SectionHeader
          icon={<AddBusinessOutlinedIcon />}
          title="Hospital tenants"
          description="View the hospital tenants returned by the global administration API. New tenants are provisioned explicitly; no tenant information is inferred in the browser."
          action={<Button variant="contained" startIcon={<AddBusinessOutlinedIcon />} onClick={() => { createHospital.reset(); setHospitalDialogOpen(true); }}>Add hospital tenant</Button>}
        />
        {hospitals.isLoading ? <ResourceLoading label="Loading hospital tenants…" /> : hospitals.isError ? <ResourceFailure error={hospitals.error} /> : hospitalItems.length ? (
          <TableContainer>
            <Table size="small" aria-label="Hospital tenants">
              <TableHead><TableRow><TableCell>Hospital</TableCell><TableCell>Tenant code</TableCell><TableCell>Status record</TableCell><TableCell>Organization ID</TableCell></TableRow></TableHead>
              <TableBody>{hospitalItems.map((hospital) => (
                <TableRow key={hospital.organization_id} hover>
                  <TableCell><Typography fontWeight={700}>{hospital.name}</Typography></TableCell>
                  <TableCell>{hospital.code}</TableCell>
                  <TableCell><Chip size="small" label={humanize(hospital.status)} color={statusColor(hospital.status)} /></TableCell>
                  <TableCell sx={{ fontFamily: "monospace", fontSize: "0.75rem", maxWidth: 260, overflowWrap: "anywhere" }}>{hospital.organization_id}</TableCell>
                </TableRow>
              ))}</TableBody>
            </Table>
          </TableContainer>
        ) : <EmptyState>No hospital tenants were returned.</EmptyState>}
        <Box sx={{ px: 3, py: 1.75, borderTop: "1px solid #dce7e3", bgcolor: "rgba(168, 91, 0, 0.04)" }}><Typography variant="caption" color="text.secondary">Status is displayed exactly as returned. Changing it is intentionally not offered here because this backend revision does not enforce tenant suspension.</Typography></Box>
      </Paper>

      <Paper>
        <SectionHeader
          icon={<AdminPanelSettingsOutlinedIcon />}
          title="User provisioning directory"
          description="The API exposes a tenant-aware user directory and supports provisioning hospital administrators, doctors, and technicians. Existing user roles are not returned by the directory endpoint."
          action={<Button variant="contained" startIcon={<BadgeOutlinedIcon />} onClick={() => { createUser.reset(); setUserDialogOpen(true); }} disabled={hospitals.isLoading || hospitalItems.length === 0}>Provision user</Button>}
        />
        {users.isLoading ? <ResourceLoading label="Loading authorized users…" /> : users.isError ? <ResourceFailure error={users.error} /> : userItems.length ? (
          <TableContainer>
            <Table size="small" aria-label="Platform users">
              <TableHead><TableRow><TableCell>Display name</TableCell><TableCell>Email</TableCell><TableCell>Tenant</TableCell><TableCell>Account record</TableCell></TableRow></TableHead>
              <TableBody>{userItems.map((entry) => {
                const organization = hospitalItems.find((hospital) => hospital.organization_id === entry.organization_id);
                return <TableRow key={entry.user_id} hover><TableCell><Typography fontWeight={700}>{entry.display_name}</Typography></TableCell><TableCell>{entry.email}</TableCell><TableCell>{organization ? `${organization.name} (${organization.code})` : entry.organization_id ?? "Platform-scoped"}</TableCell><TableCell><Chip size="small" label={entry.active ? "Active" : "Inactive"} color={entry.active ? "success" : "default"} /></TableCell></TableRow>;
              })}</TableBody>
            </Table>
          </TableContainer>
        ) : <EmptyState>No users were returned by the authorized API.</EmptyState>}
        <Box sx={{ px: 3, py: 1.75, borderTop: "1px solid #dce7e3", bgcolor: "rgba(11, 93, 76, 0.035)" }}><Typography variant="caption" color="text.secondary">This API revision supports provisioning and listing only. It does not expose user role edits, deactivation, password reset, or assignment history in this workspace.</Typography></Box>
      </Paper>

      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", xl: "minmax(0, 1fr) minmax(0, 1fr)" }, gap: 3 }}>
        <Paper>
          <SectionHeader icon={<MemoryOutlinedIcon />} title="Registered model inventory" description="Model versions and provenance are read from the API. This service exposes no browser action to upload, promote, deactivate, or retrain a model." />
          {models.isLoading ? <ResourceLoading label="Loading registered model versions…" /> : models.isError ? <ResourceFailure error={models.error} /> : modelItems.length ? (
            <Stack divider={<Divider flexItem />}>
              {modelItems.map((model) => {
                const metadata = modelMetadata(model);
                return <Box key={model.model_version_uuid} sx={{ px: 3, py: 2.5 }}>
                  <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" gap={1.5}>
                    <Box><Typography fontWeight={800}>{model.model_name}</Typography><Typography variant="body2" color="text.secondary">Version {model.version} · {model.framework ?? "Framework not returned"}</Typography></Box>
                    <Chip size="small" label={humanize(model.status ?? "status not returned")} color={statusColor(model.status ?? undefined)} />
                  </Stack>
                  <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1.5 }}>Artifact fingerprint</Typography>
                  <Typography variant="caption" sx={{ display: "block", fontFamily: "monospace", overflowWrap: "anywhere" }}>{model.artifact_sha256 ?? "Not returned"}</Typography>
                  {metadata.length > 0 && <Stack direction="row" flexWrap="wrap" gap={1} sx={{ mt: 1.5 }}>{metadata.map(([key, value]) => <Chip key={key} size="small" variant="outlined" label={`${humanize(key)}: ${String(value)}`} />)}</Stack>}
                </Box>;
              })}
            </Stack>
          ) : <EmptyState>No registered model versions were returned.</EmptyState>}
        </Paper>

        <Paper>
          <SectionHeader icon={<FactCheckOutlinedIcon />} title="Feature configuration records" description="These are the feature rows returned by the platform. Their enabled values are visible for governance review but are not route-level enforcement controls in this backend version." />
          {features.isLoading ? <ResourceLoading label="Loading feature records…" /> : features.isError ? <ResourceFailure error={features.error} /> : featureItems.length ? (
            <Stack divider={<Divider flexItem />}>
              {featureItems.map((feature: PlatformFeature) => (
                <Box key={feature.feature_uuid} sx={{ px: 3, py: 2.25 }}>
                  <Stack direction="row" justifyContent="space-between" gap={2} alignItems="flex-start">
                    <Box><Typography fontWeight={800}>{humanize(feature.key)}</Typography><Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }}>{feature.key}</Typography></Box>
                    <Chip size="small" label={feature.enabled ? "Recorded enabled" : "Recorded disabled"} color={feature.enabled ? "success" : "default"} />
                  </Stack>
                  <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>{feature.description || "No description was returned."}</Typography>
                </Box>
              ))}
            </Stack>
          ) : <EmptyState>No feature records were returned.</EmptyState>}
        </Paper>
      </Box>

      <Paper>
        <SectionHeader icon={<HistoryOutlinedIcon />} title="Recent audit records" description="The most recent authorized audit events are shown as returned by the backend. This view does not invent user attribution, resource labels, or activity details." />
        {auditLogs.isLoading ? <ResourceLoading label="Loading platform audit records…" /> : auditLogs.isError ? <ResourceFailure error={auditLogs.error} /> : auditItems.length ? (
          <TableContainer>
            <Table size="small" aria-label="Recent audit records">
              <TableHead><TableRow><TableCell>Time</TableCell><TableCell>Action</TableCell><TableCell>Resource</TableCell><TableCell>Outcome</TableCell><TableCell>Request ID</TableCell></TableRow></TableHead>
              <TableBody>{auditItems.map((entry) => <TableRow key={entry.audit_id} hover><TableCell>{formatTimestamp(entry.timestamp)}</TableCell><TableCell>{entry.action ? humanize(entry.action) : "Not returned"}</TableCell><TableCell>{[entry.resource_type, entry.resource_id].filter(Boolean).join(" · ") || "Not returned"}</TableCell><TableCell><Chip size="small" label={entry.success === undefined ? "Not returned" : entry.success ? "Success" : "Failed"} color={entry.success === true ? "success" : entry.success === false ? "warning" : "default"} /></TableCell><TableCell sx={{ fontFamily: "monospace", fontSize: "0.75rem", overflowWrap: "anywhere" }}>{entry.request_id ?? "Not returned"}</TableCell></TableRow>)}</TableBody>
            </Table>
          </TableContainer>
        ) : <EmptyState>No audit events were returned.</EmptyState>}
      </Paper>

      <Dialog open={hospitalDialogOpen} onClose={() => !createHospital.isPending && setHospitalDialogOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Provision hospital tenant</DialogTitle>
        <DialogContent><Stack spacing={2.25} sx={{ pt: 1 }}>
          <Alert severity="info">This creates a tenant record through the authorized API. It does not automatically provision staff, patient data, or an external identity provider.</Alert>
          {createHospital.isError && <ApiErrorAlert error={createHospital.error} />}
          <TextField label="Hospital name" value={hospitalForm.name} onChange={(event) => setHospitalForm((value) => ({ ...value, name: event.target.value }))} autoFocus required inputProps={{ maxLength: 200 }} />
          <TextField label="Tenant code" helperText="2–48 letters, numbers, underscores, or hyphens." value={hospitalForm.code} onChange={(event) => setHospitalForm((value) => ({ ...value, code: event.target.value.toUpperCase() }))} required inputProps={{ maxLength: 48, pattern: "[A-Za-z0-9_-]+" }} />
        </Stack></DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5 }}><Button onClick={() => setHospitalDialogOpen(false)} disabled={createHospital.isPending}>Cancel</Button><Button variant="contained" onClick={submitHospital} disabled={!hospitalForm.name.trim() || !hospitalForm.code.trim() || createHospital.isPending}>{createHospital.isPending ? "Provisioning…" : "Provision hospital"}</Button></DialogActions>
      </Dialog>

      <Dialog open={userDialogOpen} onClose={() => !createUser.isPending && setUserDialogOpen(false)} fullWidth maxWidth="sm">
        <DialogTitle>Provision tenant user</DialogTitle>
        <DialogContent><Stack spacing={2.25} sx={{ pt: 1 }}>
          <Alert severity="info">The server assigns the selected role and tenant. Super Administrator and patient-linked accounts are intentionally excluded from this form. Use a unique, institution-managed password outside demo environments.</Alert>
          {createUser.isError && <ApiErrorAlert error={createUser.error} />}
          <TextField label="Display name" value={userForm.display_name} onChange={(event) => setUserForm((value) => ({ ...value, display_name: event.target.value }))} autoFocus required inputProps={{ maxLength: 200 }} />
          <TextField label="Email address" type="email" value={userForm.email} onChange={(event) => setUserForm((value) => ({ ...value, email: event.target.value }))} required inputProps={{ maxLength: 320 }} />
          <TextField label="Initial password" type="password" helperText="At least 12 characters. This is sent only to the authorized API over the current connection." value={userForm.password} onChange={(event) => setUserForm((value) => ({ ...value, password: event.target.value }))} required inputProps={{ minLength: 12, maxLength: 256 }} />
          <FormControl required><InputLabel id="provision-role-label">Role</InputLabel><Select labelId="provision-role-label" label="Role" value={userForm.role} onChange={(event) => setUserForm((value) => ({ ...value, role: event.target.value as ProvisionableRole }))}>{(Object.keys(roleLabels) as ProvisionableRole[]).map((role) => <MenuItem key={role} value={role}>{roleLabels[role]}</MenuItem>)}</Select></FormControl>
          <FormControl required><InputLabel id="provision-hospital-label">Hospital tenant</InputLabel><Select labelId="provision-hospital-label" label="Hospital tenant" value={userForm.organization_id} onChange={(event) => setUserForm((value) => ({ ...value, organization_id: event.target.value }))}>{hospitalItems.map((hospital: AdminHospital) => <MenuItem key={hospital.organization_id} value={hospital.organization_id}>{hospital.name} ({hospital.code})</MenuItem>)}</Select></FormControl>
        </Stack></DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5 }}><Button onClick={() => setUserDialogOpen(false)} disabled={createUser.isPending}>Cancel</Button><Button variant="contained" onClick={submitUser} disabled={!userForm.display_name.trim() || !userForm.email.trim() || userForm.password.length < 12 || !userForm.organization_id || createUser.isPending}>{createUser.isPending ? "Provisioning…" : "Provision user"}</Button></DialogActions>
      </Dialog>
    </Stack>
  );
}
