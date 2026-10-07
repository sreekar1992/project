import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import VisibilityOffOutlinedIcon from "@mui/icons-material/VisibilityOffOutlined";
import { Alert, Box, Button, Chip, Paper, Stack, Typography } from "@mui/material";
import type { EcgSecurityStatus } from "../types/api";

interface ProtectedEcgPreviewProps {
  /** Identifier of the record that is locked; it is never used to fetch visual data. */
  ecgId: string;
  filename: string;
  security?: EcgSecurityStatus;
  securityIsLoading?: boolean;
  onClose: () => void;
}

/**
 * Renders only a bundled decorative mosaic. It does not request waveform
 * samples, source-image bytes, or a server-rendered ECG preview, so opening
 * this view cannot expose an ECG in the browser for a non-doctor role.
 */
export function ProtectedEcgPreview({
  filename,
  security,
  securityIsLoading = false,
  onClose,
}: ProtectedEcgPreviewProps) {
  const storageLabel = security?.storage.encrypted_at_rest
    ? `${security.storage.algorithm ?? "Server encryption"} reported`
    : security?.storage.legacy_unencrypted
      ? "Legacy storage reported"
      : security
        ? "Server encryption not confirmed"
        : securityIsLoading
          ? "Checking server protection…"
          : "Protection status unavailable";

  return (
    <Paper component="section" aria-labelledby="protected-ecg-title" sx={{ overflow: "hidden", borderColor: "rgba(9, 124, 137, 0.30)" }}>
      <Stack spacing={0}>
        <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "center" }} justifyContent="space-between" gap={1.5} sx={{ px: 2.5, py: 2, borderBottom: "1px solid", borderColor: "divider" }}>
          <Box>
            <Stack direction="row" spacing={1} alignItems="center">
              <LockOutlinedIcon color="primary" fontSize="small" />
              <Typography id="protected-ecg-title" variant="h6">Original ECG locked</Typography>
            </Stack>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.4 }}>
              {filename} is not rendered, downloaded, or supplied to this browser role.
            </Typography>
          </Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <Chip size="small" icon={<VisibilityOffOutlinedIcon />} label="Doctor visual access required" color="warning" variant="outlined" />
            <Button onClick={onClose} size="small">Hide</Button>
          </Stack>
        </Stack>

        <Box sx={{ position: "relative", minHeight: 280, overflow: "hidden", bgcolor: "#f7fbfc", px: { xs: 1.5, sm: 3 }, py: 2.5 }}>
          <Box
            aria-hidden="true"
            sx={{
              position: "absolute",
              inset: 0,
              backgroundImage: "linear-gradient(90deg, rgba(247, 251, 252, 0.26), rgba(234, 244, 247, 0.48)), url('/assets/protected-ecg-mosaic.png')",
              backgroundPosition: "center",
              backgroundRepeat: "no-repeat",
              backgroundSize: "cover",
              filter: "blur(0.8px) saturate(0.82)",
              opacity: 0.94,
              transform: "scale(1.018)",
            }}
          />
          <Box aria-hidden="true" sx={{ position: "absolute", inset: 0, bgcolor: "rgba(255, 255, 255, 0.26)" }} />

          <Stack alignItems="center" justifyContent="center" spacing={1.25} sx={{ position: "relative", minHeight: 230, textAlign: "center", px: 2 }}>
            <Box sx={{ display: "grid", placeItems: "center", width: 60, height: 60, borderRadius: "50%", bgcolor: "rgba(255,255,255,0.92)", color: "primary.main", border: "1px solid rgba(9,124,137,0.25)", boxShadow: "0 6px 24px rgba(25, 68, 85, 0.12)" }}>
              <LockOutlinedIcon />
            </Box>
            <Typography variant="h6" fontWeight={800}>Protected visual preview</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 680 }}>
              This is a static mosaic redaction, not the original ECG, ciphertext, a server-rendered ECG preview, or a diagnostic visualization.
            </Typography>
          </Stack>
        </Box>

        <Stack spacing={1.25} sx={{ px: 2.5, py: 2.25 }}>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }} justifyContent="space-between">
            <Typography variant="subtitle2">Protection status</Typography>
            <Chip size="small" color={security?.storage.encrypted_at_rest ? "success" : "default"} label={storageLabel} variant="outlined" />
          </Stack>
          <Alert severity="info" variant="outlined" sx={{ alignItems: "flex-start" }}>
            <Typography variant="body2">
              <strong>Defense in depth:</strong> server-side AES-256-GCM authenticated encryption protects the stored source when confirmed by the service. Adversarial camouflage remains research-only and is not used for clinical original-ECG access. This static redaction cannot be decrypted into the original ECG by this browser role. Its blur is only an access-control display safeguard; it is not encryption and does not replace server-side RBAC.
            </Typography>
          </Alert>
          <Typography variant="caption" color="text.secondary">
            The API must enforce doctor-only source access. This client deliberately makes no waveform, source-image, Grad-CAM, or source-file download request while this protected preview is shown.
          </Typography>
        </Stack>
      </Stack>
    </Paper>
  );
}
