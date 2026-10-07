import AddTaskOutlinedIcon from "@mui/icons-material/AddTaskOutlined";
import ArrowBackOutlinedIcon from "@mui/icons-material/ArrowBackOutlined";
import AutoGraphOutlinedIcon from "@mui/icons-material/AutoGraphOutlined";
import CloudUploadOutlinedIcon from "@mui/icons-material/CloudUploadOutlined";
import DeleteOutlineOutlinedIcon from "@mui/icons-material/DeleteOutlineOutlined";
import ImageOutlinedIcon from "@mui/icons-material/ImageOutlined";
import PlayCircleOutlineOutlinedIcon from "@mui/icons-material/PlayCircleOutlineOutlined";
import SecurityOutlinedIcon from "@mui/icons-material/SecurityOutlined";
import VerifiedUserOutlinedIcon from "@mui/icons-material/VerifiedUserOutlined";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Checkbox,
  Chip,
  Divider,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  InputLabel,
  IconButton,
  LinearProgress,
  MenuItem,
  Paper,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Controller, useFieldArray, useForm } from "react-hook-form";
import { Link, useParams } from "react-router-dom";
import { z } from "zod";
import { ApiErrorAlert } from "../components/ApiErrorAlert";
import { LoadingState } from "../components/LoadingState";
import { EcgWaveformViewer } from "../components/EcgWaveformViewer";
import { ProtectedEcgPreview } from "../components/ProtectedEcgPreview";
import { DoctorVisualAccessInbox, VisualAccessRequestPanel, visualAccessEcgId, visualAccessRequestId } from "../components/VisualAccessControls";
import { useAuth } from "../lib/auth";
import { api, hasCurrentVisualAccessGrant } from "../lib/api";
import { listFromResponse, patientDisplayName, type EcgDigitizationResult, type EcgRecord, type EcgSecurityStatus, type EcgVisualAccessApproval, type EcgVisualAccessGrant, type EcgVisualAccessRequest, type MedicineSuggestion } from "../types/api";

const encounterSchema = z.object({
  encounter_type: z.string().trim().min(1, "Choose an encounter type."),
  reason: z.string().trim().max(500).optional(),
});
type EncounterValues = z.infer<typeof encounterSchema>;

const prescriptionItemSchema = z.object({
  medicine: z.string().trim().max(300).optional(),
  dose: z.string().trim().max(100).optional(),
  route: z.string().trim().max(100).optional(),
  frequency: z.string().trim().max(100).optional(),
  duration: z.string().trim().max(100).optional(),
  meal_timing: z.string().trim().max(100).optional(),
  instructions: z.string().trim().max(2000).optional(),
});

const reviewSchema = z.object({
  analysis_id: z.string().min(1, "Choose an analysis to review."),
  review_status: z.enum(["REVIEWED", "REQUIRES_FOLLOW_UP", "NOT_INTERPRETABLE"]),
  clinician_notes: z.string().trim().min(1, "Document the clinician assessment.").max(4000),
  diagnosis: z.string().trim().max(300).optional(),
  clinical_note: z.string().trim().max(8000).optional(),
  prescription_items: z.array(prescriptionItemSchema).max(20, "A review can include at most 20 medicines."),
});
type ReviewValues = z.infer<typeof reviewSchema>;

type PrescriptionDraft = z.infer<typeof prescriptionItemSchema>;

const emptyPrescriptionItem = (): PrescriptionDraft => ({
  medicine: "", dose: "", route: "", frequency: "", duration: "", meal_timing: "", instructions: "",
});

const frequencyOptions = [
  "1-0-0", "0-1-0", "0-0-1", "1-1-0", "1-0-1", "0-1-1", "1-1-1", "As documented",
];

const mealTimingOptions = ["Before food", "After food", "With food", "As documented"];

function optionalText(value?: string): string | undefined {
  const text = value?.trim();
  return text || undefined;
}

function medicationInstructions(item: PrescriptionDraft): string | undefined {
  const timing = optionalText(item.meal_timing);
  const instructions = optionalText(item.instructions);
  return [timing ? `Meal timing: ${timing}.` : undefined, instructions].filter(Boolean).join(" ") || undefined;
}

function formatDate(value?: string): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

function analysisLabel(label?: string): string {
  return label?.replace(/[_-]+/g, " ") || "No model label returned";
}

function confidenceValue(confidence?: number): number | undefined {
  if (typeof confidence !== "number" || Number.isNaN(confidence)) return undefined;
  return confidence <= 1 ? Math.round(confidence * 100) : Math.min(100, Math.round(confidence));
}

function isEcgImage(record: Pick<EcgRecord, "filename" | "source_filename" | "content_type" | "source_kind">): boolean {
  return record.source_kind === "ECG_IMAGE" || record.content_type === "image/jpeg" || /\.(jpe?g)$/i.test(record.filename ?? record.source_filename ?? "");
}

function isDigitizedEcg(record: Pick<EcgRecord, "source_kind" | "provenance">): boolean {
  const provenanceKind = typeof record.provenance === "string" ? record.provenance : record.provenance?.kind;
  return record.source_kind === "JPEG_DIGITIZED_WAVEFORM" || provenanceKind === "EXPERIMENTAL_JPEG_TRACE_DIGITIZATION";
}

function ecgFilename(ecg: Pick<EcgRecord, "filename" | "source_filename" | "id">): string {
  return ecg.filename ?? ecg.source_filename ?? `ECG ${ecg.id}`;
}

function SecurityStateChip({
  security,
  isLoading,
  isError,
}: {
  security?: EcgSecurityStatus;
  isLoading?: boolean;
  isError?: boolean;
}) {
  if (isLoading) return <Chip size="small" label="Checking encryption…" variant="outlined" />;
  if (isError || !security) return <Chip size="small" color="warning" label="Security status unavailable" variant="outlined" />;
  if (security.storage.encrypted_at_rest) {
    return <Chip size="small" color="success" icon={<SecurityOutlinedIcon />} label={`${security.storage.algorithm ?? "Encrypted"} at rest`} />;
  }
  if (security.storage.legacy_unencrypted) {
    return <Chip size="small" color="warning" label="Legacy · not encrypted" variant="outlined" />;
  }
  return <Chip size="small" color="warning" label="Not encrypted at rest" variant="outlined" />;
}

function RecordingSecurityStatus({ ecgId }: { ecgId: string }) {
  const security = useQuery({
    queryKey: ["ecg-security", ecgId],
    queryFn: () => api.ecgs.security(ecgId),
    staleTime: 5 * 60_000,
  });

  const detail = security.data?.storage.encrypted_at_rest
    ? `Server-confirmed ${security.data.storage.algorithm ?? "encryption"} · ${security.data.integrity.algorithm} integrity check`
    : security.data?.storage.legacy_unencrypted
      ? "Stored before at-rest encryption was enabled · re-upload or migrate to protect it"
      : security.data
        ? "The server did not report at-rest encryption for this object"
        : undefined;

  return (
    <Stack spacing={0.45} alignItems={{ xs: "flex-start", sm: "flex-end" }} sx={{ minWidth: { sm: 210 } }}>
      <SecurityStateChip security={security.data} isLoading={security.isLoading} isError={security.isError} />
      {detail && <Typography variant="caption" color="text.secondary" sx={{ textAlign: { sm: "right" } }}>{detail}</Typography>}
    </Stack>
  );
}

function RecentUploadProtection({ ecgId, filename }: { ecgId: string; filename: string }) {
  const security = useQuery({
    queryKey: ["ecg-security", ecgId],
    queryFn: () => api.ecgs.security(ecgId),
    staleTime: 5 * 60_000,
  });

  if (security.isLoading) {
    return <Alert severity="info" icon={<SecurityOutlinedIcon />}><Typography variant="body2">Upload complete. Confirming the server-side protection for <strong>{filename}</strong>…</Typography></Alert>;
  }
  if (security.isError || !security.data) {
    return <Alert severity="warning">Upload completed, but protection status could not be verified. Use the recording list to retry the server security check.</Alert>;
  }
  if (security.data.storage.encrypted_at_rest) {
    return (
      <Alert severity="success" icon={<SecurityOutlinedIcon />}>
        <Typography fontWeight={800}>Protected after upload</Typography>
        <Typography variant="body2"><strong>{filename}</strong> is stored with server-confirmed {security.data.storage.algorithm ?? "at-rest encryption"}. {security.data.integrity.algorithm} integrity verification is recorded for authorized reads.</Typography>
      </Alert>
    );
  }
  return (
    <Alert severity="warning">
      <Typography fontWeight={800}>Upload completed without confirmed at-rest encryption</Typography>
      <Typography variant="body2">The server reported this recording as {security.data.storage.legacy_unencrypted ? "a legacy asset" : "not encrypted"}. Do not treat it as encrypted until storage protection is configured or the record is migrated.</Typography>
    </Alert>
  );
}

