import AddTaskOutlinedIcon from "@mui/icons-material/AddTaskOutlined";
import ArrowBackOutlinedIcon from "@mui/icons-material/ArrowBackOutlined";
import CloudUploadOutlinedIcon from "@mui/icons-material/CloudUploadOutlined";
import PlayCircleOutlineOutlinedIcon from "@mui/icons-material/PlayCircleOutlineOutlined";
import VerifiedUserOutlinedIcon from "@mui/icons-material/VerifiedUserOutlined";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  Alert,
  Box,
  Button,
  Chip,
  Divider,
  FormControl,
  InputLabel,
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
import { Controller, useForm } from "react-hook-form";
import { Link, useParams } from "react-router-dom";
import { z } from "zod";
import { ApiErrorAlert } from "../components/ApiErrorAlert";
import { LoadingState } from "../components/LoadingState";
import { EcgWaveformViewer } from "../components/EcgWaveformViewer";
import { api } from "../lib/api";
import { listFromResponse, patientDisplayName } from "../types/api";

const encounterSchema = z.object({
  encounter_type: z.string().trim().min(1, "Choose an encounter type."),
  reason: z.string().trim().max(500).optional(),
});
type EncounterValues = z.infer<typeof encounterSchema>;

const reviewSchema = z.object({
  analysis_id: z.string().min(1, "Choose an analysis to review."),
  review_status: z.enum(["REVIEWED", "REQUIRES_FOLLOW_UP", "NOT_INTERPRETABLE"]),
  clinician_notes: z.string().trim().min(1, "Document the clinician assessment.").max(4000),
  diagnosis: z.string().trim().max(300).optional(),
  clinical_note: z.string().trim().max(8000).optional(),
  prescription_medicine: z.string().trim().max(300).optional(),
  prescription_dose: z.string().trim().max(100).optional(),
  prescription_route: z.string().trim().max(100).optional(),
  prescription_frequency: z.string().trim().max(100).optional(),
  prescription_duration: z.string().trim().max(100).optional(),
  prescription_instructions: z.string().trim().max(2000).optional(),
});
type ReviewValues = z.infer<typeof reviewSchema>;

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

function GradCamViewer({ ecgId, onClose }: { ecgId: string; onClose: () => void }) {
  const image = useQuery({ queryKey: ["gradcam", ecgId], queryFn: () => api.analyses.explanation(ecgId), staleTime: 5 * 60_000 });
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
          <Box><Typography variant="h6">Model-generated Grad-CAM</Typography><Typography variant="body2" color="text.secondary">Relevance visualization from the saved research analysis; it is not a diagnosis.</Typography></Box>
          <Button onClick={onClose} size="small">Hide</Button>
        </Stack>
        {image.isLoading && <LoadingState label="Loading authorized explanation…" />}
        {image.isError && <ApiErrorAlert error={image.error} />}
        {imageUrl && <Box component="img" src={imageUrl} alt="Authorized ECG Grad-CAM relevance visualization" sx={{ display: "block", width: "100%", maxWidth: 1120, border: "1px solid #dce7e3", borderRadius: 1.5 }} />}
      </Stack>
    </Paper>
  );
}

