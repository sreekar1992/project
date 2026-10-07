import KeyOutlinedIcon from "@mui/icons-material/KeyOutlined";
import LockClockOutlinedIcon from "@mui/icons-material/LockClockOutlined";
import PendingOutlinedIcon from "@mui/icons-material/PendingOutlined";
import VerifiedUserOutlinedIcon from "@mui/icons-material/VerifiedUserOutlined";
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  LinearProgress,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useEffect, useState } from "react";
import { ApiErrorAlert } from "./ApiErrorAlert";
import type { EcgVisualAccessApproval, EcgVisualAccessRequest, EcgVisualAccessStatus } from "../types/api";

function normalizedStatus(status?: string): string {
  return status?.trim().toUpperCase() ?? "";
}

export function visualAccessRequestId(value?: EcgVisualAccessStatus): string | undefined {
  const candidate = value?.request_id ?? value?.request_uuid ?? (value as EcgVisualAccessRequest | undefined)?.id;
  return candidate ? String(candidate) : undefined;
}

export function visualAccessEcgId(value?: EcgVisualAccessStatus): string | undefined {
  const candidate = value?.ecg_id ?? value?.ecg_uuid;
  return candidate ? String(candidate) : undefined;
}

export function visualAccessExpiry(value?: EcgVisualAccessStatus): string | undefined {
  return value?.access_expires_at ?? value?.passcode_expires_at ?? value?.expires_at;
}

export function formatVisualAccessDate(value?: string): string {
  if (!value) return "an expiry time supplied by the server";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

interface VisualAccessRequestPanelProps {
  status?: EcgVisualAccessStatus;
  isLoading?: boolean;
  statusError?: unknown;
  requestPending?: boolean;
  requestError?: unknown;
  unlockPending?: boolean;
  unlockError?: unknown;
  onRequest: () => void;
  onUnlock: (passcode: string) => void;
  onRefresh: () => void;
}

/**
 * Non-doctor controls for one short-lived, doctor-approved view-only grant.
 * The passcode is held only in this controlled input state and cleared after
 * each unlock request; nothing is written to browser storage.
 */
export function VisualAccessRequestPanel({
  status,
  isLoading = false,
  statusError,
  requestPending = false,
  requestError,
  unlockPending = false,
  unlockError,
  onRequest,
  onUnlock,
  onRefresh,
}: VisualAccessRequestPanelProps) {
  const [passcode, setPasscode] = useState("");
  const requestId = visualAccessRequestId(status);
  const state = normalizedStatus(status?.status);
  const expiration = visualAccessExpiry(status);
  const isPending = state === "PENDING";
  const isApproved = state === "APPROVED" || state === "PASSCODE_READY";
  const isUnlockedWithoutLocalCredential = state === "UNLOCKED";
  const isExpired = state === "EXPIRED" || state === "DENIED" || state === "REVOKED" || state === "LOCKED";
  const canRequest = !requestId || isExpired;

  useEffect(() => {
    setPasscode("");
  }, [requestId, state]);

  return (
    <Paper component="section" aria-labelledby="visual-access-request-title" sx={{ p: 2.5, borderColor: "rgba(9, 124, 137, 0.30)" }}>
      <Stack spacing={1.5}>
        <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "center" }} gap={1}>
          <Box>
            <Stack direction="row" spacing={1} alignItems="center">
              <LockClockOutlinedIcon color="primary" fontSize="small" />
              <Typography id="visual-access-request-title" variant="h6">Request temporary visual access</Typography>
            </Stack>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.4 }}>
              A doctor must approve this ECG-specific, view-only request before the original can be rendered.
            </Typography>
          </Box>
          {isPending && <Chip size="small" color="warning" icon={<PendingOutlinedIcon />} label="Doctor approval pending" variant="outlined" />}
          {isApproved && <Chip size="small" color="success" icon={<KeyOutlinedIcon />} label="Passcode required" variant="outlined" />}
          {isUnlockedWithoutLocalCredential && <Chip size="small" color="warning" icon={<LockClockOutlinedIcon />} label="Browser credential unavailable" variant="outlined" />}
          {isExpired && <Chip size="small" color="warning" label="Previous request expired" variant="outlined" />}
        </Stack>

        {isLoading && <LinearProgress />}
        {statusError ? <ApiErrorAlert error={statusError} /> : null}
        {requestError ? <ApiErrorAlert error={requestError} /> : null}
        {unlockError ? <ApiErrorAlert error={unlockError} /> : null}

        {canRequest && (
          <Alert severity="info" variant="outlined">
            <Typography variant="body2">The request records your identity and the selected ECG for a doctor to review. It does not disclose source pixels or waveform values.</Typography>
          </Alert>
        )}

        {isPending && (
          <Alert severity="info" variant="outlined">
            <Typography variant="body2">A doctor has not yet approved this request. Refresh the status after the doctor confirms it. No original ECG data is available while pending.</Typography>
          </Alert>
        )}

        {isApproved && requestId && (
          <Stack spacing={1.25} component="form" onSubmit={(event) => { event.preventDefault(); const submittedPasscode = passcode; setPasscode(""); onUnlock(submittedPasscode); }}>
            <Alert severity="warning" variant="outlined">
              <Typography variant="body2">Enter the one-time passcode provided by the approving doctor. The resulting visual session expires at {formatVisualAccessDate(expiration)} and permits viewing only—never download or PDF export.</Typography>
            </Alert>
            <TextField
              label="Doctor-generated passcode"
              type="password"
              autoComplete="one-time-code"
              value={passcode}
              onChange={(event) => setPasscode(event.target.value)}
              helperText="This passcode stays only in this page’s memory and is cleared after submission."
              required
            />
            <Button type="submit" variant="contained" startIcon={<KeyOutlinedIcon />} disabled={!passcode.trim() || unlockPending}>
              {unlockPending ? "Verifying passcode…" : "Unlock temporary visual view"}
            </Button>
          </Stack>
        )}

        {isUnlockedWithoutLocalCredential && (
          <Alert severity="warning" variant="outlined">
            <Typography variant="body2">A temporary view-only session exists, but this browser does not hold its memory-only credential. It cannot be restored after refresh or sign-out. Wait for expiry and make a new request if access is still required.</Typography>
          </Alert>
        )}

        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          {canRequest && <Button variant="contained" startIcon={<VerifiedUserOutlinedIcon />} disabled={requestPending} onClick={onRequest}>{requestPending ? "Requesting…" : "Request doctor access"}</Button>}
          <Button variant="text" size="small" onClick={onRefresh} disabled={isLoading}>Refresh access status</Button>
        </Stack>
        <Typography variant="caption" color="text.secondary">The server remains authoritative: this screen cannot bypass role checks, decrypt storage, or extend an expired grant.</Typography>
      </Stack>
    </Paper>
  );
}