function ResourceError({ error }: { error: unknown }) {
  return <Box sx={{ p: 2 }}><ApiErrorAlert error={error} /></Box>;
}

function saveDownload(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}

function GradCamViewer({
  ecgId,
  grant,
  temporaryVisualAccess = false,
  onClose,
}: {
  ecgId: string;
  grant?: EcgVisualAccessGrant;
  temporaryVisualAccess?: boolean;
  onClose: () => void;
}) {
  // Never use the secret token in a query key. The query is removed when the
  // temporary visual grant ends, and the API adds the grant header only after
  // checking that it is bound to this ECG and still current.
  const image = useQuery({ queryKey: ["gradcam", ecgId, grant?.expires_at ?? "doctor"], queryFn: () => api.analyses.explanation(ecgId, grant), staleTime: 0, gcTime: 0 });
  const [imageUrl, setImageUrl] = useState<string>();

  useEffect(() => {
    if (!image.data) {
      setImageUrl(undefined);
      return undefined;
    }
    const objectUrl = URL.createObjectURL(image.data);
    setImageUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [image.data]);

  return (
    <Paper sx={{ p: 2.5 }}>
      <Stack spacing={1.5}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" gap={2}>
          <Box><Typography variant="h6">Model-generated Grad-CAM</Typography><Typography variant="body2" color="text.secondary">{temporaryVisualAccess ? "Doctor-approved temporary visual access is active. This inline view expires automatically and cannot be downloaded." : "Relevance visualization from the saved research analysis; it is not a diagnosis."}</Typography></Box>
          <Button onClick={onClose} size="small">Hide</Button>
        </Stack>
        {image.isLoading && <LoadingState label="Loading authorized explanation…" />}
        {image.isError && <ApiErrorAlert error={image.error} />}
        {imageUrl && <Box component="img" src={imageUrl} alt="Authorized ECG Grad-CAM relevance visualization" sx={{ display: "block", width: "100%", maxWidth: 1120, border: "1px solid #dce7e3", borderRadius: 1.5 }} />}
      </Stack>
    </Paper>
  );
}

function AuthorizedEcgImageViewer({
  ecgId,
  filename,
  grant,
  temporaryVisualAccess = false,
  onClose,
}: {
  ecgId: string;
  filename: string;
  grant?: EcgVisualAccessGrant;
  temporaryVisualAccess?: boolean;
  onClose: () => void;
}) {
  // `/visual-image` is an inline-only endpoint. The original `/file` route is
  // never used for temporary visual grants and remains a literal doctor-only
  // download action.
  const image = useQuery({ queryKey: ["ecg-image", ecgId, grant?.expires_at ?? "doctor"], queryFn: () => api.ecgs.visualImage(ecgId, grant), staleTime: 0, gcTime: 0 });
  const [imageUrl, setImageUrl] = useState<string>();

  useEffect(() => {
    if (!image.data) {
      setImageUrl(undefined);
      return undefined;
    }
    const objectUrl = URL.createObjectURL(image.data);
    setImageUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [image.data]);

  return (
    <Paper sx={{ p: 2.5 }}>
      <Stack spacing={1.5}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" gap={2}>
          <Box><Typography variant="h6">{temporaryVisualAccess ? "Temporary doctor-approved ECG visual view" : "Doctor-authorized ECG image viewer"}</Typography><Typography variant="body2" color="text.secondary">{filename} is rendered through a protected inline visual endpoint. {temporaryVisualAccess ? `This view expires at ${formatDate(grant?.expires_at)}.` : "It is never sent directly to the raw-waveform AI model."}</Typography></Box>
          <Button onClick={onClose} size="small">Hide</Button>
        </Stack>
        <Alert severity="info">{temporaryVisualAccess ? "This temporary grant is view-only. It does not allow source-file download, signed object URLs, report export, or permanent access." : "JPEG ECG images can be reviewed here by a doctor. Source-file download remains a separate doctor-only action."} The explicit <strong>Convert &amp; analyze</strong> action can create a separately labelled experimental <strong>.mat</strong> or <strong>.csv</strong> derivative; it is not a clinically validated reconstruction.</Alert>
        {image.isLoading && <LoadingState label="Loading authorized ECG image…" />}
        {image.isError && <ApiErrorAlert error={image.error} />}
        {imageUrl && <Box component="img" src={imageUrl} alt={`Authorized ECG image: ${filename}`} sx={{ display: "block", width: "100%", maxWidth: 1120, maxHeight: 900, objectFit: "contain", bgcolor: "#f7faf9", border: "1px solid #dce7e3", borderRadius: 1.5 }} />}
      </Stack>
    </Paper>
  );
}

function MedicineAutocomplete({
  value,
  onChange,
  error,
  helperText,
}: {
  value?: string;
  onChange: (value: string) => void;
  error?: boolean;
  helperText?: string;
}) {
  const [inputValue, setInputValue] = useState(value ?? "");
  const searchText = inputValue.trim();
  const catalog = useQuery({
    queryKey: ["medicine-catalog", searchText],
    queryFn: () => api.medicines.search(searchText),
    enabled: searchText.length >= 2,
    staleTime: 60_000,
  });
  const options = catalog.data?.items ?? [];
  const catalogHint = catalog.isError
    ? "The local catalogue is temporarily unavailable. You may enter a clinician-selected medicine name manually."
    : catalog.data && !catalog.data.catalog_available
      ? "The local catalogue has not been installed on this server. You may enter a clinician-selected medicine name manually."
      : "Searchable local catalogue; selecting a name never fills dose or treatment instructions.";

  return (
    <Autocomplete<MedicineSuggestion, false, false, true>
      freeSolo
      fullWidth
      options={options}
      loading={catalog.isFetching}
      inputValue={inputValue}
      value={value || null}
      getOptionLabel={(option) => typeof option === "string" ? option : option.name}
      isOptionEqualToValue={(option, selected) => typeof selected !== "string" && option.id === selected.id}
      noOptionsText={searchText.length < 2 ? "Type at least 2 characters to search the local medicine catalogue" : "No catalogue matches"}
      onInputChange={(_event, nextValue, reason) => {
        setInputValue(nextValue);
        if (reason === "input" || reason === "clear") onChange(nextValue);
      }}
      onChange={(_event, nextValue) => {
        const nextName = typeof nextValue === "string" ? nextValue : nextValue?.name ?? "";
        setInputValue(nextName);
        onChange(nextName);
      }}
      renderOption={(props, option) => (
        <li {...props} key={option.id ?? option.name}>
          <Box>
            <Typography variant="body2" fontWeight={700}>{option.name}</Typography>
            {(option.composition || option.category) && <Typography variant="caption" color="text.secondary">{[option.composition, option.category].filter(Boolean).join(" · ")}</Typography>}
          </Box>
        </li>
      )}
      renderInput={(params) => (
        <TextField
          {...params}
          label="Medicine"
          error={error}
          helperText={helperText ?? catalogHint}
        />
      )}
    />
  );
}

export function PatientDetailPage() {
  const { patientId = "" } = useParams();
  const { user } = useAuth();
  const userRoles = user?.roles ?? (user?.role ? [user.role] : []);
  // The direct visual-source policy is intentionally stricter than the
  // platform's administrative roles: only a literal DOCTOR role gets normal
  // waveform/file controls. A non-doctor can render a narrowly scoped,
  // short-lived inline visual only after a server-approved grant.
  const canViewOriginalEcg = userRoles.some((role) => role.toUpperCase() === "DOCTOR");
  const queryClient = useQueryClient();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadEncounter, setUploadEncounter] = useState("");
  const [recentUpload, setRecentUpload] = useState<EcgRecord | null>(null);
  const [analysisEcg, setAnalysisEcg] = useState("");
  const [viewerEcgId, setViewerEcgId] = useState("");
  const [imageViewerEcgId, setImageViewerEcgId] = useState("");
  const [protectedPreviewEcgId, setProtectedPreviewEcgId] = useState("");
  const [explanationEcgId, setExplanationEcgId] = useState("");
  // View-only grant credentials intentionally live only in this component's
  // React state. They are never written to localStorage, sessionStorage, URLs,
  // query keys, or application logs.
  const [visualAccessGrants, setVisualAccessGrants] = useState<Record<string, EcgVisualAccessGrant>>({});
  const [grantClock, setGrantClock] = useState(() => Date.now());
  const [visualAccessApproval, setVisualAccessApproval] = useState<EcgVisualAccessApproval>();
  const [digitizationEcgId, setDigitizationEcgId] = useState("");
  const [digitizationOutputFormat, setDigitizationOutputFormat] = useState<"csv" | "mat">("csv");
  const [confirmExperimentalDigitization, setConfirmExperimentalDigitization] = useState(false);
  const [recentDigitization, setRecentDigitization] = useState<EcgDigitizationResult | null>(null);
  const securityEcgId = viewerEcgId || imageViewerEcgId || protectedPreviewEcgId;
  // Keep the client affordance aligned with the server RBAC policy: the
  // platform-wide SUPER_ADMIN role has the wildcard permission that includes
  // `ecg.analyze`, while hospital administrators do not receive this clinical
  // research capability by default.
  const canUseResearchModel = user?.roles?.some((role) => (
    role === "DOCTOR" || role === "TECHNICIAN" || role === "SUPER_ADMIN"
  )) ?? false;
  const canRecordClinicianReview = user?.roles?.some((role) => (
    role === "DOCTOR" || role === "SUPER_ADMIN"
  )) ?? false;
  const patient = useQuery({ queryKey: ["patient", patientId], queryFn: () => api.patients.get(patientId), enabled: Boolean(patientId) });
  const encounters = useQuery({ queryKey: ["encounters", patientId], queryFn: () => api.encounters.list(patientId), enabled: Boolean(patientId) });
  const ecgs = useQuery({ queryKey: ["ecgs", patientId], queryFn: () => api.ecgs.list(patientId), enabled: Boolean(patientId) });
  const analyses = useQuery({ queryKey: ["analyses", patientId], queryFn: () => api.analyses.list(patientId), enabled: Boolean(patientId) });
  const reviews = useQuery({ queryKey: ["reviews", patientId], queryFn: () => api.reviews.list(patientId), enabled: Boolean(patientId) });
  const reports = useQuery({ queryKey: ["reports", patientId], queryFn: () => api.reports.list(patientId), enabled: Boolean(patientId) });
  const hasVisualGrantFor = (ecgId: string): boolean => hasCurrentVisualAccessGrant(visualAccessGrants[ecgId], ecgId, grantClock);
  const canRenderOriginalFor = (ecgId: string): boolean => canViewOriginalEcg || hasVisualGrantFor(ecgId);
  const waveform = useQuery({
    queryKey: ["waveform", viewerEcgId],
    queryFn: () => api.ecgs.waveform(viewerEcgId),
    // Temporary grants render the server-side inline image instead of sending
    // raw numerical waveform samples to a non-doctor browser.
    enabled: canViewOriginalEcg && Boolean(viewerEcgId),
    staleTime: 0,
    gcTime: 0,
  });
  const security = useQuery({ queryKey: ["ecg-security", securityEcgId], queryFn: () => api.ecgs.security(securityEcgId), enabled: Boolean(securityEcgId), staleTime: 5 * 60_000 });
  const visualAccessStatus = useQuery({
    queryKey: ["ecg-visual-access-status", protectedPreviewEcgId],
    queryFn: () => api.ecgs.visualAccess.status(protectedPreviewEcgId),
    enabled: !canViewOriginalEcg && Boolean(protectedPreviewEcgId),
    refetchInterval: 10_000,
    retry: false,
  });
  const doctorVisualAccessInbox = useQuery({
    queryKey: ["doctor-visual-access-requests"],
    queryFn: () => api.ecgs.visualAccess.inbox(),
    enabled: canViewOriginalEcg,
    refetchInterval: 10_000,
    retry: false,
  });
  const researchModel = useQuery({ queryKey: ["configured-research-model"], queryFn: api.researchModel, enabled: canUseResearchModel, staleTime: 5 * 60_000 });
  const encounterForm = useForm<EncounterValues>({ resolver: zodResolver(encounterSchema), defaultValues: { encounter_type: "", reason: "" } });
  const reviewForm = useForm<ReviewValues>({ resolver: zodResolver(reviewSchema), defaultValues: {
    analysis_id: "", review_status: "REVIEWED", clinician_notes: "", diagnosis: "", clinical_note: "",
    prescription_items: [],
  } });
  const prescriptionItems = useFieldArray({ control: reviewForm.control, name: "prescription_items" });

  useEffect(() => {
    const timer = window.setInterval(() => setGrantClock(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    // A grant is meaningful only in the current patient workspace. Discard it
    // rather than carrying any temporary credential across route changes.
    setVisualAccessGrants({});
    setVisualAccessApproval(undefined);
  }, [patientId]);

  useEffect(() => {
    setVisualAccessGrants((current) => {
      const active = Object.fromEntries(Object.entries(current).filter(([ecgId, grant]) => hasCurrentVisualAccessGrant(grant, ecgId, grantClock)));
      return Object.keys(active).length === Object.keys(current).length ? current : active;
    });
  }, [grantClock]);

  useEffect(() => {
    const currentlyRenderedEcg = viewerEcgId || imageViewerEcgId || explanationEcgId;
    if (!canViewOriginalEcg && currentlyRenderedEcg && !hasVisualGrantFor(currentlyRenderedEcg)) {
      // A grant has expired or the account no longer has a valid visual-view
      // session. Immediately unmount source-bearing components and discard
      // their cached payloads before returning to the protected preview.
      setProtectedPreviewEcgId(currentlyRenderedEcg);
      setViewerEcgId("");
      setImageViewerEcgId("");
      setExplanationEcgId("");
      queryClient.removeQueries({ queryKey: ["waveform"] });
      queryClient.removeQueries({ queryKey: ["ecg-image"] });
      queryClient.removeQueries({ queryKey: ["gradcam"] });
    }
  }, [canViewOriginalEcg, explanationEcgId, grantClock, imageViewerEcgId, queryClient, viewerEcgId, visualAccessGrants]);

  const createEncounter = useMutation({
    mutationFn: (values: EncounterValues) => api.encounters.create({ ...values, patient_id: patientId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["encounters", patientId] });
      encounterForm.reset();
    },
  });
  const uploadEcg = useMutation({
    mutationFn: () => {
      if (!selectedFile) throw new Error("Choose a recording file before upload.");
      return api.ecgs.upload({ patientId, encounterId: uploadEncounter || undefined, file: selectedFile });
    },
    onSuccess: (record) => {
      queryClient.invalidateQueries({ queryKey: ["ecgs", patientId] });
      setRecentUpload(record);
      if (!canViewOriginalEcg) {
        setProtectedPreviewEcgId(record.id);
        setAnalysisEcg("");
        setViewerEcgId("");
        setImageViewerEcgId("");
      } else if (isEcgImage(record)) {
        setProtectedPreviewEcgId("");
        setAnalysisEcg("");
        setViewerEcgId("");
        setImageViewerEcgId(record.id);
      } else {
        setProtectedPreviewEcgId("");
        setAnalysisEcg(record.id);
        setViewerEcgId(record.id);
        setImageViewerEcgId("");
      }
      setSelectedFile(null);
      setUploadEncounter("");
    },
  });
  const requestAnalysis = useMutation({
    mutationFn: () => api.analyses.create({ ecg_id: analysisEcg }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["analyses", patientId] }),
  });
  const requestVisualAccess = useMutation({
    mutationFn: (ecgId: string) => api.ecgs.visualAccess.request(ecgId),
    onSuccess: (_result, ecgId) => {
      queryClient.invalidateQueries({ queryKey: ["ecg-visual-access-status", ecgId] });
      queryClient.invalidateQueries({ queryKey: ["doctor-visual-access-requests"] });
    },
  });
  const unlockVisualAccess = useMutation({
    mutationFn: (values: { ecgId: string; requestId: string; passcode: string }) => api.ecgs.visualAccess.unlock(values.ecgId, values.requestId, values.passcode),
    onSuccess: (grant, values) => {
      // Bind the credential to the selected ECG locally as an additional
      // client-side guard. The API independently binds/verifies it server-side.
      const boundGrant: EcgVisualAccessGrant = { ...grant, ecg_id: values.ecgId };
      setVisualAccessGrants((current) => ({ ...current, [values.ecgId]: boundGrant }));
      queryClient.invalidateQueries({ queryKey: ["ecg-visual-access-status", values.ecgId] });
      setProtectedPreviewEcgId("");
      setExplanationEcgId("");
      // A grant is intentionally visual-only: use the server's inline image
      // renderer for both JPEG and numerical ECG records. Do not expose raw
      // waveform samples, downloads, signed URLs, or report bytes.
      setViewerEcgId("");
      setImageViewerEcgId(values.ecgId);
    },
  });
  const approveVisualAccess = useMutation({
    mutationFn: (requestId: string) => api.ecgs.visualAccess.approve(requestId),
    onSuccess: (approval) => {
      setVisualAccessApproval(approval);
      queryClient.invalidateQueries({ queryKey: ["doctor-visual-access-requests"] });
      queryClient.invalidateQueries({ queryKey: ["ecg-visual-access-status"] });
    },
  });
  const insertResearchAssessment = useMutation({
    mutationFn: (ecgId: string) => api.analyses.assessmentSuggestion(ecgId),
    onSuccess: (draft) => {
      // This is an explicit clinician action. The result stays editable and
      // is not saved until the clinician submits the surrounding review form.
      reviewForm.setValue("clinician_notes", draft.suggestion, { shouldDirty: true, shouldValidate: true });
    },
  });
  const digitizeEcg = useMutation({
    mutationFn: () => {
      if (!digitizationEcgId) throw new Error("Choose a JPEG ECG image before requesting digitization.");
      return api.ecgs.digitize(digitizationEcgId, {
        output_format: digitizationOutputFormat,
        confirm_experimental: true,
      });
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["ecgs", patientId] });
      queryClient.invalidateQueries({ queryKey: ["analyses", patientId] });
      queryClient.invalidateQueries({ queryKey: ["ecg-security", result.derived_ecg.id] });
      setRecentDigitization(result);
      setAnalysisEcg(result.derived_ecg.id);
      setViewerEcgId(canViewOriginalEcg ? result.derived_ecg.id : "");
      setImageViewerEcgId("");
      setProtectedPreviewEcgId(canViewOriginalEcg ? "" : result.derived_ecg.id);
      setDigitizationEcgId("");
      setConfirmExperimentalDigitization(false);
    },
  });
  const submitReview = useMutation({
    mutationFn: (values: ReviewValues) => api.reviews.create({
      analysis_id: values.analysis_id,
      doctor_assessment: values.clinician_notes,
      review_status: values.review_status,
      diagnosis: values.diagnosis ? { display: values.diagnosis } : undefined,
      clinical_note: values.clinical_note || undefined,
      prescription_items: values.prescription_items.flatMap((item) => {
        const medicine = optionalText(item.medicine);
        return medicine ? [{
          medicine,
          dose: optionalText(item.dose),
          route: optionalText(item.route),
          frequency: optionalText(item.frequency),
          duration: optionalText(item.duration),
          instructions: medicationInstructions(item),
        }] : [];
      }),
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["reviews", patientId] });
      queryClient.invalidateQueries({ queryKey: ["analyses", patientId] });
      reviewForm.reset();
    },
  });
  const generateReport = useMutation({
    mutationFn: (reportId: string) => api.reports.generate(reportId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["reports", patientId] }),
  });
  const downloadReport = useMutation({
    mutationFn: async (report: { id: string; title?: string }) => {
      saveDownload(await api.reports.download(report.id), `${report.title ?? "ecg-report"}.pdf`);
    },
  });
  const downloadEcg = useMutation({
    mutationFn: async (ecg: { id: string; filename?: string; source_filename?: string }) => {
      saveDownload(await api.ecgs.download(ecg.id), ecg.filename ?? ecg.source_filename ?? "ecg-recording.mat");
    },
  });

  if (patient.isLoading) return <LoadingState label="Loading authorized patient workspace…" />;
  if (patient.isError) return <ApiErrorAlert error={patient.error} />;
  if (!patient.data) return <Alert severity="warning">No patient record was returned for this authorized route.</Alert>;

  const encounterItems = listFromResponse(encounters.data ?? []);
  const ecgItems = listFromResponse(ecgs.data ?? []);
  const analysisItems = listFromResponse(analyses.data ?? []);
  const selectedReviewAnalysis = analysisItems.find((analysis) => analysis.id === reviewForm.watch("analysis_id"));
  const reviewItems = listFromResponse(reviews.data ?? []);
  const reportItems = listFromResponse(reports.data ?? []);
  const patientEcgIds = new Set(ecgItems.map((ecg) => ecg.id));
  const doctorVisualRequests = listFromResponse<EcgVisualAccessRequest>(doctorVisualAccessInbox.data ?? [])
    .filter((request) => {
      const requestedEcgId = visualAccessEcgId(request);
      return requestedEcgId ? patientEcgIds.has(requestedEcgId) : false;
    })
    .map((request) => {
      const requestEcgId = visualAccessEcgId(request);
      const matchedRecord = requestEcgId ? ecgItems.find((ecg) => ecg.id === requestEcgId) : undefined;
      return matchedRecord && !request.filename ? { ...request, filename: ecgFilename(matchedRecord) } : request;
    });
  const viewedEcg = ecgItems.find((ecg) => ecg.id === viewerEcgId);
  const activeViewedEcg = viewedEcg ?? (recentDigitization?.derived_ecg.id === viewerEcgId ? recentDigitization.derived_ecg : undefined);
  const viewedEcgIsDigitized = isDigitizedEcg(activeViewedEcg ?? {});
  const viewedImageEcg = ecgItems.find((ecg) => ecg.id === imageViewerEcgId)
    ?? (recentDigitization?.derived_ecg.id === imageViewerEcgId ? recentDigitization.derived_ecg : undefined)
    ?? (recentUpload?.id === imageViewerEcgId ? recentUpload : undefined);
  const protectedPreviewEcg = ecgItems.find((ecg) => ecg.id === protectedPreviewEcgId)
    ?? (recentDigitization?.derived_ecg.id === protectedPreviewEcgId ? recentDigitization.derived_ecg : undefined)
    ?? (recentUpload?.id === protectedPreviewEcgId ? recentUpload : undefined);
  const digitizationSourceEcg = ecgItems.find((ecg) => ecg.id === digitizationEcgId);
  const waveformEcgItems = ecgItems.filter((ecg) => !isEcgImage(ecg));
  const selectedFileIsImage = /\.(jpe?g)$/i.test(selectedFile?.name ?? "");
  const recentDigitizationConfidence = confidenceValue(recentDigitization?.analysis.confidence);

  return (
    <Stack spacing={3}>
      <Button component={Link} to="/patients" startIcon={<ArrowBackOutlinedIcon />} sx={{ alignSelf: "flex-start" }}>Back to patients</Button>
      <Paper sx={{ p: 3 }}>
        <Stack spacing={1}>
          <Typography variant="overline" color="primary.main" sx={{ fontWeight: 800, letterSpacing: 1.5 }}>Patient workspace</Typography>
          <Typography variant="h4">{patientDisplayName(patient.data)}</Typography>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={2} color="text.secondary">
            <Typography>MRN: {patient.data.mrn ?? "—"}</Typography>
            <Typography>Date of birth: {formatDate(patient.data.date_of_birth)}</Typography>
            <Typography>Sex at birth: {patient.data.sex_at_birth ?? "—"}</Typography>
          </Stack>
          <Typography variant="caption" color="text.secondary">Patient-identifying data and permitted fields are returned by the backend according to active role and hospital tenant.</Typography>
        </Stack>
      </Paper>

      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", lg: "repeat(2, minmax(0, 1fr))" }, gap: 3 }}>
        <Box>
          <Paper component="form" onSubmit={encounterForm.handleSubmit((values) => createEncounter.mutate(values))} sx={{ p: 3, height: "100%" }}>
            <Stack spacing={2}>
              <Box><Typography variant="h6">Create encounter</Typography><Typography variant="body2" color="text.secondary">Record the context before attaching an ECG.</Typography></Box>
              {createEncounter.isError && <ApiErrorAlert error={createEncounter.error} />}
              <TextField label="Encounter type" placeholder="e.g. outpatient review" required error={Boolean(encounterForm.formState.errors.encounter_type)} helperText={encounterForm.formState.errors.encounter_type?.message} {...encounterForm.register("encounter_type")} />
              <TextField label="Reason / context" multiline minRows={3} error={Boolean(encounterForm.formState.errors.reason)} helperText={encounterForm.formState.errors.reason?.message} {...encounterForm.register("reason")} />
              <Button type="submit" variant="outlined" startIcon={<AddTaskOutlinedIcon />} disabled={createEncounter.isPending}>{createEncounter.isPending ? "Saving…" : "Create encounter"}</Button>
            </Stack>
          </Paper>
        </Box>
        <Box>
          <Paper sx={{ p: 3, height: "100%" }}>
            <Stack spacing={2}>
              <Box><Typography variant="h6">Upload ECG recording</Typography><Typography variant="body2" color="text.secondary">The server validates file type, stores it through the authorized API, and records an audit event.</Typography></Box>
              <Alert severity="info" icon={<SecurityOutlinedIcon />} sx={{ py: 0.25 }}>
                <Typography variant="body2" fontWeight={800}>Encryption is verified after upload</Typography>
                <Typography variant="caption">Every completed upload receives a server-derived encryption and SHA-256 integrity status below. A record is only labeled encrypted when the storage service confirms it.</Typography>
              </Alert>
              {uploadEcg.isError && <ApiErrorAlert error={uploadEcg.error} />}
              <FormControl fullWidth>
                <InputLabel id="upload-encounter-label">Associated encounter</InputLabel>
                <Select labelId="upload-encounter-label" label="Associated encounter" value={uploadEncounter} onChange={(event) => setUploadEncounter(event.target.value)}>
                  <MenuItem value=""><em>Choose an encounter</em></MenuItem>
                  {encounterItems.map((encounter) => <MenuItem key={encounter.id} value={encounter.id}>{encounter.encounter_type ?? "Encounter"} · {formatDate(encounter.occurred_at ?? encounter.created_at)}</MenuItem>)}
                </Select>
              </FormControl>
              <Button component="label" variant="outlined" startIcon={<CloudUploadOutlinedIcon />}>
                {selectedFile ? selectedFile.name : "Choose ECG file"}
                <input hidden type="file" accept=".mat,.csv,.jpg,.jpeg,image/jpeg" onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)} />
              </Button>
              {selectedFile && (
                <Alert severity={selectedFileIsImage ? "info" : "success"} sx={{ py: 0.25 }}>
                  {selectedFileIsImage
                    ? "JPEG ECG image selected. It will be retained for authorized visual review; raw-waveform prediction requires a .mat or .csv recording."
                    : "Waveform file selected. Its stored encryption status and integrity check will be verified after upload."}
                </Alert>
              )}
              <Button variant="contained" onClick={() => uploadEcg.mutate()} disabled={!selectedFile || !uploadEncounter || uploadEcg.isPending}>{uploadEcg.isPending ? "Uploading…" : "Upload recording"}</Button>
              {recentUpload && <RecentUploadProtection ecgId={recentUpload.id} filename={ecgFilename(recentUpload)} />}
            </Stack>
          </Paper>
        </Box>
      </Box>

      <Paper sx={{ overflow: "hidden" }}>
        <Box sx={{ p: 3, borderBottom: "1px solid #dce7e3" }}><Typography variant="h6">ECG recordings</Typography><Typography variant="body2" color="text.secondary">Each item below shows a server-derived at-rest encryption and integrity status. Files are returned through backend-authorized URLs.</Typography></Box>
        {ecgs.isError ? <ResourceError error={ecgs.error} /> : ecgs.isLoading ? <LoadingState label="Loading recordings…" /> : (
          <Stack divider={<Divider flexItem />}>
            {ecgItems.map((ecg) => {
              const isImage = isEcgImage(ecg);
              const canRenderThisEcg = canRenderOriginalFor(ecg.id);
              const temporaryGrant = visualAccessGrants[ecg.id];
              return (
                <Box key={ecg.id} sx={{ p: 2.5, display: "flex", gap: 2, alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
                  <Box sx={{ minWidth: 0, flex: "1 1 250px" }}>
                    <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                      <Typography fontWeight={700}>{ecgFilename(ecg)}</Typography>
                      {isImage && <Chip size="small" label="JPEG source image" color="info" variant="outlined" />}
                      {isDigitizedEcg(ecg) && <Chip size="small" label="Experimental JPEG-derived waveform" color="warning" variant="outlined" />}
                    </Stack>
                    <Typography variant="body2" color="text.secondary">Uploaded/acquired: {formatDate(ecg.acquired_at ?? ecg.created_at)} · Status: {ecg.status ?? "unknown"}</Typography>
                    {isImage && <Typography variant="caption" color="text.secondary">This source image is never sent directly to RAMNV2. An authorized user may explicitly create a separate experimental numerical derivative.</Typography>}
                    {isDigitizedEcg(ecg) && <Typography variant="caption" color="warning.dark">Derived from a JPEG trace extraction. Treat waveform and score as research artifacts requiring clinician review.</Typography>}
                    {!canViewOriginalEcg && !canRenderThisEcg && <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.4 }}>Original ECG pixels and waveform values are locked for this role. Only a non-clinical protected camouflage preview is available until a doctor approves a temporary visual grant.</Typography>}
                    {!canViewOriginalEcg && canRenderThisEcg && <Typography variant="caption" color="success.dark" sx={{ display: "block", mt: 0.4 }}>Temporary doctor-approved visual access is active until {formatDate(temporaryGrant?.expires_at)}. It is view-only and cannot be downloaded.</Typography>}
                  </Box>
                  <RecordingSecurityStatus ecgId={ecg.id} />
                  <Stack direction="row" spacing={1}>
                    {canViewOriginalEcg ? (
                      isImage
                        ? <Button onClick={() => { setImageViewerEcgId(ecg.id); setViewerEcgId(""); setProtectedPreviewEcgId(""); }} size="small" startIcon={<ImageOutlinedIcon />}>View image</Button>
                        : <Button onClick={() => { setViewerEcgId(ecg.id); setImageViewerEcgId(""); setProtectedPreviewEcgId(""); }} size="small">View waveform</Button>
                    ) : canRenderThisEcg ? (
                      <Button onClick={() => { setImageViewerEcgId(ecg.id); setViewerEcgId(""); setProtectedPreviewEcgId(""); setExplanationEcgId(""); }} size="small" color="success" startIcon={<ImageOutlinedIcon />}>Open temporary visual view</Button>
                    ) : (
                      <Button onClick={() => { setProtectedPreviewEcgId(ecg.id); setViewerEcgId(""); setImageViewerEcgId(""); setExplanationEcgId(""); }} size="small" startIcon={<SecurityOutlinedIcon />}>View protected preview</Button>
                    )}
                    {isImage && canUseResearchModel && (
                      <Button
                        size="small"
                        color="warning"
                        startIcon={<AutoGraphOutlinedIcon />}
                        onClick={() => {
                          digitizeEcg.reset();
                          setRecentDigitization(null);
                          setDigitizationOutputFormat("csv");
                          setConfirmExperimentalDigitization(false);
                          setDigitizationEcgId(ecg.id);
                        }}
                      >
                        Convert &amp; analyze
                      </Button>
                    )}
                    {canViewOriginalEcg ? (
                      <Button onClick={() => downloadEcg.mutate(ecg)} disabled={downloadEcg.isPending} size="small">
                        {downloadEcg.isPending ? "Preparing download…" : "Download source file"}
                      </Button>
                    ) : <Chip size="small" icon={<SecurityOutlinedIcon />} label="Source download locked" variant="outlined" />}
                  </Stack>
                </Box>
              );
            })}
            {!ecgItems.length && <Box sx={{ p: 3 }}><Typography color="text.secondary">No ECG recordings were returned.</Typography></Box>}
          </Stack>
        )}
      </Paper>

      {canViewOriginalEcg && (
        <DoctorVisualAccessInbox
          requests={doctorVisualRequests}
          isLoading={doctorVisualAccessInbox.isLoading}
          error={doctorVisualAccessInbox.error}
          approvingRequestId={approveVisualAccess.isPending ? approveVisualAccess.variables : undefined}
          approval={visualAccessApproval}
          approvalError={approveVisualAccess.error}
          onApprove={(requestId) => {
            setVisualAccessApproval(undefined);
            approveVisualAccess.mutate(requestId);
          }}
          onDismissApproval={() => setVisualAccessApproval(undefined)}
        />
      )}

      <Dialog
        open={Boolean(digitizationEcgId)}
        onClose={() => {
          if (!digitizeEcg.isPending) setDigitizationEcgId("");
        }}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>Experimental JPEG trace digitization</DialogTitle>
        <DialogContent dividers>
          <Stack spacing={2} sx={{ pt: 0.5 }}>
            <Typography variant="body2" color="text.secondary">
              Create a separate numerical ECG derivative from <strong>{digitizationSourceEcg ? ecgFilename(digitizationSourceEcg) : "the selected JPEG image"}</strong> and send only that derivative to the configured RAMNV2 research classifier.
            </Typography>
            <Alert severity="warning">
              <Typography fontWeight={800}>Research-only, not clinically validated</Typography>
              <Typography variant="body2">
                This process infers a plotted signal trace from pixels. It can lose calibration, lead identity, paper-grid details, image artifacts, or portions of the waveform. It is not a diagnosis, treatment recommendation, or replacement for the original numerical acquisition.
              </Typography>
            </Alert>
            <Alert severity="info">
              The original JPEG remains unchanged. The server creates a separately encrypted derived waveform, records digitization provenance, and runs one research analysis on that derivative.
            </Alert>
            <FormControl fullWidth>
              <InputLabel id="digitization-format-label">Derived waveform format</InputLabel>
              <Select
                labelId="digitization-format-label"
                label="Derived waveform format"
                value={digitizationOutputFormat}
                onChange={(event) => setDigitizationOutputFormat(event.target.value as "csv" | "mat")}
              >
                <MenuItem value="csv">CSV numerical waveform</MenuItem>
                <MenuItem value="mat">MAT numerical waveform</MenuItem>
              </Select>
            </FormControl>
            <FormControlLabel
              control={<Checkbox checked={confirmExperimentalDigitization} onChange={(event) => setConfirmExperimentalDigitization(event.target.checked)} />}
              label="I understand that this is an experimental image-to-signal extraction and its RAMNV2 result is research-only."
            />
            {digitizeEcg.isError && <ApiErrorAlert error={digitizeEcg.error} />}
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, py: 2 }}>
          <Button disabled={digitizeEcg.isPending} onClick={() => setDigitizationEcgId("")}>Cancel</Button>
          <Button
            variant="contained"
            color="warning"
            startIcon={<AutoGraphOutlinedIcon />}
            disabled={!confirmExperimentalDigitization || digitizeEcg.isPending}
            onClick={() => digitizeEcg.mutate()}
          >
            {digitizeEcg.isPending ? "Converting & analyzing…" : "Create derivative & analyze"}
          </Button>
        </DialogActions>
      </Dialog>

      {recentDigitization && (
        <Paper sx={{ p: 2.5, borderColor: "warning.light", bgcolor: "rgba(237, 108, 2, 0.035)" }}>
          <Stack spacing={1.25}>
            <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "center" }} gap={1}>
              <Box>
                <Typography variant="overline" color="warning.dark">Experimental JPEG digitization completed</Typography>
                <Typography fontWeight={800}>{analysisLabel(recentDigitization.analysis.predicted_label ?? recentDigitization.analysis.prediction_label)}</Typography>
                <Typography variant="body2" color="text.secondary">
                  Derived {recentDigitization.digitization.output_format.toUpperCase()} waveform analyzed by {recentDigitization.analysis.model_version ?? "the configured RAMNV2 research model"}.
                </Typography>
              </Box>
              <Chip size="small" color="warning" variant="outlined" label="Clinician review required" />
            </Stack>
            {recentDigitizationConfidence !== undefined && (
              <Box>
                <Stack direction="row" justifyContent="space-between"><Typography variant="caption">Uncalibrated research score</Typography><Typography variant="caption">{recentDigitizationConfidence}%</Typography></Stack>
                <LinearProgress color="warning" variant="determinate" value={recentDigitizationConfidence} sx={{ mt: 0.5, height: 7, borderRadius: 10 }} />
              </Box>
            )}
            <Typography variant="caption" color="text.secondary">{recentDigitization.digitization.limitations}</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              {canViewOriginalEcg ? (
                <>
                  <Button size="small" onClick={() => { setViewerEcgId(recentDigitization.derived_ecg.id); setProtectedPreviewEcgId(""); }}>View derived waveform</Button>
                  <Button size="small" onClick={() => setExplanationEcgId(recentDigitization.derived_ecg.id)} disabled={!recentDigitization.analysis.has_explanation}>View Grad-CAM</Button>
                </>
              ) : canRenderOriginalFor(recentDigitization.derived_ecg.id) ? (
                <>
                  <Button size="small" color="success" startIcon={<ImageOutlinedIcon />} onClick={() => { setImageViewerEcgId(recentDigitization.derived_ecg.id); setViewerEcgId(""); setProtectedPreviewEcgId(""); }}>Open temporary visual view</Button>
                  {recentDigitization.analysis.has_explanation && <Button size="small" color="success" onClick={() => setExplanationEcgId(recentDigitization.derived_ecg.id)}>View temporary Grad-CAM</Button>}
                </>
              ) : <Button size="small" startIcon={<SecurityOutlinedIcon />} onClick={() => setProtectedPreviewEcgId(recentDigitization.derived_ecg.id)}>View protected preview</Button>}
            </Stack>
          </Stack>
        </Paper>
      )}

      {viewerEcgId && canRenderOriginalFor(viewerEcgId) && (
        canViewOriginalEcg ? (
          <Paper sx={{ p: 2.5 }}>
            <Stack spacing={1.5}>
              <Stack direction="row" alignItems="center" justifyContent="space-between" gap={2}>
                <Box>
                  <Typography variant="h6">{viewedEcgIsDigitized ? "Experimental derived ECG viewer" : "Doctor-authorized ECG viewer"}</Typography>
                  <Typography variant="body2" color="text.secondary">
                    {viewedEcgIsDigitized
                      ? `This numerical waveform was estimated from ${activeViewedEcg ? ecgFilename(activeViewedEcg) : "a JPEG ECG source image"}. It is not the original device acquisition and is shown for research review only.`
                      : `Single-lead source waveform from ${activeViewedEcg ? ecgFilename(activeViewedEcg) : "the selected recording"}. Zoom and pan happen only in this browser session.`}
                  </Typography>
                </Box>
                <Button onClick={() => setViewerEcgId("")} size="small">Hide</Button>
              </Stack>
              {waveform.isLoading && <LoadingState label="Loading authorized waveform…" />}
              {waveform.isError && <ApiErrorAlert error={waveform.error} />}
              {waveform.data && <EcgWaveformViewer samples={waveform.data.samples} samplingRateHz={waveform.data.sampling_rate_hz} title={viewedEcgIsDigitized ? "Experimental JPEG-derived ECG waveform" : "Authorized single-lead ECG waveform"} />}
            </Stack>
          </Paper>
        ) : (
          <AuthorizedEcgImageViewer
            ecgId={viewerEcgId}
            filename={activeViewedEcg ? ecgFilename(activeViewedEcg) : "Selected ECG recording"}
            grant={visualAccessGrants[viewerEcgId]}
            temporaryVisualAccess
            onClose={() => setViewerEcgId("")}
          />
        )
      )}

      {imageViewerEcgId && viewedImageEcg && canRenderOriginalFor(imageViewerEcgId) && (
        <AuthorizedEcgImageViewer
          ecgId={imageViewerEcgId}
          filename={ecgFilename(viewedImageEcg)}
          grant={canViewOriginalEcg ? undefined : visualAccessGrants[imageViewerEcgId]}
          temporaryVisualAccess={!canViewOriginalEcg}
          onClose={() => setImageViewerEcgId("")}
        />
      )}

      {!canViewOriginalEcg && protectedPreviewEcgId && protectedPreviewEcg && (
        <Stack spacing={2}>
          <ProtectedEcgPreview
            ecgId={protectedPreviewEcgId}
            filename={ecgFilename(protectedPreviewEcg)}
            security={security.data}
            securityIsLoading={security.isLoading}
            onClose={() => setProtectedPreviewEcgId("")}
          />
          <VisualAccessRequestPanel
            status={visualAccessStatus.data ?? undefined}
            isLoading={visualAccessStatus.isLoading}
            statusError={visualAccessStatus.error}
            requestPending={requestVisualAccess.isPending}
            requestError={requestVisualAccess.error}
            unlockPending={unlockVisualAccess.isPending}
            unlockError={unlockVisualAccess.error}
            onRequest={() => {
              requestVisualAccess.reset();
              unlockVisualAccess.reset();
              requestVisualAccess.mutate(protectedPreviewEcgId);
            }}
            onUnlock={(passcode) => {
              const requestId = visualAccessRequestId(visualAccessStatus.data ?? undefined);
              if (!requestId) return;
              unlockVisualAccess.reset();
              unlockVisualAccess.mutate({ ecgId: protectedPreviewEcgId, requestId, passcode });
            }}
            onRefresh={() => { void visualAccessStatus.refetch(); }}
          />
        </Stack>
      )}

      {securityEcgId && (
        <Paper sx={{ p: 2.5, borderColor: security.data?.storage.encrypted_at_rest ? "rgba(8, 123, 131, 0.34)" : "warning.light" }}>
          <Stack spacing={1.5}>
            <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "center" }} justifyContent="space-between" gap={1.5}>
              <Stack direction="row" spacing={1.25} alignItems="center">
                <Box sx={{ display: "grid", placeItems: "center", width: 38, height: 38, borderRadius: 2, color: "common.white", bgcolor: security.data?.storage.encrypted_at_rest ? "primary.main" : "warning.main" }}><SecurityOutlinedIcon fontSize="small" /></Box>
                <Box><Typography variant="h6">Security &amp; integrity</Typography><Typography variant="body2" color="text.secondary">Backend-derived protection details for this authorized ECG asset.</Typography></Box>
              </Stack>
              {security.data && <Chip color={security.data.storage.encrypted_at_rest ? "success" : "warning"} label={security.data.storage.encrypted_at_rest ? "Encrypted at rest" : "Legacy asset"} size="small" />}
            </Stack>
            {security.isLoading && <LinearProgress />}
            {security.isError && <ApiErrorAlert error={security.error} />}
            {security.data && (
              <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "repeat(3, minmax(0, 1fr))" }, gap: 1.5 }}>
                <Box sx={{ p: 1.75, borderRadius: 2, bgcolor: "rgba(8, 123, 131, 0.07)" }}><Typography variant="overline" color="primary.main">At-rest protection</Typography><Typography fontWeight={800}>{security.data.storage.algorithm ?? "Not encrypted"}</Typography><Typography variant="caption" color="text.secondary">{security.data.storage.authenticated_encryption ? "Authenticated encryption with per-object nonce." : security.data.storage.key_management}</Typography></Box>
                <Box sx={{ p: 1.75, borderRadius: 2, bgcolor: "rgba(17, 87, 167, 0.06)" }}><Typography variant="overline" color="secondary.main">Integrity verification</Typography><Typography fontWeight={800}>{security.data.integrity.algorithm}</Typography><Typography variant="caption" color="text.secondary">Verified on authorized reads · fingerprint {security.data.integrity.fingerprint ?? "not returned"}</Typography></Box>
                <Box sx={{ p: 1.75, borderRadius: 2, bgcolor: "rgba(22, 47, 66, 0.05)" }}><Typography variant="overline" color="text.secondary">Access control</Typography><Typography fontWeight={800}>Tenant-scoped RBAC</Typography><Typography variant="caption" color="text.secondary">Authorized access and security-status checks are audit logged.</Typography></Box>
              </Box>
            )}
            {security.data?.storage.legacy_unencrypted && <Alert severity="warning">This is a legacy local asset and remains readable for safety. New uploads use AES-256-GCM; migrate or re-upload this record before treating it as encrypted at rest.</Alert>}
            {security.data && <Typography variant="caption" color="text.secondary">{security.data.research_camouflage.reason}</Typography>}
          </Stack>
        </Paper>
      )}

      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", lg: "minmax(0, 5fr) minmax(0, 7fr)" }, gap: 3 }}>
        <Box>
          <Paper sx={{ p: 3, height: "100%" }}>
            <Stack spacing={2}>
              <Box><Typography variant="h6">Request research analysis</Typography><Typography variant="body2" color="text.secondary">This requests an AI analysis; it does not create a diagnosis or treatment plan.</Typography></Box>
              {researchModel.isLoading && <LinearProgress />}
              {researchModel.isError && <ApiErrorAlert error={researchModel.error} />}
              {researchModel.data && (
                <Box sx={{ p: 1.75, borderRadius: 2, bgcolor: "rgba(17, 87, 167, 0.06)", border: "1px solid rgba(17, 87, 167, 0.14)" }}>
                  <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" gap={1}>
                    <Box>
                      <Typography variant="overline" color="secondary.main">Configured research model</Typography>
                      <Typography fontWeight={800}>{researchModel.data.model_name}</Typography>
                      <Typography variant="body2" color="text.secondary">Version {researchModel.data.version} · {researchModel.data.framework} · {researchModel.data.architecture}</Typography>
                    </Box>
                    <Chip size="small" color="info" label="Research-only" sx={{ alignSelf: { xs: "flex-start", sm: "center" } }} />
                  </Stack>
                  <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>{researchModel.data.supported_source_formats.join(", ")} are native classifier inputs. {researchModel.data.image_source_formats.join(", ")} are not direct model inputs; only an explicitly requested experimental numerical derivative may be analyzed.</Typography>
                </Box>
              )}
              {requestAnalysis.isError && <ApiErrorAlert error={requestAnalysis.error} />}
              <FormControl fullWidth>
                <InputLabel id="analysis-ecg-label">ECG recording</InputLabel>
                <Select labelId="analysis-ecg-label" label="ECG recording" value={analysisEcg} onChange={(event) => setAnalysisEcg(event.target.value)}>
                  <MenuItem value=""><em>Choose a .mat or .csv recording</em></MenuItem>
                  {waveformEcgItems.map((ecg) => <MenuItem key={ecg.id} value={ecg.id}>{ecgFilename(ecg)}{isDigitizedEcg(ecg) ? " · experimental JPEG-derived waveform" : ""}</MenuItem>)}
                </Select>
              </FormControl>
              {!waveformEcgItems.length && <Typography variant="caption" color="text.secondary">Upload a native .mat or .csv waveform, or explicitly create an experimental numerical derivative from a JPEG source image before requesting analysis.</Typography>}
              <Button variant="contained" startIcon={<PlayCircleOutlineOutlinedIcon />} onClick={() => requestAnalysis.mutate()} disabled={!analysisEcg || requestAnalysis.isPending}>{requestAnalysis.isPending ? "Requesting…" : "Request analysis"}</Button>
            </Stack>
          </Paper>
        </Box>
        <Box>
          <Paper sx={{ overflow: "hidden", height: "100%" }}>
            <Box sx={{ p: 3, borderBottom: "1px solid #dce7e3" }}>
              <Typography variant="h6">AI research analyses</Typography>
              <Typography variant="body2" color="text.secondary">Scores are not calibrated disease probabilities and require qualified clinician review.</Typography>
            </Box>
            {analyses.isError ? <ResourceError error={analyses.error} /> : analyses.isLoading ? <LoadingState label="Loading analyses…" /> : (
              <Stack divider={<Divider flexItem />}>
                {analysisItems.map((analysis) => {
                  const confidence = confidenceValue(analysis.confidence);
                  const canRenderAnalysisVisual = Boolean(analysis.ecg_id && canRenderOriginalFor(analysis.ecg_id));
                  return (
                    <Box key={analysis.id} sx={{ p: 2.5 }}>
                      <Stack direction="row" justifyContent="space-between" gap={2} alignItems="flex-start">
                        <Box>
                          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                            <Typography fontWeight={700}>{analysisLabel(analysis.predicted_label ?? analysis.prediction_label)}</Typography>
                            {analysis.ecg_id && isDigitizedEcg(ecgItems.find((ecg) => ecg.id === analysis.ecg_id) ?? {}) && <Chip size="small" color="warning" variant="outlined" label="Experimental JPEG-derived input" />}
                          </Stack>
                          <Typography variant="body2" color="text.secondary">Model: {analysis.model_version ?? "not returned"} · {formatDate(analysis.created_at)}</Typography>
                        </Box>
                        <Chip size="small" label={analysis.status ?? "pending clinician review"} color="warning" variant="outlined" />
                      </Stack>
                      {confidence !== undefined && <Box sx={{ mt: 1.5 }}><Stack direction="row" justifyContent="space-between"><Typography variant="caption">Uncalibrated model score</Typography><Typography variant="caption">{confidence}%</Typography></Stack><LinearProgress variant="determinate" value={confidence} sx={{ mt: 0.5, height: 7, borderRadius: 10 }} /></Box>}
                      <Stack direction="row" spacing={1} sx={{ mt: 1.5 }}>
                        {analysis.ecg_id && canViewOriginalEcg && <Button size="small" onClick={() => { setViewerEcgId(analysis.ecg_id!); setProtectedPreviewEcgId(""); }}>View waveform</Button>}
                        {analysis.ecg_id && canViewOriginalEcg && analysis.has_explanation && <Button size="small" onClick={() => setExplanationEcgId(analysis.ecg_id!)}>View Grad-CAM</Button>}
                        {analysis.ecg_id && !canViewOriginalEcg && canRenderAnalysisVisual && <Button size="small" color="success" startIcon={<ImageOutlinedIcon />} onClick={() => { setImageViewerEcgId(analysis.ecg_id!); setViewerEcgId(""); setProtectedPreviewEcgId(""); setExplanationEcgId(""); }}>Open temporary visual view</Button>}
                        {analysis.ecg_id && !canViewOriginalEcg && canRenderAnalysisVisual && analysis.has_explanation && <Button size="small" color="success" onClick={() => setExplanationEcgId(analysis.ecg_id!)}>View temporary Grad-CAM</Button>}
                        {analysis.ecg_id && !canViewOriginalEcg && !canRenderAnalysisVisual && <Button size="small" startIcon={<SecurityOutlinedIcon />} onClick={() => { setProtectedPreviewEcgId(analysis.ecg_id!); setViewerEcgId(""); setImageViewerEcgId(""); setExplanationEcgId(""); }}>View protected preview</Button>}
                      </Stack>
                    </Box>
                  );
                })}
                {!analysisItems.length && <Box sx={{ p: 3 }}><Typography color="text.secondary">No analyses were returned.</Typography></Box>}
              </Stack>
            )}
          </Paper>
        </Box>
      </Box>

      {explanationEcgId && canRenderOriginalFor(explanationEcgId) && <GradCamViewer ecgId={explanationEcgId} grant={canViewOriginalEcg ? undefined : visualAccessGrants[explanationEcgId]} temporaryVisualAccess={!canViewOriginalEcg} onClose={() => setExplanationEcgId("")} />}

      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", lg: "repeat(2, minmax(0, 1fr))" }, gap: 3 }}>
        <Box>
          <Paper component="form" onSubmit={reviewForm.handleSubmit((values) => submitReview.mutate(values))} sx={{ p: 3, height: "100%" }}>
            {canRecordClinicianReview ? (
              <Stack spacing={2}>
                <Box><Typography variant="h6">Clinician review and sign-off</Typography><Typography variant="body2" color="text.secondary">Record a qualified clinician’s assessment of the research result. The AI can supply only editable, research-only wording; it never recommends treatment.</Typography></Box>
                {submitReview.isError && <ApiErrorAlert error={submitReview.error} />}
                <Controller
                  name="analysis_id"
                  control={reviewForm.control}
                  render={({ field }) => (
                    <FormControl fullWidth error={Boolean(reviewForm.formState.errors.analysis_id)}>
                      <InputLabel id="review-analysis-label">Analysis</InputLabel>
                      <Select {...field} labelId="review-analysis-label" label="Analysis" onChange={(event) => field.onChange(event.target.value)}>
                        <MenuItem value=""><em>Choose an analysis</em></MenuItem>
                        {analysisItems.map((analysis) => <MenuItem key={analysis.id} value={analysis.id}>{analysisLabel(analysis.predicted_label ?? analysis.prediction_label)} · {formatDate(analysis.created_at)}</MenuItem>)}
                      </Select>
                    </FormControl>
                  )}
                />
                <Controller
                  name="review_status"
                  control={reviewForm.control}
                  render={({ field }) => (
                    <FormControl fullWidth error={Boolean(reviewForm.formState.errors.review_status)}>
                      <InputLabel id="review-status-label">Review status</InputLabel>
                      <Select {...field} labelId="review-status-label" label="Review status" onChange={(event) => field.onChange(event.target.value)}>
                        <MenuItem value="REVIEWED">Reviewed</MenuItem>
                        <MenuItem value="REQUIRES_FOLLOW_UP">Requires follow-up</MenuItem>
                        <MenuItem value="NOT_INTERPRETABLE">Not interpretable</MenuItem>
                      </Select>
                    </FormControl>
                  )}
                />
                <Alert severity="info" variant="outlined">The research draft describes only the selected RAMNV2 result and its uncalibrated score. It is not a diagnosis, triage, treatment, or prescription recommendation.</Alert>
                {insertResearchAssessment.isError && <ApiErrorAlert error={insertResearchAssessment.error} />}
                <Button
                  type="button"
                  variant="outlined"
                  startIcon={<AutoGraphOutlinedIcon />}
                  disabled={!selectedReviewAnalysis?.ecg_id || insertResearchAssessment.isPending}
                  onClick={() => selectedReviewAnalysis?.ecg_id && insertResearchAssessment.mutate(selectedReviewAnalysis.ecg_id)}
                >
                  {insertResearchAssessment.isPending ? "Preparing editable research wording…" : "Insert research-only assessment draft"}
                </Button>
                <TextField label="Clinician assessment (editable)" multiline minRows={4} required error={Boolean(reviewForm.formState.errors.clinician_notes)} helperText={reviewForm.formState.errors.clinician_notes?.message ?? "The clinician must review, revise, and approve this text before saving."} {...reviewForm.register("clinician_notes")} />
                <TextField label="Clinician-entered diagnosis (optional)" error={Boolean(reviewForm.formState.errors.diagnosis)} helperText={reviewForm.formState.errors.diagnosis?.message} {...reviewForm.register("diagnosis")} />
                <TextField label="Clinical note (optional)" multiline minRows={3} error={Boolean(reviewForm.formState.errors.clinical_note)} helperText={reviewForm.formState.errors.clinical_note?.message} {...reviewForm.register("clinical_note")} />
                <Box sx={{ borderTop: "1px solid #dce7e3", pt: 2 }}>
                  <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ xs: "flex-start", sm: "center" }} gap={1}>
                    <Box>
                      <Typography variant="subtitle2">Clinician-entered prescription record (optional)</Typography>
                      <Typography variant="caption" color="text.secondary">The local catalogue only helps find a medicine name. It never supplies a dose, route, timing, or treatment plan.</Typography>
                    </Box>
                    <Button type="button" size="small" variant="outlined" startIcon={<AddTaskOutlinedIcon />} onClick={() => prescriptionItems.append(emptyPrescriptionItem())} disabled={prescriptionItems.fields.length >= 20}>Add medicine</Button>
                  </Stack>
                  <Stack spacing={1.25} sx={{ mt: 1.25 }}>
                    {prescriptionItems.fields.map((item, index) => (
                      <Paper key={item.id} variant="outlined" sx={{ p: 1.75, bgcolor: "#fbfdfd" }}>
                        <Stack spacing={1.25}>
                          <Stack direction="row" alignItems="center" justifyContent="space-between" gap={1}>
                            <Typography variant="subtitle2">Medicine {index + 1}</Typography>
                            <IconButton type="button" size="small" aria-label={`Remove medicine ${index + 1}`} onClick={() => prescriptionItems.remove(index)}><DeleteOutlineOutlinedIcon fontSize="small" /></IconButton>
                          </Stack>
                          <Controller
                            name={`prescription_items.${index}.medicine`}
                            control={reviewForm.control}
                            render={({ field, fieldState }) => <MedicineAutocomplete value={field.value} onChange={field.onChange} error={Boolean(fieldState.error)} helperText={fieldState.error?.message} />}
                          />
                          <Stack direction={{ xs: "column", sm: "row" }} spacing={1.25}>
                            <TextField fullWidth label="Dose" {...reviewForm.register(`prescription_items.${index}.dose`)} />
                            <TextField fullWidth label="Route" placeholder="e.g. oral" {...reviewForm.register(`prescription_items.${index}.route`)} />
                            <Controller
                              name={`prescription_items.${index}.frequency`}
                              control={reviewForm.control}
                              render={({ field }) => (
                                <TextField select fullWidth label="Frequency" value={field.value ?? ""} onChange={field.onChange} helperText="Example schedule notation: 1-1-1">
                                  <MenuItem value=""><em>Choose or enter in instructions</em></MenuItem>
                                  {frequencyOptions.map((frequency) => <MenuItem key={frequency} value={frequency}>{frequency}</MenuItem>)}
                                </TextField>
                              )}
                            />
                          </Stack>
                          <Stack direction={{ xs: "column", sm: "row" }} spacing={1.25}>
                            <TextField fullWidth label="Duration" {...reviewForm.register(`prescription_items.${index}.duration`)} />
                            <Controller
                              name={`prescription_items.${index}.meal_timing`}
                              control={reviewForm.control}
                              render={({ field }) => (
                                <TextField select fullWidth label="Meal timing" value={field.value ?? ""} onChange={field.onChange}>
                                  <MenuItem value=""><em>Not recorded</em></MenuItem>
                                  {mealTimingOptions.map((timing) => <MenuItem key={timing} value={timing}>{timing}</MenuItem>)}
                                </TextField>
                              )}
                            />
                          </Stack>
                          <TextField label="Prescription instructions" multiline minRows={2} {...reviewForm.register(`prescription_items.${index}.instructions`)} />
                        </Stack>
                      </Paper>
                    ))}
                    {!prescriptionItems.fields.length && <Typography variant="body2" color="text.secondary">No medicine has been added. Use “Add medicine” to record one or more clinician-selected items.</Typography>}
                  </Stack>
                </Box>
                <Button type="submit" variant="contained" startIcon={<VerifiedUserOutlinedIcon />} disabled={submitReview.isPending}>{submitReview.isPending ? "Saving review…" : "Record clinician review"}</Button>
              </Stack>
            ) : (
              <Stack spacing={1.5}>
                <Typography variant="h6">Clinician review and sign-off</Typography>
                <Alert severity="info">Only a signed-in doctor or super administrator can record a clinician assessment or prescription. The research analysis remains available for authorized viewing.</Alert>
              </Stack>
            )}
          </Paper>
        </Box>
        <Box>
          <Paper sx={{ overflow: "hidden", height: "100%" }}>
            <Box sx={{ p: 3, borderBottom: "1px solid #dce7e3" }}><Typography variant="h6">Recorded reviews and reports</Typography><Typography variant="body2" color="text.secondary">Review status and report availability come from the server audit trail.</Typography></Box>
            {reviews.isError ? <ResourceError error={reviews.error} /> : reviews.isLoading ? <LoadingState label="Loading reviews…" /> : (
              <Stack divider={<Divider flexItem />}>
                {reviewItems.map((review) => <Box key={review.id} sx={{ p: 2.5 }}><Stack direction="row" justifyContent="space-between" gap={2}><Typography fontWeight={700}>{review.status ?? "Review recorded"}</Typography><Typography variant="caption" color="text.secondary">{formatDate(review.reviewed_at ?? review.created_at)}</Typography></Stack><Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>{review.clinician_notes ?? "No narrative review returned."}</Typography></Box>)}
                {!reviewItems.length && <Box sx={{ p: 2.5 }}><Typography color="text.secondary">No clinician reviews were returned.</Typography></Box>}
                {reports.isError ? <ResourceError error={reports.error} /> : reportItems.map((report) => <Box key={report.id} sx={{ p: 2.5, borderTop: "1px solid #dce7e3" }}><Stack direction="row" justifyContent="space-between" alignItems="center" gap={2}><Box><Typography fontWeight={700}>{report.title ?? "Clinical report"}</Typography><Typography variant="body2" color="text.secondary">{report.status ?? "available"} · {formatDate(report.created_at)}</Typography></Box>{canViewOriginalEcg ? (report.download_url ? <Button onClick={() => downloadReport.mutate(report)} disabled={downloadReport.isPending} size="small">{downloadReport.isPending ? "Preparing…" : "Download authorized PDF"}</Button> : <Button onClick={() => generateReport.mutate(report.id)} disabled={generateReport.isPending} size="small">{generateReport.isPending ? "Generating…" : "Generate PDF"}</Button>) : <Chip size="small" icon={<SecurityOutlinedIcon />} label="Waveform-bearing PDF locked" variant="outlined" />}</Stack></Box>)}
                {generateReport.isError && <ResourceError error={generateReport.error} />}
                {downloadReport.isError && <ResourceError error={downloadReport.error} />}
              </Stack>
            )}
          </Paper>
        </Box>
      </Box>
    </Stack>
  );
}