export function PatientDetailPage() {
  const { patientId = "" } = useParams();
  const queryClient = useQueryClient();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadEncounter, setUploadEncounter] = useState("");
  const [analysisEcg, setAnalysisEcg] = useState("");
  const [viewerEcgId, setViewerEcgId] = useState("");
  const [explanationEcgId, setExplanationEcgId] = useState("");
  const patient = useQuery({ queryKey: ["patient", patientId], queryFn: () => api.patients.get(patientId), enabled: Boolean(patientId) });
  const encounters = useQuery({ queryKey: ["encounters", patientId], queryFn: () => api.encounters.list(patientId), enabled: Boolean(patientId) });
  const ecgs = useQuery({ queryKey: ["ecgs", patientId], queryFn: () => api.ecgs.list(patientId), enabled: Boolean(patientId) });
  const analyses = useQuery({ queryKey: ["analyses", patientId], queryFn: () => api.analyses.list(patientId), enabled: Boolean(patientId) });
  const reviews = useQuery({ queryKey: ["reviews", patientId], queryFn: () => api.reviews.list(patientId), enabled: Boolean(patientId) });
  const reports = useQuery({ queryKey: ["reports", patientId], queryFn: () => api.reports.list(patientId), enabled: Boolean(patientId) });
  const waveform = useQuery({ queryKey: ["waveform", viewerEcgId], queryFn: () => api.ecgs.waveform(viewerEcgId), enabled: Boolean(viewerEcgId), staleTime: 5 * 60_000 });
  const encounterForm = useForm<EncounterValues>({ resolver: zodResolver(encounterSchema), defaultValues: { encounter_type: "", reason: "" } });
  const reviewForm = useForm<ReviewValues>({ resolver: zodResolver(reviewSchema), defaultValues: {
    analysis_id: "", review_status: "REVIEWED", clinician_notes: "", diagnosis: "", clinical_note: "",
    prescription_medicine: "", prescription_dose: "", prescription_route: "", prescription_frequency: "",
    prescription_duration: "", prescription_instructions: "",
  } });

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
      setAnalysisEcg(record.id);
      setViewerEcgId(record.id);
      setSelectedFile(null);
      setUploadEncounter("");
    },
  });
  const requestAnalysis = useMutation({
    mutationFn: () => api.analyses.create({ ecg_id: analysisEcg }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["analyses", patientId] }),
  });
  const submitReview = useMutation({
    mutationFn: (values: ReviewValues) => api.reviews.create({
      analysis_id: values.analysis_id,
      doctor_assessment: values.clinician_notes,
      review_status: values.review_status,
      diagnosis: values.diagnosis ? { display: values.diagnosis } : undefined,
      clinical_note: values.clinical_note || undefined,
      prescription_items: values.prescription_medicine ? [{
        medicine: values.prescription_medicine, dose: values.prescription_dose || undefined,
        route: values.prescription_route || undefined, frequency: values.prescription_frequency || undefined,
        duration: values.prescription_duration || undefined, instructions: values.prescription_instructions || undefined,
      }] : [],
      prescription_instructions: values.prescription_instructions || undefined,
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
  const reviewItems = listFromResponse(reviews.data ?? []);
  const reportItems = listFromResponse(reports.data ?? []);
  const viewedEcg = ecgItems.find((ecg) => ecg.id === viewerEcgId);

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
              <Box><Typography variant="h6">Upload ECG recording</Typography><Typography variant="body2" color="text.secondary">The server validates file type, stores it securely, and records an audit event.</Typography></Box>
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
                <input hidden type="file" accept=".mat,.csv" onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)} />
              </Button>
              <Button variant="contained" onClick={() => uploadEcg.mutate()} disabled={!selectedFile || !uploadEncounter || uploadEcg.isPending}>{uploadEcg.isPending ? "Uploading…" : "Upload recording"}</Button>
            </Stack>
          </Paper>
        </Box>
      </Box>

      <Paper sx={{ overflow: "hidden" }}>
        <Box sx={{ p: 3, borderBottom: "1px solid #dce7e3" }}><Typography variant="h6">ECG recordings</Typography><Typography variant="body2" color="text.secondary">Recordings are returned from secured object storage through backend-authorized URLs.</Typography></Box>
        {ecgs.isError ? <ResourceError error={ecgs.error} /> : ecgs.isLoading ? <LoadingState label="Loading recordings…" /> : (
          <Stack divider={<Divider flexItem />}>
            {ecgItems.map((ecg) => (
              <Box key={ecg.id} sx={{ p: 2.5, display: "flex", gap: 2, alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
                <Box><Typography fontWeight={700}>{ecg.filename ?? ecg.source_filename ?? `ECG ${ecg.id}`}</Typography><Typography variant="body2" color="text.secondary">Uploaded/acquired: {formatDate(ecg.acquired_at ?? ecg.created_at)} · Status: {ecg.status ?? "unknown"}</Typography></Box>
                <Stack direction="row" spacing={1}>
                  <Button onClick={() => setViewerEcgId(ecg.id)} size="small">View waveform</Button>
                  <Button onClick={() => downloadEcg.mutate(ecg)} disabled={downloadEcg.isPending} size="small">
                    {downloadEcg.isPending ? "Preparing download…" : "Download source file"}
                  </Button>
                </Stack>
              </Box>
            ))}
            {!ecgItems.length && <Box sx={{ p: 3 }}><Typography color="text.secondary">No ECG recordings were returned.</Typography></Box>}
          </Stack>
        )}
      </Paper>

      {viewerEcgId && (
        <Paper sx={{ p: 2.5 }}>
          <Stack spacing={1.5}>
            <Stack direction="row" alignItems="center" justifyContent="space-between" gap={2}>
              <Box><Typography variant="h6">Authorized ECG viewer</Typography><Typography variant="body2" color="text.secondary">Single-lead source waveform from {viewedEcg?.filename ?? viewedEcg?.source_filename ?? "the selected recording"}. Zoom and pan happen only in this browser session.</Typography></Box>
              <Button onClick={() => setViewerEcgId("")} size="small">Hide</Button>
            </Stack>
            {waveform.isLoading && <LoadingState label="Loading authorized waveform…" />}
            {waveform.isError && <ApiErrorAlert error={waveform.error} />}
            {waveform.data && <EcgWaveformViewer samples={waveform.data.samples} samplingRateHz={waveform.data.sampling_rate_hz} title="Authorized single-lead ECG waveform" />}
          </Stack>
        </Paper>
      )}

      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", lg: "minmax(0, 5fr) minmax(0, 7fr)" }, gap: 3 }}>
        <Box>
          <Paper sx={{ p: 3, height: "100%" }}>
            <Stack spacing={2}>
              <Box><Typography variant="h6">Request research analysis</Typography><Typography variant="body2" color="text.secondary">This requests an AI analysis; it does not create a diagnosis or treatment plan.</Typography></Box>
              {requestAnalysis.isError && <ApiErrorAlert error={requestAnalysis.error} />}
              <FormControl fullWidth>
                <InputLabel id="analysis-ecg-label">ECG recording</InputLabel>
                <Select labelId="analysis-ecg-label" label="ECG recording" value={analysisEcg} onChange={(event) => setAnalysisEcg(event.target.value)}>
                  <MenuItem value=""><em>Choose a recording</em></MenuItem>
                  {ecgItems.map((ecg) => <MenuItem key={ecg.id} value={ecg.id}>{ecg.filename ?? ecg.source_filename ?? ecg.id}</MenuItem>)}
                </Select>
              </FormControl>
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
                  return (
                    <Box key={analysis.id} sx={{ p: 2.5 }}>
                      <Stack direction="row" justifyContent="space-between" gap={2} alignItems="flex-start">
                        <Box><Typography fontWeight={700}>{analysisLabel(analysis.predicted_label ?? analysis.prediction_label)}</Typography><Typography variant="body2" color="text.secondary">Model: {analysis.model_version ?? "not returned"} · {formatDate(analysis.created_at)}</Typography></Box>
                        <Chip size="small" label={analysis.status ?? "pending clinician review"} color="warning" variant="outlined" />
                      </Stack>
                      {confidence !== undefined && <Box sx={{ mt: 1.5 }}><Stack direction="row" justifyContent="space-between"><Typography variant="caption">Uncalibrated model score</Typography><Typography variant="caption">{confidence}%</Typography></Stack><LinearProgress variant="determinate" value={confidence} sx={{ mt: 0.5, height: 7, borderRadius: 10 }} /></Box>}
                      <Stack direction="row" spacing={1} sx={{ mt: 1.5 }}>
                        {analysis.ecg_id && <Button size="small" onClick={() => setViewerEcgId(analysis.ecg_id!)}>View waveform</Button>}
                        {analysis.ecg_id && analysis.has_explanation && <Button size="small" onClick={() => setExplanationEcgId(analysis.ecg_id!)}>View Grad-CAM</Button>}
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

      {explanationEcgId && <GradCamViewer ecgId={explanationEcgId} onClose={() => setExplanationEcgId("")} />}

      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", lg: "repeat(2, minmax(0, 1fr))" }, gap: 3 }}>
        <Box>
          <Paper component="form" onSubmit={reviewForm.handleSubmit((values) => submitReview.mutate(values))} sx={{ p: 3, height: "100%" }}>
            <Stack spacing={2}>
              <Box><Typography variant="h6">Clinician review and sign-off</Typography><Typography variant="body2" color="text.secondary">Record a qualified clinician’s assessment of the research result. This form intentionally offers no automatic treatment recommendation.</Typography></Box>
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
              <TextField label="Clinician assessment" multiline minRows={4} required error={Boolean(reviewForm.formState.errors.clinician_notes)} helperText={reviewForm.formState.errors.clinician_notes?.message} {...reviewForm.register("clinician_notes")} />
              <TextField label="Clinician-entered diagnosis (optional)" error={Boolean(reviewForm.formState.errors.diagnosis)} helperText={reviewForm.formState.errors.diagnosis?.message} {...reviewForm.register("diagnosis")} />
              <TextField label="Clinical note (optional)" multiline minRows={3} error={Boolean(reviewForm.formState.errors.clinical_note)} helperText={reviewForm.formState.errors.clinical_note?.message} {...reviewForm.register("clinical_note")} />
              <Box sx={{ borderTop: "1px solid #dce7e3", pt: 2 }}>
                <Typography variant="subtitle2">Clinician-entered prescription record (optional)</Typography>
                <Typography variant="caption" color="text.secondary">This is never generated or recommended by the AI model.</Typography>
                <Stack spacing={1.25} sx={{ mt: 1.25 }}>
                  <TextField label="Medicine" error={Boolean(reviewForm.formState.errors.prescription_medicine)} helperText={reviewForm.formState.errors.prescription_medicine?.message} {...reviewForm.register("prescription_medicine")} />
                  <Stack direction={{ xs: "column", sm: "row" }} spacing={1.25}>
                    <TextField fullWidth label="Dose" {...reviewForm.register("prescription_dose")} />
                    <TextField fullWidth label="Route" {...reviewForm.register("prescription_route")} />
                    <TextField fullWidth label="Frequency" {...reviewForm.register("prescription_frequency")} />
                  </Stack>
                  <TextField label="Duration" {...reviewForm.register("prescription_duration")} />
                  <TextField label="Prescription instructions" multiline minRows={2} {...reviewForm.register("prescription_instructions")} />
                </Stack>
              </Box>
              <Button type="submit" variant="contained" startIcon={<VerifiedUserOutlinedIcon />} disabled={submitReview.isPending}>{submitReview.isPending ? "Saving review…" : "Record clinician review"}</Button>
            </Stack>
          </Paper>
        </Box>
        <Box>
          <Paper sx={{ overflow: "hidden", height: "100%" }}>
            <Box sx={{ p: 3, borderBottom: "1px solid #dce7e3" }}><Typography variant="h6">Recorded reviews and reports</Typography><Typography variant="body2" color="text.secondary">Review status and report availability come from the server audit trail.</Typography></Box>
            {reviews.isError ? <ResourceError error={reviews.error} /> : reviews.isLoading ? <LoadingState label="Loading reviews…" /> : (
              <Stack divider={<Divider flexItem />}>
                {reviewItems.map((review) => <Box key={review.id} sx={{ p: 2.5 }}><Stack direction="row" justifyContent="space-between" gap={2}><Typography fontWeight={700}>{review.status ?? "Review recorded"}</Typography><Typography variant="caption" color="text.secondary">{formatDate(review.reviewed_at ?? review.created_at)}</Typography></Stack><Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>{review.clinician_notes ?? "No narrative review returned."}</Typography></Box>)}
                {!reviewItems.length && <Box sx={{ p: 2.5 }}><Typography color="text.secondary">No clinician reviews were returned.</Typography></Box>}
                {reports.isError ? <ResourceError error={reports.error} /> : reportItems.map((report) => <Box key={report.id} sx={{ p: 2.5, borderTop: "1px solid #dce7e3" }}><Stack direction="row" justifyContent="space-between" alignItems="center" gap={2}><Box><Typography fontWeight={700}>{report.title ?? "Clinical report"}</Typography><Typography variant="body2" color="text.secondary">{report.status ?? "available"} · {formatDate(report.created_at)}</Typography></Box>{report.download_url ? <Button onClick={() => downloadReport.mutate(report)} disabled={downloadReport.isPending} size="small">{downloadReport.isPending ? "Preparing…" : "Download authorized PDF"}</Button> : <Button onClick={() => generateReport.mutate(report.id)} disabled={generateReport.isPending} size="small">{generateReport.isPending ? "Generating…" : "Generate PDF"}</Button>}</Stack></Box>)}
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