interface DoctorVisualAccessInboxProps {
  requests: EcgVisualAccessRequest[];
  isLoading?: boolean;
  error?: unknown;
  approvingRequestId?: string;
  approval?: EcgVisualAccessApproval;
  approvalError?: unknown;
  onApprove: (requestId: string) => void;
  onDismissApproval: () => void;
}

/** Doctor-only inbox for approving a patient-page ECG visual-access request. */
export function DoctorVisualAccessInbox({
  requests,
  isLoading = false,
  error,
  approvingRequestId,
  approval,
  approvalError,
  onApprove,
  onDismissApproval,
}: DoctorVisualAccessInboxProps) {
  return (
    <Paper component="section" aria-labelledby="doctor-visual-access-title" sx={{ overflow: "hidden", borderColor: "rgba(9, 124, 137, 0.30)" }}>
      <Box sx={{ p: 2.5, borderBottom: "1px solid", borderColor: "divider" }}>
        <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "center" }} gap={1}>
          <Box>
            <Typography id="doctor-visual-access-title" variant="h6">Doctor visual-access approvals</Typography>
            <Typography variant="body2" color="text.secondary">Approve only after confirming the requester and the selected ECG. Approval creates a short-lived, view-only passcode.</Typography>
          </Box>
          <Chip size="small" color="primary" icon={<VerifiedUserOutlinedIcon />} label="Doctor-only workflow" variant="outlined" />
        </Stack>
      </Box>
      <Stack spacing={0} divider={<Box sx={{ borderTop: "1px solid", borderColor: "divider" }} />}>
        {isLoading && <Box sx={{ px: 2.5, py: 1.5 }}><LinearProgress /></Box>}
        {error ? <Box sx={{ p: 2.5 }}><ApiErrorAlert error={error} /></Box> : null}
        {!isLoading && !error && !requests.length && <Box sx={{ p: 2.5 }}><Typography variant="body2" color="text.secondary">No pending visual-access requests for this patient’s ECG recordings.</Typography></Box>}
        {requests.map((request) => {
          const requestId = visualAccessRequestId(request);
          const requestedBy = request.requester?.display_name ?? request.requester_name ?? request.requester_email ?? "Authorized requester";
          const recording = request.filename ?? request.source_filename ?? `ECG ${visualAccessEcgId(request) ?? "recording"}`;
          return (
            <Box key={requestId ?? `${recording}-${request.requested_at ?? "pending"}`} sx={{ p: 2.5 }}>
              <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "center" }} gap={1.5}>
                <Box>
                  <Typography fontWeight={800}>{requestedBy}</Typography>
                  <Typography variant="body2" color="text.secondary">{recording} · requested {formatVisualAccessDate(request.requested_at)}</Typography>
                  {request.requester_role && <Typography variant="caption" color="text.secondary">Role: {request.requester_role}</Typography>}
                </Box>
                <Button variant="contained" startIcon={<KeyOutlinedIcon />} disabled={!requestId || approvingRequestId === requestId} onClick={() => requestId && onApprove(requestId)}>
                  {approvingRequestId === requestId ? "Approving…" : "Approve & generate passcode"}
                </Button>
              </Stack>
            </Box>
          );
        })}
      </Stack>

      {approvalError ? <Box sx={{ p: 2.5 }}><ApiErrorAlert error={approvalError} /></Box> : null}
      <Dialog open={Boolean(approval?.passcode)} onClose={onDismissApproval} maxWidth="xs" fullWidth>
        <DialogTitle>Share one-time visual-access passcode</DialogTitle>
        <DialogContent dividers>
          <Stack spacing={1.5} sx={{ pt: 0.5 }}>
            <Alert severity="warning"><Typography variant="body2">Share this passcode only through a verified channel with the requester. It expires at {formatVisualAccessDate(visualAccessExpiry(approval))}. Closing this dialog clears it from the browser.</Typography></Alert>
            <Box sx={{ px: 2, py: 1.5, borderRadius: 2, bgcolor: "rgba(9, 124, 137, 0.08)", border: "1px dashed rgba(9, 124, 137, 0.42)", textAlign: "center" }}>
              <Typography variant="overline" color="primary.main">One-time passcode</Typography>
              <Typography variant="h4" sx={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace", letterSpacing: 3 }}>{approval?.passcode}</Typography>
            </Box>
            <Typography variant="caption" color="text.secondary">The passcode is shown only in this temporary dialog. It is not copied to browser storage or application logs.</Typography>
          </Stack>
        </DialogContent>
        <DialogActions><Button onClick={onDismissApproval} variant="contained">I have shared it securely</Button></DialogActions>
      </Dialog>
    </Paper>
  );
}
